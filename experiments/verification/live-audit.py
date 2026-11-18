"""Independent recomputation of live transport packet evidence and endpoint telemetry."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shlex
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from analysis.evidence_paths import evidence_path


CASES = ("clean", "cap", "step", "reverse-delay")
ARMS = ("periodic", "adaptive")
NS = 1_000_000_000
FLOW = {("10.239.71.1", 52000, "10.239.71.2", 52001, 1, 568),
        ("10.239.71.2", 52001, "10.239.71.1", 52000, 2, 100)}


def load(path):
    return json.loads(path.read_text())


def rows(path):
    with path.open(newline="") as file:
        yield from csv.DictReader(file)


def qdisc(directory, stamp, dev):
    data = load(directory / f"qdisc-{stamp}.json")[dev]
    return next(row for row in data if row["root"])


def end_stats(records, warm_ns, stop_ns):
    return [r for r in records if warm_ns <= int(r["elapsed_ns"]) < stop_ns]


def route(directory):
    send, receive = [load(directory / f"route-{i}.json") for i in range(2)]
    assert send["network_namespace_inode"] != receive["network_namespace_inode"]
    for namespace, local, desired, peer in ((send, "10.239.71.1", "media0", "10.239.71.2"),
                                            (receive, "10.239.71.2", "report0", "10.239.71.1")):
        assert any(r["dev"] == desired and r["prefsrc"] == local and r["dst"] == peer
                   for r in namespace["peer_route"])


def calibration(root):
    route(root)
    observed = {}
    for name in ("clean-forward", "cap-forward", "clean-reverse", "delay-reverse", "loss-forward"):
        directory = root / name
        offered = load(directory / "offered.json")
        received = list(rows(directory / "packets.csv"))
        assert len(received) == len({r["sequence"] for r in received})
        assert all(0 <= int(r["sequence"]) < offered["packets"] for r in received)
        assert all(int(r["ip_bytes"]) == offered["payload_bytes"] + 28 for r in received)
        start, end = offered["first_due_ns"], offered["last_send_ns"]
        assert end > start
        arrival = [int(r["arrival_ns"]) for r in received]
        within = [r for r in received if min(arrival) + NS // 2 <= int(r["arrival_ns"]) < max(arrival) - NS // 2]
        window = max(arrival) - min(arrival) - NS
        one_way = [(int(r["arrival_ns"]) - int(r["send_ns"])) / 1e6 for r in received]
        observed[name] = {
            "received": len(received), "lost": offered["packets"] - len(received),
            "median_path_ms": median(one_way),
            "delivered_interior_bps": sum(int(r["ip_bytes"]) for r in within) * 8 * NS / window if window > 0 else None,
        }
    capped = qdisc(root / "cap-forward", "after", "media0")
    reverse = qdisc(root / "delay-reverse", "after", "report0")
    loss = qdisc(root / "loss-forward", "after", "media0")
    assert capped["kind"] == "tbf" and capped["options"]["rate"] * 8 == 950000
    assert capped["drops"] >= observed["cap-forward"]["lost"] > 0 and capped["overlimits"] > 0
    assert reverse["kind"] == "netem" and reverse["options"]["delay"]["delay"] == .1
    assert reverse["packets"] >= observed["delay-reverse"]["received"]
    assert loss["kind"] == "netem" and loss["options"]["loss-random"]["loss"] == .2
    assert loss["drops"] == observed["loss-forward"]["lost"]
    assert observed["clean-forward"]["delivered_interior_bps"] > 1_250_000
    assert 850_000 < observed["cap-forward"]["delivered_interior_bps"] < 1_050_000
    assert 85 < observed["delay-reverse"]["median_path_ms"] - observed["clean-reverse"]["median_path_ms"] < 130
    return observed


def check_routes_and_counters(path, case, trial):
    route(path)
    schedule = {"clean": (), "cap": (), "step": ((20, 950), (30, 1800)),
                "reverse-delay": ((20, 100), (30, 0))}[case]
    assert tuple((ch["scheduled_sender_second"], ch["setting"]) for ch in trial["changes"]) == schedule
    for change in trial["changes"]:
        elapsed = (change["command_started_ns"] - trial["sender_ready_ns"]) / NS
        assert abs(elapsed - change["scheduled_sender_second"]) < .25
        assert change["command_started_ns"] <= change["command_finished_ns"]
    if case in ("cap", "step"):
        assert all(qdisc(path, stamp, "report0")["kind"] == "noqueue" for stamp in ("before", "after"))
        desired = (("before", 950000), ("after", 950000)) if case == "cap" else (
            ("before", 1800000), ("change-20", 950000), ("change-30", 1800000), ("after", 1800000))
        for stamp, expected in desired:
            status = qdisc(path, stamp, "media0")
            assert status["kind"] == "tbf" and status["options"]["rate"] * 8 == expected
        done, first = [qdisc(path, stamp, "media0") for stamp in ("after", "before")]
        assert done["packets"] > first["packets"] and done["overlimits"] > first["overlimits"]
    if case == "reverse-delay":
        for stamp, delay in (("before", 0), ("change-20", .1), ("change-30", 0), ("after", 0)):
            assert qdisc(path, stamp, "media0")["kind"] == "noqueue"
            status = qdisc(path, stamp, "report0")
            actual = status["options"].get("delay", {}).get("delay", 0)
            assert status["kind"] == "netem" and actual == delay
        assert qdisc(path, "after", "report0")["packets"] > qdisc(path, "before", "report0")["packets"]
    if case == "clean":
        assert all(qdisc(path, stamp, dev)["kind"] == "noqueue"
                   for stamp in ("before", "after") for dev in ("media0", "report0"))


def capture(path, side):
    result = {1: {}, 2: {}}
    actual = set()
    for row in rows(path / f"capture-{side}.csv"):
        kind, sequence = int(row["type"]), int(row["id"])
        actual.add((row["src"], int(row["src_port"]), row["dst"], int(row["dst_port"]), kind, int(row["ip_bytes"])))
        assert kind in result and sequence not in result[kind] and row["session"] == "711"
        result[kind][sequence] = int(row["monotonic_ns"])
    assert actual == FLOW, (path, side, actual)
    return result


def check_trial(path, case, repetition, arm, profile):
    trial = load(path / "trial.json")
    assert trial["case"] == case and trial["arm"] == arm and trial["profile_sha256"] == profile
    check_routes_and_counters(path, case, trial)
    launch = [shlex.split(line) for line in (path / "commands.log").read_text().splitlines()
              if " -jar " in line]
    assert len(launch) == 2, (path, launch)
    for command, role, local, peer, duration in (
        (launch[0], "receive", "10.239.71.2:52001", "10.239.71.1:52000", "42750"),
        (launch[1], "send", "10.239.71.1:52000", "10.239.71.2:52001", "40000"),
    ):
        jar_index = command.index("-jar") + 1
        entrypoint, *arguments = command[jar_index + 1:]
        options = dict(zip(arguments[::2], arguments[1::2], strict=True))
        assert entrypoint == role and options["--arm"] == arm
        assert options["--bind"] == local and options["--peer"] == peer
        assert options["--session"] == "711" and options["--run-ms"] == duration
        assert hashlib.sha256(evidence_path(command[jar_index]).read_bytes()).hexdigest() == trial["jar_sha256"]
        assert hashlib.sha256(evidence_path(options["--profile"]).read_bytes()).hexdigest() == profile
    mcap, rcap = [capture(path, side) for side in range(2)]

    endpoints = [[*rows(path / f"{role}.csv")] for role in ("send", "receive")]
    sent, received, reports, accepted = [{int(row[key]): row for row in endpoint if row["event"] == event}
                                         for endpoint, event, key in (
        (endpoints[0], "media_sent", "sequence"), (endpoints[1], "media_received", "sequence"),
        (endpoints[1], "feedback_sent", "report"), (endpoints[0], "feedback_received", "report"))]
    assert set(mcap[1]) <= sent.keys() and set(rcap[1]) <= sent.keys()
    assert set(mcap[2]) <= reports.keys() and set(rcap[2]) <= reports.keys()
    assert set(received) <= sent.keys() and set(accepted) <= reports.keys()
    assert len(mcap[1].keys() & rcap[1].keys()) >= .99 * len(received)
    assert len(mcap[2].keys() & rcap[2].keys()) >= .99 * len(accepted)
    for key, remote in received.items():
        local = sent[key]
        assert all(local[col] == remote[col] for col in ("frame", "index", "session", "application_bytes", "ip_bytes"))
    rtts = []
    for key, current in accepted.items():
        original = reports[key]
        assert all(current[col] == original[col] for col in ("report_sample_ns", "echo_send_ns",
                                                            "echo_hold_us", "ip_bytes", "application_bytes"))
        if current["corrected_rtt_ns"]:
            corrected = int(current["elapsed_ns"]) - int(current["echo_send_ns"]) - int(current["echo_hold_us"]) * 1000
            assert corrected == int(current["corrected_rtt_ns"]) and corrected >= 0
            rtts.append((int(current["elapsed_ns"]), corrected / 1e6))
    def interval(lo, hi):
        selection = [rtt for time, rtt in rtts if lo * NS <= time < hi * NS]
        return median(selection) if selection else None
    pre, changed, recovered = [interval(lo, hi) for lo, hi in ((12, 18), (22, 28), (32, 38))]
    assert all(value is not None for value in (pre, changed, recovered))
    if case == "reverse-delay":
        assert 80 <= changed - pre <= 130, (path, pre, changed)
        assert abs(recovered - pre) <= 20, (path, pre, recovered)
    cohort = {int(r["sequence"]) for r in end_stats(sent.values(), 10 * NS, 40 * NS)}
    delivered = cohort & set(received)
    anchor = trial["sender_ready_ns"]
    wire_feedback = sum(100 for timestamp in rcap[2].values() if anchor + 10 * NS <= timestamp < anchor + 40 * NS)
    summary = load(path / "summary.json")
    assert wire_feedback == summary["feedback_capture_measurement_ip_bytes"]
    assert len(cohort) == summary["forward_measurement_datagrams_emitted"]
    assert len(cohort & set(rcap[1])) == summary["forward_measurement_arrivals_from_sent_cohort"]
    q0 = qdisc(path, "after", "media0")
    q1 = qdisc(path, "after", "report0")
    return {"case": case, "repetition": repetition, "arm": arm, "build": trial["jar_sha256"],
            "wire_feedback_bytes": wire_feedback, "media_emitted": len(cohort), "media_received": len(delivered),
            "payload_received_bps": len(delivered) * 4000 / 30,
            "rtt_pre_ms": pre, "rtt_during_ms": changed, "rtt_after_ms": recovered,
            "forward_qdisc_sends": q0["packets"], "forward_qdisc_drops": q0["drops"],
            "reverse_qdisc_sends": q1["packets"], "reverse_qdisc_drops": q1["drops"],
            "qdisc_change_max_command_span_ms": max(
                ((ch["command_finished_ns"] - ch["command_started_ns"]) / 1e6 for ch in trial["changes"]),
                default=0),
            "qdisc_change_max_start_error_ms": max(
                (abs((ch["command_started_ns"] - anchor) / 1e6 - 1000 * ch["scheduled_sender_second"])
                 for ch in trial["changes"]), default=0)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    print(json.dumps(calibration(args.root / "calibration"), indent=2))
    profile = hashlib.sha256(Path("config/profiles/frozen-study.json").read_bytes()).hexdigest()
    output = []
    for case in CASES:
        for repetition in range(1, 4):
            for arm in ARMS:
                directory = args.root / case / str(repetition) / arm
                if args.allow_partial and not (directory / "summary.json").exists():
                    continue
                output.append(check_trial(directory, case, repetition, arm, profile))
    if output:
        builds = {r["build"] for r in output}
        assert len(builds) == 1
        preserved = args.root / "build" / "bandwidth-control.jar"
        if preserved.exists():
            assert builds == {hashlib.sha256(preserved.read_bytes()).hexdigest()}
    print(f"Independently verified {len(output)} impairment trials")
    if args.out and output:
        with args.out.open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=output[0])
            writer.writeheader()
            writer.writerows(output)


if __name__ == "__main__":
    main()
