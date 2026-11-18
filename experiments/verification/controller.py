"""Independently check controller implementation simulator arms on synthetic correctness fixtures."""

import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location(
    "simulator_checks", Path(__file__).with_name("simulator.py"))
simulator_checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(simulator_checks)


ARMS = ("fixed", "aimd", "periodic", "adaptive", "heartbeat-only",
        "matched-budget", "no-delay-periodic")


def run_case(stack, scenario, arms):
    label = scenario["scenarioId"]
    path = stack.output / f"{label}.json"
    path.write_text(json.dumps(scenario) + "\n")
    for arm in arms:
        simulator_checks.invoke(stack, path, f"{label}-{arm}", arm, 89171)
    return path


def data(stack, case, arm, name):
    return json.loads((stack.output / f"{case}-{arm}" / name).read_text())


def event_list(stack, case, arm, endpoint, event):
    rows = simulator_checks.read_events(stack, f"{case}-{arm}", endpoint)
    return simulator_checks.events(rows, event)


def verify_clean(stack, scenario):
    label = scenario["scenarioId"]
    manifests = [data(stack, label, arm, "manifest.json") for arm in ARMS]
    for key in ("scenarioSha256", "profileSha256", "exogenousTraceSha256", "buildSha256"):
        assert len({manifest[key] for manifest in manifests}) == 1, key
    assert manifests[0]["buildSha256"] == hashlib.sha256(stack.jar.read_bytes()).hexdigest()
    assert all(manifest["arm"] == arm and manifest["seed"] == 89171
               for manifest, arm in zip(manifests, ARMS, strict=True))

    for arm, period_ms in zip(ARMS, (100, 100, 100, 500, 500, 300, 100), strict=True):
        reports = event_list(stack, label, arm, "receiver", "feedback_sent")
        assert len(reports) >= 3, (arm, len(reports))
        assert [int(row["report"]) for row in reports] == list(range(len(reports))), arm
        assert [int(row["report_sample_ns"]) for row in reports] == [
            n * period_ms * 1_000_000 for n in range(1, len(reports) + 1)], arm
        assert all(row["application_bytes"] == "72" and row["ip_bytes"] == "100"
                   for row in reports), arm
        frames = event_list(stack, label, arm, "sender", "frame_created")
        assert [int(row["elapsed_ns"]) for row in frames] == [
            n * 20_000_000 for n in range(100)], arm

    for filename in ("sender.csv", "receiver.csv", "links.csv", "summary.json"):
        adaptive = stack.output / f"{label}-adaptive" / filename
        heartbeat = stack.output / f"{label}-heartbeat-only" / filename
        if filename == "receiver.csv":
            reports_a = event_list(stack, label, "adaptive", "receiver", "feedback_sent")
            reports_h = event_list(stack, label, "heartbeat-only", "receiver", "feedback_sent")
            ignore = {"reason"}
            assert [{k: v for k, v in row.items() if k not in ignore} for row in reports_a] == [
                {k: v for k, v in row.items() if k not in ignore} for row in reports_h]
        else:
            assert adaptive.read_bytes() == heartbeat.read_bytes(), filename
    print("Clean path: all seven arms used the same trace, v1 bytes and source clock. "
          "Cadences were exact and quiet adaptive delivery matched heartbeat-only.")


def verify_retry(stack, scenario):
    label = scenario["scenarioId"]
    for arm in ("adaptive", "periodic"):
        reports = event_list(stack, label, arm, "receiver", "feedback_sent")
        summary = data(stack, label, arm, "summary.json")
        assert reports and summary["feedback_packets_emitted"] == len(reports)
        assert summary["feedback_ip_bytes"] == len(reports) * 100
    reports = event_list(stack, label, "adaptive", "receiver", "feedback_sent")
    times = [int(row["report_sample_ns"]) for row in reports]
    gaps = [b - a for a, b in zip([0] + times, times)]
    assert min(gaps) >= 100_000_000 and max(gaps) <= 500_000_000, gaps
    assert any(gap < 500_000_000 for gap in gaps), gaps
    receipts = event_list(stack, label, "adaptive", "sender", "feedback_received")
    received_ids = {row["report"] for row in receipts}
    assert any(int(row["elapsed_ns"]) < 1_300_000_000 and row["report"] not in received_ids
               for row in reports), received_ids
    assert any(int(row["elapsed_ns"]) > 1_300_000_000 for row in receipts), receipts
    ticks = event_list(stack, label, "adaptive", "sender", "rate_tick")
    assert any(row["reason"] == "stale_decrease" for row in ticks)
    print("Reverse blackout: early reports and retries continued despite path drops. "
          "Emitted overhead included dropped feedback, and post-blackout feedback arrived.")


def verify_matched_periods(stack, scenario):
    profile = json.loads(stack.profile.read_text())
    for period in (100, 150, 200, 250, 300, 350, 400, 450, 500, 50, 325, 550):
        label = f"qa-matched-{period}"
        path = stack.output / f"{label}.json"
        path.write_text(json.dumps(profile | {
            "hybrid": profile["hybrid"] | {"matchedPeriodMs": period},
        }) + "\n")
        success = 100 <= period <= 500 and period % 50 == 0
        simulator_checks.invoke(stack, scenario, label, "matched-budget", 89171, success=success, profile=path)
        if success:
            reports = simulator_checks.events(simulator_checks.read_events(stack, label, "receiver"), "feedback_sent")
            assert [int(row["report_sample_ns"]) for row in reports[:2]] == [
                period * 1_000_000, period * 2_000_000], period
    print("Matched-budget accepted all nine 100–500 ms grid values and rejected 50, 325 and 550 ms.")


def main(stack):
    stack.output.mkdir(parents=True)
    clean = copy.deepcopy(simulator_checks.fixtures()[0])
    clean.update(scenarioId="qa-hybrid-clean", measureMs=2_000)
    clean_path = run_case(stack, clean, ARMS)
    verify_clean(stack, clean)
    verify_matched_periods(stack, clean_path)
    simulator_checks.invoke(stack, clean_path, "qa-hybrid-repeat", "adaptive", 89171)
    for filename in ("sender.csv", "receiver.csv", "links.csv", "emission.csv", "summary.json"):
        assert (stack.output / "qa-hybrid-clean-adaptive" / filename).read_bytes() == (
            stack.output / "qa-hybrid-repeat" / filename).read_bytes(), filename

    retransmit = copy.deepcopy(clean)
    retransmit.update(scenarioId="qa-hybrid-retry", measureMs=3_400)
    retransmit["forward"]["capacity"] = [{"atMs": 0, "bps": 800_000}]
    retransmit["forward"]["queueBytes"] = 1_136
    retransmit["reverse"]["blackouts"] = [{"fromMs": 0, "untilMs": 1_300}]
    run_case(stack, retransmit, ("adaptive", "periodic"))
    verify_retry(stack, retransmit)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java", default="java")
    parser.add_argument("--jar", type=Path, default=Path("target/bandwidth-control.jar"))
    parser.add_argument("--profile", type=Path, default=Path("config/profiles/controller-candidate.json"))
    parser.add_argument("output", type=Path)
    main(parser.parse_args())
