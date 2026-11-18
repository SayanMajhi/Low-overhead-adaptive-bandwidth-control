"""Independent UDP transport UDP checks. Run with a built jar and a fresh evidence directory."""

import argparse
import csv
import errno
import math
from pathlib import Path
import socket
import struct
import subprocess
import time


IP = "127.0.0.1"
MEDIA = struct.Struct(">4sBBHQQQIBBH")
REPORT = struct.Struct(">4sBBHQQQI HHiHH QQII")


def packet(sequence, frame, index=0, session=1):
    return MEDIA.pack(b"ABWC", 1, 1, 0, session, sequence, sequence * 1_000_000,
                      frame, index, 0, 500) + bytes(500)


def reserve():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((IP, 0))
        return sock.getsockname()[1]


def launch(stack, kind, local, peer, duration, target=None, label=None):
    label = label or kind
    command = [stack.java, "-jar", str(stack.jar), kind,
               "--bind", f"{IP}:{local}", "--peer", f"{IP}:{peer}", "--session", "1",
               "--run-ms", str(duration), "--events", str(stack.output / f"{label}.csv")]
    if target is not None:
        command.extend(("--rate-bps", str(target)))
    (stack.output / f"{label}.command.txt").write_text(repr(command) + "\n")
    with (stack.output / f"{label}.stdout.txt").open("wb") as stdout:
        return subprocess.Popen(command, stdout=stdout, stderr=subprocess.STDOUT)


def wait_bound(process, port):
    end = time.monotonic() + 4
    while time.monotonic() < end:
        assert process.poll() is None, f"endpoint exited early ({process.returncode})"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.bind((IP, port))
            except OSError as exc:
                if exc.errno != errno.EADDRINUSE:
                    raise
                return
        time.sleep(0.01)
    raise AssertionError(f"endpoint never bound port {port}")


def clean_finish(process):
    try:
        assert process.wait(timeout=16) == 0, f"endpoint exit {process.returncode}"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def events(stack, label, event):
    with (stack.output / f"{label}.csv").open(newline="") as source:
        return [row for row in csv.DictReader(source) if row["event"] == event]


def test_pair(stack):
    receiver_port, sender_port = reserve(), reserve()
    while sender_port == receiver_port:
        sender_port = reserve()
    receiver = launch(stack, "receive", receiver_port, sender_port, 5500, label="pair-receive")
    try:
        wait_bound(receiver, receiver_port)
        sender = launch(stack, "send", sender_port, receiver_port, 3500,
                        1_000_000, label="pair-send")
        clean_finish(sender)
    finally:
        clean_finish(receiver)
    sent = events(stack, "pair-send", "media_sent")
    received = events(stack, "pair-receive", "media_received")
    assert len(sent) > 500, len(sent)
    assert [(r["sequence"], r["frame"], r["index"], r["ip_bytes"]) for r in sent] == [
        (r["sequence"], r["frame"], r["index"], r["ip_bytes"]) for r in received]
    assert all(r["application_bytes"] == "540" and r["ip_bytes"] == "568" for r in sent)
    reported = events(stack, "pair-receive", "feedback_sent")
    accepted = events(stack, "pair-send", "feedback_received")
    assert 30 <= len(accepted) <= len(reported), (len(accepted), len(reported))
    assert [r["report"] for r in reported[:len(accepted)]] == [r["report"] for r in accepted]
    assert all(r["application_bytes"] == "72" and r["ip_bytes"] == "100" for r in accepted)
    assert any(int(r["corrected_rtt_ns"]) >= 0 for r in accepted if r["corrected_rtt_ns"])
    print(f"Java pair: {len(sent)} media and {len(accepted)} accepted feedback datagrams "
          f"matched independent telemetry joins, of {len(reported)} reports emitted.")


