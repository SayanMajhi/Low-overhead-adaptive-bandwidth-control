"""Generate independent simulation baselines correctness fixtures and invoke the Java simulator."""

import argparse
import copy
import csv
import hashlib
import json
from pathlib import Path
import subprocess


def link(capacity, queue, delay):
    return {
        "queueBytes": queue, "capacity": [{"atMs": 0, "bps": capacity}],
        "delayMs": delay, "jitterMs": 0, "lossPpm": 0,
        "burstEnterPpm": 0, "burstExitPpm": 1_000_000, "blackouts": [], "extraDelay": [],
    }


def fixtures():
    base = {
        "schemaVersion": 1, "scenarioId": "qa-base", "warmupMs": 0,
        "measureMs": 1_000, "drainMs": 300,
        "media": {"frameIntervalMs": 20, "deadlineMs": 150, "basePackets": 2,
                  "enhancementPackets": 8, "payloadBytesPerPacket": 500},
        "forward": link(2_272_000, 16_000, 1),
        "reverse": link(200_000, 4_000, 1),
        "background": [],
    }
    queue = copy.deepcopy(base)
    queue.update(scenarioId="qa-queue", measureMs=20)
    queue["forward"] = link(568_000, 568, 2)
    queue["forward"]["capacity"].append({"atMs": 4, "bps": 1_136_000})
    queue["background"] = [{"fromMs": 0, "untilMs": 1, "packetIpBytes": 568, "intervalMs": 10}]

    deadline = copy.deepcopy(base)
    deadline.update(scenarioId="qa-deadline", measureMs=20)
    deadline["forward"]["delayMs"] = 148

    reverse = copy.deepcopy(base)
    reverse.update(scenarioId="qa-reverse", measureMs=2_200)
    reverse["reverse"]["blackouts"] = [{"fromMs": 1, "untilMs": 2_100}]

    loss = copy.deepcopy(base)
    loss.update(scenarioId="qa-loss", measureMs=1_400)
    loss["forward"]["blackouts"] = [{"fromMs": 290, "untilMs": 450}]

    channel = copy.deepcopy(base)
    channel.update(scenarioId="qa-channel", measureMs=2_000)
    channel["forward"].update(delayMs=4, jitterMs=3, lossPpm=125_000)
    channel["background"] = [{"fromMs": 0, "untilMs": 1_000, "packetIpBytes": 568, "intervalMs": 23}]
    empty = copy.deepcopy(base)
    empty.update(scenarioId="qa-no-arrival", measureMs=20)
    empty["forward"]["lossPpm"] = 1_000_000
    return (base, queue, deadline, reverse, loss, channel, empty)


def test_invalid(stack, label, replacement):
    path = stack.output / f"{label}.json"
    path.write_text(replacement)
    invoke(stack, path, label, "fixed", 17703, success=False)


def invoke(stack, scenario, label, arm, seed, success=True, profile=None):
    output = stack.output / label
    command = [stack.java, "-jar", str(stack.jar), "simulate", "--scenario", str(scenario),
               "--profile", str(profile or stack.profile), "--arm", arm, "--seed", str(seed), "--out", str(output)]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=15)
    (stack.output / f"{label}.command.txt").write_text(repr(command) + "\n")
    (stack.output / f"{label}.log").write_text(completed.stdout + completed.stderr)
    assert (completed.returncode == 0) == success, (label, completed.returncode, completed.stderr)
    if not success:
        assert not output.exists(), label


def read_events(stack, run, endpoint):
    with (stack.output / run / f"{endpoint}.csv").open(newline="") as file:
        return list(csv.DictReader(file))


def events(rows, event):
    return [row for row in rows if row["event"] == event]


