"""Small UDP calibration peer and AF_PACKET IPv4/UDP path observer."""

import argparse
import csv
import json
from pathlib import Path
import socket
import struct
import time


def capture(interface, output, seconds):
    # Classic Linux Ethernet packet socket. Filter to this isolated peer pair and UDP ports.
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3)) as link, output.open("w") as file:
        link.bind((interface, 0))
        link.settimeout(0.1)
        writer = csv.writer(file)
        writer.writerow(("monotonic_ns", "src", "src_port", "dst", "dst_port", "ip_bytes", "type", "session", "id"))
        deadline = time.monotonic() + seconds
        print("READY", flush=True)
        while time.monotonic() < deadline:
            try:
                packet, _ = link.recvfrom(65535)
            except socket.timeout:
                continue
            now = time.monotonic_ns()
            if len(packet) < 42 or packet[12:14] != b"\x08\x00" or packet[23] != 17:
                continue
            end_ip = 14 + 4 * (packet[14] & 15)
            if end_ip + 32 > len(packet):
                continue
            source, dest = struct.unpack_from("!HH", packet, end_ip)
            if (source, dest) not in {(52000, 52001), (52001, 52000)}:
                continue
            ip_bytes = struct.unpack_from("!H", packet, 16)[0]
            if packet[end_ip + 8:end_ip + 12] != b"ABWC":
                continue
            kind = packet[end_ip + 13]
            session, identifier = struct.unpack_from("!QQ", packet, end_ip + 16)
            writer.writerow((now, socket.inet_ntoa(packet[26:30]), source, socket.inet_ntoa(packet[30:34]), dest,
                             ip_bytes, kind, session, identifier))


def probe_receive(address, port, output, seconds):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as link, output.open("w") as file:
        link.bind((address, port))
        link.settimeout(0.1)
        writer = csv.writer(file)
        writer.writerow(("sequence", "send_ns", "arrival_ns", "ip_bytes"))
        deadline = time.monotonic() + seconds
        print("READY", flush=True)
        while time.monotonic() < deadline:
            try:
                packet = link.recv(2000)
            except socket.timeout:
                continue
            arrival = time.monotonic_ns()
            if len(packet) < 16:
                continue
            sequence, sent = struct.unpack_from("!QQ", packet)
            writer.writerow((sequence, sent, arrival, len(packet) + 28))


def probe_send(address, peer, port, packets, gap_us, payload, output):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as link:
        link.bind((address, port + 1))
        padding = bytes(payload - 16)
        start = time.monotonic_ns()
        for sequence in range(packets):
            due = start + sequence * gap_us * 1000
            wait = (due - time.monotonic_ns()) / 1e9
            if wait > 0:
                time.sleep(wait)
            now = time.monotonic_ns()
            link.sendto(struct.pack("!QQ", sequence, now) + padding, (peer, port))
        output.write_text(json.dumps({"packets": packets, "payload_bytes": payload, "gap_us": gap_us,
                                      "first_due_ns": start, "last_send_ns": now}) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    observe = sub.add_parser("capture")
    observe.add_argument("interface")
    observe.add_argument("output", type=Path)
    observe.add_argument("seconds", type=float)
    receive = sub.add_parser("probe-receive")
    receive.add_argument("address")
    receive.add_argument("port", type=int)
    receive.add_argument("output", type=Path)
    receive.add_argument("seconds", type=float)
    send = sub.add_parser("probe-send")
    send.add_argument("address")
    send.add_argument("peer")
    send.add_argument("port", type=int)
    send.add_argument("packets", type=int)
    send.add_argument("gap_us", type=int)
    send.add_argument("payload", type=int)
    send.add_argument("output", type=Path)
    args = vars(parser.parse_args())
    action = args.pop("action").replace("-", "_")
    {"capture": capture, "probe_receive": probe_receive, "probe_send": probe_send}[action](**args)


if __name__ == "__main__":
    main()