def test_receiver(stack):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer,             socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
        peer.bind((IP, 0))
        other.bind((IP, 0))
        port = reserve()
        receiver = launch(stack, "receive", port, peer.getsockname()[1],
                          2500, label="inject-receive")
        target = (IP, port)
        try:
            wait_bound(receiver, port)
            good = packet(0, 0)
            invalid_packets = [b"", good[:15], good[:539], good + b"x",
                               bytes(1400), b"!" + good[1:],
                               good[:4] + b"\x02" + good[5:], packet(0, 0, session=2),
                               good[:37] + b"\x80" + good[38:]]
            for invalid in invalid_packets:
                peer.sendto(invalid, target)
            other.sendto(packet(40, 40), target)
            peer.settimeout(0.18)
            try:
                peer.recv(512)
            except TimeoutError:
                pass
            else:
                raise AssertionError("receiver emitted feedback before valid peer media")

            # First gap is filled before 75 ms. The second expires and later arrives.
            for sequence, frame in [(0, 0), (2, 2), (1, 1), (2, 2), (4, 4), (8, 4)]:
                peer.sendto(packet(sequence, frame), target)
                time.sleep(0.005)
            time.sleep(0.09)
            peer.sendto(packet(3, 3), target)
            reports = []
            peer.settimeout(0.45)
            for _ in range(5):
                raw = peer.recv(1024)
                assert len(raw) == 72, len(raw)
                fields = REPORT.unpack(raw)
                assert fields[:5] == (b"ABWC", 1, 2, 0, 1), fields
                assert fields[11] & ~3 == 0 and fields[12] == fields[16] == 0, fields
                assert fields[11] & 2 and fields[13] == 4, fields
                reports.append(fields)
            assert [f[5] for f in reports] == list(range(5)), reports
            assert [f[6] for f in reports] == [n * 100_000_000 for n in range(1, 6)], reports
            assert any(f[9] == 1 for f in reports), reports
            print("Malformed/wrong-peer packets produced no reports before valid media. "
                  "Five reports strictly sequential, at nominal 100 ms periods, "
                  "with expected echo frontier and confirmed gap.")
            with (stack.output / "inject-reports.csv").open("w", newline="") as output:
                rows = csv.writer(output)
                rows.writerow(("magic", "version", "type", "reserved", "session", "report",
                               "sample_ns", "receive_ip_bytes", "finalized_received",
                               "finalized_missing", "delay_us", "flags", "reserved16",
                               "echo_sequence", "echo_send_ns", "hold_us", "reserved32"))
                rows.writerows(reports)
        finally:
            clean_finish(receiver)
    received = events(stack, "inject-receive", "media_received")
    assert [(r["sequence"], r["reason"]) for r in received] == [
        ("0", "in_order"), ("2", "in_order"), ("1", "reordered"),
        ("4", "in_order"), ("3", "late_after_gap")]
    assert len(events(stack, "inject-receive", "media_duplicate")) == 1
    assert any(r["reason"] == "identity_conflict"
               for r in events(stack, "inject-receive", "media_rejected"))


def test_feedback_validation(stack):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        peer.bind((IP, 0))
        peer.settimeout(2)
        sender_port = reserve()
        sender = launch(stack, "send", sender_port, peer.getsockname()[1],
                        1400, 800_000, label="inject-send")
        try:
            raw, sender_address = peer.recvfrom(1024)
            fields = MEDIA.unpack_from(raw)
            sequence, send_time = fields[5:7]

            def report(report_id, sample_ms, echo=sequence, echo_time=send_time, session=1):
                return REPORT.pack(b"ABWC", 1, 2, 0, session, report_id,
                                   sample_ms * 1_000_000, 568, 1, 0, 0, 2, 0,
                                   echo, echo_time, 0, 0)

            valid = report(8, 100)
            inputs = [valid[:71], valid + b"x", report(8, 100, session=2),
                      report(8, 100, echo=1_000_000), report(8, 100, echo_time=send_time + 1),
                      valid, report(7, 150), valid, report(9, 99), report(9, 200)]
            for datagram in inputs:
                peer.sendto(datagram, sender_address)
                time.sleep(0.01)
        finally:
            clean_finish(sender)
    accepted = events(stack, "inject-send", "feedback_received")
    assert [r["report"] for r in accepted] == ["8", "9"], accepted
    reasons = [r["reason"] for r in events(stack, "inject-send", "feedback_rejected")]
    assert reasons == ["length", "length", "session", "unknown_echo_sequence",
                       "echo_timestamp", "old_report", "old_report", "old_report"], reasons
    print(f"Sender report validation: accepted IDs 8 and 9 only, {len(reasons)} rejections.")