def check_endpoints(stack):
    regular = read_events(stack, "qa-base-fixed", "sender")
    latency = read_events(stack, "qa-base-fixed", "receiver")
    sends = {row["sequence"]: int(row["elapsed_ns"]) for row in events(regular, "media_sent")}
    arrivals = {row["sequence"]: int(row["elapsed_ns"]) for row in events(latency, "media_received")}
    assert len(sends) > 200 and set(sends) == set(arrivals)
    assert all(arrivals[seq] - sends[seq] == 3_000_000 for seq in sends)
    assert {row["target_bps"] for row in events(regular, "rate_tick")} == {"1400000"}
    aimd = read_events(stack, "qa-base-aimd", "sender")
    positive = [row for row in events(aimd, "rate_tick") if row["reason"] == "healthy_increase"]
    assert positive and int(positive[0]["target_bps"]) == 1_030_000

    loss_ticks = events(read_events(stack, "qa-loss-aimd", "sender"), "rate_tick")
    previous = 1_000_000
    reductions = 0
    for row in loss_ticks:
        rate = int(row["target_bps"])
        if row["reason"] == "loss_decrease":
            assert rate == max(500_000, previous * 3 // 4), row
            reductions += 1
        previous = rate
    assert reductions, loss_ticks

    queued = read_events(stack, "qa-queue-fixed", "receiver")
    arrived = events(queued, "media_received")
    assert arrived[0]["sequence"] == "0" and int(arrived[0]["elapsed_ns"]) == 12_000_000
    assert "1" not in {row["sequence"] for row in arrived}

    edge = read_events(stack, "qa-deadline-fixed", "receiver")
    receive_times = sorted(int(row["elapsed_ns"]) for row in events(edge, "media_received"))
    assert receive_times[0] == 150_000_000 and receive_times[1] > 150_000_000

    blackout = read_events(stack, "qa-reverse-aimd", "sender")
    stale = [(int(row["elapsed_ns"]), int(row["target_bps"]))
             for row in events(blackout, "rate_tick") if row["reason"] == "stale_decrease"]
    targets = [(900_000_000, 850_000), (1_400_000_000, 722_500),
               (1_900_000_000, 614_125)]
    assert stale == targets, stale
    assert all(int(row["elapsed_ns"]) >= 2_100_000_000
               for row in events(blackout, "feedback_received"))
    print("Endpoint event oracles verified 3 ms transit, 12 ms first queued arrival, "
          "inclusive deadline receive, clean AIMD growth, and exact silence reduction ticks.")


def check_links(stack, link_file):
    def read(run):
        with (stack.output / run / link_file).open(newline="") as file:
            return list(csv.DictReader(file))

    rows = read("qa-queue-fixed")
    actual = {(r["stream"], r["packet_id"], r["event"], int(r["elapsed_ns"]), r["reason"])
              for r in rows}
    assert ("background", "0", "serialized", 6_000_000, "") in actual
    assert ("background", "0", "arrived", 8_000_000, "") in actual
    assert ("media", "0", "admitted", 0, "queued") in actual
    assert any(stream == "media" and seq == "1" and event == "drop" and cause == "queue"
               for stream, seq, event, _, cause in actual)
    assert all(int(r["waiting_ip_bytes"]) <= 568 for r in rows
               if r["direction"] == "forward" and r["waiting_ip_bytes"])

    random_fixed = read("qa-channel-fixed")
    random_aimd = read("qa-channel-aimd")
    assert random_aimd == read("qa-channel-aimd-repeat")
    expected = [(n * 23_000_000, str(n)) for n in range(44)]
    for path in (random_fixed, random_aimd):
        emitted = [(int(r["elapsed_ns"]), r["packet_id"]) for r in path
                   if r["stream"] == "background" and r["event"] == "emitted"]
        assert emitted == expected, emitted
        assert any(r["stream"] == "media" and r["reason"] == "isolated" for r in path)
        terminals = [r for r in path if r["event"] in ("arrived", "drop", "undelivered")]
        terminal_ids = {(r["stream"], r["packet_id"]) for r in terminals}
        assert len(terminals) == len(terminal_ids), (len(terminals), len(terminal_ids))
        offered = {(r["stream"], r["packet_id"]) for r in path
                   if r["event"] == "admitted" or r["event"] == "drop" and r["reason"] == "queue"}
        assert terminal_ids == offered
    print("Link event oracles verified service boundary, FIFO/tail-drop, exogenous background, "
          "and one terminal outcome per admitted or queue-rejected packet.")


def check_metrics_and_replay(stack):
    def artifact(run, name):
        return stack.output / run / name

    def json_data(run, name):
        return json.loads(artifact(run, name).read_text())

    edge = json_data("qa-deadline-fixed", "summary.json")
    assert edge["frames_generated"] == 1
    assert edge["base_frames_complete"] == 0
    assert edge["on_time_payload_goodput_bps"] == 200_000
    assert edge["decodable_proxy_goodput_bps"] == 0
    assert edge["packet_p95_delay_ns"] == 150_000_000
    assert edge["media_delivery_samples"] == edge["media_emitted_measured_frames"]
    assert edge["media_late_arrivals"] == edge["media_delivery_samples"] - 1

    empty = json_data("qa-no-arrival-fixed", "summary.json")
    assert empty["media_delivery_samples"] == 0
    assert empty["packet_p95_delay_ns"] is None and empty["interarrival_jitter_ms"] is None
    assert empty["media_loss_ratio"] == 1 and empty["feedback_ip_bytes"] == 0
    assert empty["losses_by_cause"] == {"isolated": empty["media_emitted_measured_frames"]}

    queued = json_data("qa-queue-fixed", "summary.json")
    assert queued["forward_capacity_work_bit_ns"] == 20_448_000_000_000
    assert queued["forward_service_work_bit_ns"] == 20_448_000_000_000
    assert queued["forward_media_service_work_bit_ns"] == 15_904_000_000_000
    assert queued["forward_utilization"] == 1.0

    blackout = json_data("qa-reverse-fixed", "summary.json")
    rows = events(read_events(stack, "qa-reverse-fixed", "receiver"), "feedback_sent")
    assert blackout["feedback_packets_emitted"] == len(rows) == 21
    assert blackout["feedback_application_bytes"] == len(rows) * 72
    assert blackout["feedback_ip_bytes"] == len(rows) * 100
    denominator = blackout["feedback_ip_bytes"] + blackout["media_emitted_in_measurement_ip_bytes"]
    assert blackout["feedback_traffic_share"] == blackout["feedback_ip_bytes"] / denominator
    assert blackout["feedback_packets_received_in_measurement"] == 1

    fixed, aimd, repeat, other = (f"qa-channel-{suffix}" for suffix in
                                  ("fixed", "aimd", "aimd-repeat", "aimd-other-seed"))
    manifests = [json_data(run, "manifest.json") for run in (fixed, aimd, repeat, other)]
    for run, manifest in zip((fixed, aimd, repeat, other), manifests):
        assert manifest["scenarioSha256"] == hashlib.sha256(
            artifact(run, "scenario.json").read_bytes()).hexdigest()
        assert manifest["profileSha256"] == hashlib.sha256(
            artifact(run, "profile.json").read_bytes()).hexdigest()
        assert manifest["buildSha256"] == hashlib.sha256(stack.jar.read_bytes()).hexdigest()
    hashes = [manifest["exogenousTraceSha256"] for manifest in manifests]
    assert hashes[0] == hashes[1] == hashes[2] != hashes[3], hashes
    for name in ("sender.csv", "receiver.csv", "links.csv", "emission.csv"):
        assert artifact(aimd, name).read_bytes() == artifact(repeat, name).read_bytes(), name
    assert json_data(aimd, "summary.json") == json_data(repeat, "summary.json")
    assert json_data(fixed, "summary.json") != json_data(aimd, "summary.json")
    print("Summary and replay oracles verified inclusive deadline, byte accounting, partial service, "
          "paired trace hashes, changed-seed behavior, and deterministic endpoint/link/metric output.")


def main(stack):
    stack.output.mkdir(parents=True)
    cases = fixtures()
    base, queue, deadline, reverse, loss, channel, empty = cases
    for scenario in cases:
        label = scenario["scenarioId"]
        path = stack.output / f"{label}.json"
        path.write_text(json.dumps(scenario) + "\n")
        invoke(stack, path, f"{label}-fixed", "fixed", 17703)
        if scenario in (base, reverse, loss, channel):
            invoke(stack, path, f"{label}-aimd", "aimd", 17703)
        if scenario is channel:
            invoke(stack, path, f"{label}-aimd-repeat", "aimd", 17703)
            invoke(stack, path, f"{label}-aimd-other-seed", "aimd", 17704)
    valid = json.dumps(base)
    for index, altered in enumerate((
        valid.replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1'),
        valid.replace('"delayMs": 1', '"delayMs": 1.0', 1),
        valid.replace('"queueBytes": 16000', '"queueBytes": 567', 1),
        valid.replace('"payloadBytesPerPacket": 500', '"payloadBytesPerPacket": 501'),
        valid.replace('"drainMs": 300', '"drainMs": 149'),
        valid.replace('"measureMs": 1000', '"measureMs": 1001'),
        valid.replace('"delayMs": 1', '"delayMs": 9223372036854775807', 1),
        valid.replace('"lossPpm": 0', '"lossPpm": -1', 1),
        valid.replace('"lossPpm": 0', '"unknown": 1, "lossPpm": 0', 1),
        valid + " {}",
    )):
        test_invalid(stack, f"invalid-{index}", altered)
    profile = json.loads(stack.profile.read_text())
    reverse_path = stack.output / "qa-reverse.json"
    overflow_profile = stack.output / "qa-high-factor-profile.json"
    overflow_profile.write_text(json.dumps(profile | {
        "profileId": "qa-high-factor", "initialBps": 2_272_000,
        "stalePermille": 999, "aimdDecreasePermille": 999,
    }))
    invoke(stack, reverse_path, "qa-high-factor-aimd", "aimd", 17703, profile=overflow_profile)
    high_factor = read_events(stack, "qa-high-factor-aimd", "sender")
    reductions = [int(row["target_bps"]) for row in events(high_factor, "rate_tick")
                  if row["reason"] == "stale_decrease"]
    assert reductions[0] == 2_269_728, reductions
    for index, change in enumerate(({"periodMs": 150}, {"initialBps": 2_400_000},
                                    {"aimdDecreasePermille": 1000}, {"unknownProfile": 1})):
        invalid = stack.output / f"invalid-profile-{index}.json"
        invalid.write_text(json.dumps(profile | change))
        invoke(stack, reverse_path, f"invalid-profile-{index}", "aimd", 17703,
               success=False, profile=invalid)
    print("Simulation CLI produced independent fixtures and rejected invalid scenarios and profiles.")
    check_endpoints(stack)
    check_links(stack, "links.csv")
    check_metrics_and_replay(stack)



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java", default="java")
    parser.add_argument("--jar", type=Path, default=Path("target/bandwidth-control.jar"))
    parser.add_argument("--profile", type=Path, default=Path("config/profiles/baselines.json"))
    parser.add_argument("output", type=Path)
    main(parser.parse_args())
