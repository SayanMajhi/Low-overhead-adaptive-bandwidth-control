"""Run the predeclared live transport calibration or one isolated Java UDP trial."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import statistics
import subprocess
import sys
import time

LEFT = "10.239.71.1"
RIGHT = "10.239.71.2"
IFACES = ("media0", "report0")
PORTS = (52000, 52001)
PACKETS = Path(__file__).with_name("packets.py")
CAL_PROBES = (
    ("clean-forward", 0, 900, 3000, 540, 3.5),
    ("cap-forward", 0, 900, 3000, 540, 3.5),
    ("clean-reverse", 1, 70, 10000, 72, 1.4),
    ("delay-reverse", 1, 70, 10000, 72, 1.4),
    ("loss-forward", 0, 200, 4000, 540, 1.4),
)
CASES = ("clean", "cap", "step", "reverse-delay")


def json_file(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def rows(path):
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


class Network:
    def __init__(self, out):
        self.out = out
        self.holder = subprocess.Popen(["unshare", "-n", "sleep", "3600"])
        self.processes = [self.holder]

    def stop(self):
        for process in self.processes[::-1]:
            if process.poll() is None:
                process.terminate()
        for process in self.processes[::-1]:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def command(self, side, *args):
        prefix = ["nsenter", "-t", str(self.holder.pid), "-n"] if side else []
        command = [*prefix, *map(str, args)]
        with (self.out / "commands.log").open("a") as file:
            file.write(shlex.join(command) + "\n")
        return command

    def run(self, side, *args):
        return subprocess.check_output(self.command(side, *args), stderr=subprocess.STDOUT, text=True)

    def start(self, side, args, stdout_path, ready=True):
        process = subprocess.Popen(self.command(side, *args), stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        self.processes.append(process)
        line = ""
        timestamp = None
        if ready:
            if select.select([process.stdout], [], [], 5)[0]:
                line = process.stdout.readline()
                timestamp = time.monotonic_ns()
            if not line.startswith("READY"):
                process.kill()
                stdout, stderr = process.communicate()
                stdout_path.write_text(line + stdout + stderr)
                raise RuntimeError(f"process did not become ready, see {stdout_path}")
        return process, stdout_path, line, timestamp

    def finish(self, child, timeout=55):
        process, path, line, _ = child
        output, stderr = process.communicate(timeout=timeout)
        path.write_text(line + output)
        path.with_suffix(path.suffix + ".stderr").write_text(stderr)
        if process.returncode:
            raise RuntimeError(f"exit {process.returncode}, see {path}")

    def setup(self):
        before = time.monotonic() + 4
        here = Path("/proc/self/ns/net").stat().st_ino
        original = int(os.environ["CN_ORIGINAL_NETNS"])
        if here == original:
            raise RuntimeError("start this runner with run.sh to isolate network changes")
        holder_ns = Path(f"/proc/{self.holder.pid}/ns/net")
        while holder_ns.stat().st_ino == here:
            if time.monotonic() >= before or self.holder.poll() is not None:
                raise RuntimeError("private peer namespace failed to start")
            time.sleep(0.01)
        self.run(0, "ip", "link", "add", IFACES[0], "type", "veth", "peer", "name", IFACES[1])
        self.run(0, "ip", "link", "set", IFACES[1], "netns", self.holder.pid)
        for side, address in enumerate((LEFT, RIGHT)):
            self.run(side, "ip", "addr", "add", address + "/30", "dev", IFACES[side])
            self.run(side, "ip", "link", "set", IFACES[side], "up")
        self.run(0, "ping", "-c", "1", "-W", "1", RIGHT)
        for side, peer in enumerate((RIGHT, LEFT)):
            data = {
                "network_namespace_inode": Path(f"/proc/{self.holder.pid}/ns/net").stat().st_ino if side else here,
                "links": json.loads(self.run(side, "ip", "-j", "address", "show")),
                "peer_route": json.loads(self.run(side, "ip", "-j", "route", "get", peer)),
            }
            json_file(self.out / f"route-{side}.json", data)

    def clear(self, side):
        self.run(side, "tc", "qdisc", "del", "dev", IFACES[side], "root")

    def tbf(self, action, rate):
        self.run(0, "tc", "qdisc", action, "dev", IFACES[0], "root", "handle", "10:",
                 "tbf", "rate", f"{rate}kbit", "burst", 1600, "limit", 16000)

    def netem(self, side, *settings):
        self.run(side, "tc", "qdisc", "add", "dev", IFACES[side], "root", "handle", "20:",
                 "netem", *settings)

    def counters(self, dest, label):
        raw = {}
        for side in range(2):
            raw[IFACES[side]] = json.loads(self.run(side, "tc", "-s", "-j", "qdisc", "show", "dev", IFACES[side]))
        json_file(dest / f"qdisc-{label}.json", raw)


def probe(net, name, side, count, gap, payload, duration):
    out = net.out / name
    out.mkdir()
    addresses = (LEFT, RIGHT)
    net.counters(out, "before")
    peer = net.start(1 - side, [sys.executable, PACKETS, "probe-receive", addresses[1 - side], 52100,
                               out / "packets.csv", duration], out / "receive.log")
    net.run(side, sys.executable, PACKETS, "probe-send", addresses[side], addresses[1 - side],
            52100, count, gap, payload, out / "offered.json")
    net.finish(peer)
    net.counters(out, "after")
    received = rows(out / "packets.csv")
    first = int(received[0]["arrival_ns"])
    last = int(received[-1]["arrival_ns"])
    from_ns, until_ns = first + 500_000_000, last - 500_000_000
    steady_bytes = sum(int(row["ip_bytes"]) for row in received if from_ns <= int(row["arrival_ns"]) < until_ns)
    return {
        "offered_packets": count, "received_packets": len(received),
        "loss_fraction": (count - len(received)) / count,
        "median_one_way_ms": statistics.median((int(row["arrival_ns"]) - int(row["send_ns"])) / 1e6
                                               for row in received),
        "steady_ip_bps": steady_bytes * 8e9 / (until_ns - from_ns) if until_ns > from_ns else None,
    }


def calibration(net):
    result = {}
    for args in CAL_PROBES:
        name = args[0]
        if name == "cap-forward":
            net.tbf("add", 950)
        elif name == "delay-reverse":
            net.netem(1, "delay", "100ms")
        elif name == "loss-forward":
            net.netem(0, "loss", "20%", "seed", "117")
        result[name] = probe(net, *args)
        if name in {"cap-forward", "loss-forward"}:
            net.clear(0)
        elif name == "delay-reverse":
            net.clear(1)
    json_file(net.out / "verification.json", result)
    clean_rate = result["clean-forward"]["steady_ip_bps"]
    cap_rate = result["cap-forward"]["steady_ip_bps"]
    delay = result["delay-reverse"]["median_one_way_ms"] - result["clean-reverse"]["median_one_way_ms"]
    if not (clean_rate > 1_250_000 and 850_000 < cap_rate < 1_050_000
            and 85 < delay < 130 and 0.08 < result["loss-forward"]["loss_fraction"] < 0.35):
        raise RuntimeError("independent impairment calibration outside expected bounds")


def trial(net, args):
    if args.case in {"cap", "step"}:
        net.tbf("add", 950 if args.case == "cap" else 1800)
    elif args.case == "reverse-delay":
        net.netem(1, "delay", "0ms")
    net.counters(net.out, "before")
    capture_duration = 44
    captures = [net.start(side, [sys.executable, PACKETS, "capture", iface,
                                  net.out / f"capture-{side}.csv", capture_duration],
                          net.out / f"capture-{side}.log") for side, iface in enumerate(IFACES)]
    endpoints = {}
    for side, mode, duration in ((1, "receive", 42750), (0, "send", 40000)):
        addresses = (LEFT, RIGHT)
        java = [args.java, "-jar", args.jar, mode, "--bind", f"{addresses[side]}:{PORTS[side]}",
                "--peer", f"{addresses[1 - side]}:{PORTS[1 - side]}", "--session", "711",
                "--run-ms", duration, "--profile", args.profile, "--arm", args.arm,
                "--events", net.out / f"{mode}.csv"]
        endpoints[mode] = net.start(side, java, net.out / f"{mode}.log")
    sender_ready = endpoints["send"][3]
    changes = []
    schedule = [(20, 950), (30, 1800)] if args.case == "step" else [(20, 100), (30, 0)] if args.case == "reverse-delay" else []
    for second, setting in schedule:
        delay = (sender_ready - time.monotonic_ns()) / 1e9 + second
        if delay > 0:
            time.sleep(delay)
        begin = time.monotonic_ns()
        if args.case == "step":
            net.tbf("change", setting)
        else:
            net.run(1, "tc", "qdisc", "change", "dev", IFACES[1], "root", "handle", "20:",
                    "netem", "delay", f"{setting}ms")
        end = time.monotonic_ns()
        changes.append({"scheduled_sender_second": second, "setting": setting, "command_started_ns": begin,
                        "command_finished_ns": end, "sender_ready_ns": sender_ready})
        net.counters(net.out, f"change-{second}")
    for process in [*endpoints.values(), *captures]:
        net.finish(process)
    net.counters(net.out, "after")
    json_file(net.out / "trial.json", {
        "case": args.case, "arm": args.arm, "sender_ready_ns": sender_ready,
        "receiver_ready_ns": endpoints["receive"][3], "changes": changes,
        "java": subprocess.check_output([args.java, "-version"], stderr=subprocess.STDOUT, text=True),
        "jar_sha256": hashlib.sha256(args.jar.read_bytes()).hexdigest(),
        "profile_sha256": hashlib.sha256(args.profile.read_bytes()).hexdigest(),
        "kernel": subprocess.check_output(["uname", "-sr"], text=True).strip(),
        "tc_version": subprocess.check_output(["tc", "-V"], text=True).strip(),
    })
    summarize(net.out)


def summarize(out):
    sends = [r for r in rows(out / "send.csv") if r["event"] == "media_sent"]
    ticks = [r for r in rows(out / "send.csv") if r["event"] == "rate_tick"]
    a, b = (rows(out / f"capture-{side}.csv") for side in range(2))
    def verify_tuples(records):
        return {(r["src"], int(r["src_port"]), r["dst"], int(r["dst_port"]),
                 int(r["type"]), int(r["ip_bytes"])) for r in records}
    expected = {(LEFT, PORTS[0], RIGHT, PORTS[1], 1, 568), (RIGHT, PORTS[1], LEFT, PORTS[0], 2, 100)}
    if verify_tuples(a) != expected or verify_tuples(b) != expected:
        raise RuntimeError("expected IPv4 five-tuples not observed in both namespaces")
    a_media = {int(r["id"]) for r in a if r["type"] == "1"}
    b_media = {int(r["id"]) for r in b if r["type"] == "1"}
    if not b_media or not b_media <= a_media:
        raise RuntimeError("media path capture is inconsistent")
    sender_reports = [r for r in rows(out / "send.csv") if r["event"] == "feedback_received" and r["corrected_rtt_ns"]]
    sender_ready = json.loads((out / "trial.json").read_text())["sender_ready_ns"]
    rtts = {}
    for start, stop in ((10, 20), (20, 30), (30, 40)):
        samples = [int(r["corrected_rtt_ns"]) / 1e6 for r in sender_reports
                   if start * 10**9 <= int(r["elapsed_ns"]) < stop * 10**9]
        rtts[f"{start}-{stop}"] = statistics.median(samples) if samples else None
    measured_seq = {int(r["sequence"]) for r in sends if int(r["elapsed_ns"]) >= 10**10}
    reverse_emitted = [r for r in b if r["type"] == "2" and
                       sender_ready + 10**10 <= int(r["monotonic_ns"]) < sender_ready + 4 * 10**10]
    measured_ticks = [int(r["target_bps"]) for r in ticks if int(r["elapsed_ns"]) >= 10**10]
    json_file(out / "summary.json", {
        "app_media_sent": len(sends), "receiver_interface_unique_media": len(b_media),
        "forward_measurement_offer_bps": len(measured_seq) * 568 * 8 / 30,
        "forward_measurement_arrivals_from_sent_cohort": len(measured_seq & b_media),
        "forward_measurement_datagrams_emitted": len(measured_seq),
        "feedback_capture_measurement_ip_bytes": 100 * len(reverse_emitted),
        "corrected_rtt_median_ms_by_sender_interval": rtts,
        "target_min_bps": min(measured_ticks), "target_max_bps": max(measured_ticks),
        "capture_tuple_checks_passed": True,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", choices=("calibrate", *CASES))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--arm", choices=("periodic", "adaptive"))
    parser.add_argument("--jar", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--java", default=shutil.which("java"))
    args = parser.parse_args()
    if args.case != "calibrate" and not all((args.arm, args.jar, args.profile, args.java)):
        parser.error("trials require --arm, --jar, --profile and Java")
    args.out = args.out.resolve()
    args.out.mkdir()
    net = Network(args.out)
    try:
        net.setup()
        calibration(net) if args.case == "calibrate" else trial(net, args)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