def test_sender(stack):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as peer:
        peer.bind((IP, 0))
        peer.settimeout(0.25)
        port = reserve()
        sender = launch(stack, "send", port, peer.getsockname()[1],
                        11_000, 800_000, label="silent-send")
        frames = []
        limit = time.monotonic() + 15
        try:
            while time.monotonic() < limit:
                try:
                    raw = peer.recv(2048)
                except TimeoutError:
                    if sender.poll() is not None:
                        break
                    continue
                assert len(raw) == 540, len(raw)
                header = MEDIA.unpack_from(raw)
                assert header[:5] == (b"ABWC", 1, 1, 0, 1), header
                assert header[5] == len(frames), header
                assert header[8] <= 9 and header[9:] == (0, 500), header
                assert raw[40:] == bytes(500)
                frames.append((header[5], header[6], header[7], header[8]))
        finally:
            clean_finish(sender)

    active = [row for row in frames if 1_000_000_000 <= row[1] < 11_000_000_000]
    assert active, "No valid media in the ten-second measurement interval"
    amount = len(active) * 568 * 8 / 10
    gaps = [b[1] - a[1] for a, b in zip(active, active[1:])]
    ideal = 5_680_000
    with (stack.output / "silent-wire-media.csv").open("w", newline="") as output:
        rows = csv.writer(output)
        rows.writerow(("sequence", "send_time_ns", "frame", "index"))
        rows.writerows(frames)
    sent = events(stack, "silent-send", "media_sent")
    discarded = events(stack, "silent-send", "source_discard")
    created = events(stack, "silent-send", "frame_created")
    terminal = [(r["frame"], r["index"]) for r in sent + discarded]
    assert len(terminal) == len(set(terminal)) == len(created) * 10
    assert set(terminal) == {(r["frame"], str(i)) for r in created for i in range(10)}
    assert [(int(r["sequence"]), int(r["elapsed_ns"]), int(r["frame"]), int(r["index"]))
            for r in sent] == frames
    ticks = events(stack, "silent-send", "rate_tick")
    assert len(ticks) > 90 and all(
        r["reason"] == "no_feedback" and r["target_bps"] == "800000" and not r["report_age_ns"]
        for r in ticks)
    assert not events(stack, "silent-send", "feedback_received")
    assert min(gaps) >= ideal, f"pacing burst: {min(gaps)} < {ideal} ns"
    lateness = sorted(gap - ideal for gap in gaps)
    p95 = lateness[math.ceil(0.95 * len(lateness)) - 1]
    # A whole frame is not always backlogged. A source idle gap makes this metric
    # conservative because it is measured against the earliest pacer eligibility.
    assert p95 <= 10_000_000, f"p95 lateness exceeds 10 ms ({p95} ns)"
    assert abs(amount / 800_000 - 1) <= 0.05, f"offered rate misses ±5%: {amount} bps"
    print(f"Silent peer: {len(frames)} media datagrams on wire, "
          f"{amount:.1f} measured IPv4 bit/s, minimum send spacing "
          f"{min(gaps) / 1e6:.3f} ms, p95 extra spacing {p95 / 1e6:.3f} ms.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java", default="java")
    parser.add_argument("--jar", type=Path, default=Path("target/bandwidth-control.jar"))
    parser.add_argument("output", type=Path)
    options = parser.parse_args()
    options.output.mkdir(parents=True)
    test_pair(options)
    test_receiver(options)
    test_feedback_validation(options)
    test_sender(options)
