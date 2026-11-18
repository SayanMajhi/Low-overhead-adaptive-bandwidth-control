import csv
import select
import socket
import statistics
import struct
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
jar = ["java", "-jar", "target/bandwidth-control.jar"]
session = 12345

def endpoint(mode, address, peer, duration, path, rate=None):
    def address_arg(endpoint):
        return f"{endpoint[0]}:{endpoint[1]}"

    args = [*jar, mode, "--bind", address_arg(address), "--peer", address_arg(peer), "--session", str(session),
            "--run-ms", str(duration), "--events", str(path)]
    if rate:
        args.extend(["--rate-bps", str(rate)])
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        readable, _, _ = select.select([process.stdout], [], [], 3)
        line = process.stdout.readline().strip() if readable else ""
        assert line.startswith("READY"), (args, line)
    except BaseException:
        process.kill()
        _, stderr = process.communicate()
        if stderr:
            print(stderr, file=sys.stderr, end="")
        raise
    print(line)
    return process

def finish(process):
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 0, stderr
    print(stdout.strip())

def rows(path):
    with path.open() as file:
        return list(csv.DictReader(file))

def frame_accounting(events, frames):
    expected = {(str(i), str(j)) for i in range(frames) for j in range(10)}
    actual = [(row["frame"], row["index"]) for row in events
              if row["event"] in {"source_discard", "media_sent"}]
    assert len(actual) == len(expected) and set(actual) == expected

def port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()

sender_address = port()
receiver_address = port()
while receiver_address == sender_address:
    receiver_address = port()

for rate in (1_000_000, 800_000):
    recv_path = root / f"receive-{rate}.csv"
    send_path = root / f"send-{rate}.csv"
    duration_ms = 10_000 if rate == 800_000 else 4_000
    receiver = endpoint("receive", receiver_address, sender_address, duration_ms + 500, recv_path)
    sender = endpoint("send", sender_address, receiver_address, duration_ms, send_path, rate)
    finish(sender)
    finish(receiver)
    sent = rows(send_path)
    received = rows(recv_path)
    frame_accounting(sent, duration_ms // 20)
    sends = [row for row in sent if row["event"] == "media_sent"]
    receives = [row for row in received if row["event"] == "media_received"]
    assert [(x["sequence"], x["frame"], x["index"]) for x in sends] == [
        (x["sequence"], x["frame"], x["index"]) for x in receives]
    assert [int(x["sequence"]) for x in sends] == list(range(len(sends)))
    times = [int(x["elapsed_ns"]) for x in sends]
    gaps = [(b - a)/1e6 for a, b in zip(times, times[1:])]
    target_gap = 568 * 8 * 1000 / rate
    feedback_sent = [r for r in received if r["event"] == "feedback_sent"]
    feedback_received = [r for r in sent if r["event"] == "feedback_received"]
    assert len(feedback_sent) >= 35 and len(feedback_received) >= 35
    assert min(gaps) >= target_gap
    steady_packets = sum(timestamp >= 1_000_000_000 for timestamp in times)
    steady_bps = steady_packets * 568 * 8 / (duration_ms / 1000 - 1)
    steady_gaps = [gap for previous, gap in zip(times, gaps) if previous >= 1_000_000_000]
    positive_lateness = sorted(gap - target_gap for gap in steady_gaps)
    p95_lateness = positive_lateness[(95 * len(positive_lateness) + 99) // 100 - 1]
    if rate == 800_000:
        assert 0.95 * rate <= steady_bps <= 1.05 * rate, steady_bps
        assert p95_lateness <= 10, p95_lateness
    report_samples = [int(row["report_sample_ns"]) for row in feedback_sent]
    assert all(later - earlier == 100_000_000 for earlier, later in zip(report_samples, report_samples[1:]))
    print(f"{rate} bps target; {steady_bps:.0f} bps after warmup; p95 positive lateness {p95_lateness:.3f} ms;"
          f" min/median gap {min(gaps):.3f}/{statistics.median(gaps):.3f} ms;"
          f" reports sent/received {len(feedback_sent)}/{len(feedback_received)}")

# Same peer, one malformed datagram and one valid datagram.
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
    peer.bind(("127.0.0.1", 0))
    peer.settimeout(2)
    path = root / "malformed.csv"
    receiver = endpoint("receive", receiver_address, peer.getsockname(), 450, path)
    peer.sendto(b"broken", receiver_address)
    media = struct.pack(">4sBBHQQQIBBH", b"ABWC", 1, 1, 0, session, 0, 0, 0, 0, 0, 500) + bytes(500)
    peer.sendto(media, receiver_address)
    assert len(peer.recv(1024)) == 72
    finish(receiver)
    assert {"media_rejected", "media_received", "feedback_sent"} <= {row["event"] for row in rows(path)}
    print("malformed input rejected; next valid packet delivered and reported")

# Bound peer receives media but intentionally sends no reports.
with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
    peer.bind(("127.0.0.1", 0))
    path = root / "no-feedback.csv"
    sender = endpoint("send", sender_address, peer.getsockname(), 750, path, 800_000)
    finish(sender)
    all_rows = rows(path)
    assert any(row["event"] == "media_sent" for row in all_rows)
    ticks = [row for row in all_rows if row["event"] == "rate_tick"]
    assert len(ticks) >= 5 and all(r["reason"] == "no_feedback" and r["target_bps"] == "800000" for r in ticks)
    frame_accounting(all_rows, 38)
    print(f"silent feedback path; {len(ticks)} unchanged fixed-rate ticks")
