"""Independent raw simulation audit. Run on three held-out gate scenarios after freeze."""

import argparse
import concurrent.futures
import csv
from datetime import datetime
import hashlib
import io
import json
from math import isclose
import re
from pathlib import Path
import subprocess
import tarfile


SEEDS = range(630201, 630231)
ARMS = ("fixed", "aimd", "periodic", "adaptive", "heartbeat-only",
        "matched-budget", "no-delay-periodic")
GATES = ("fluctuate", "downstep", "upstep")
NS_PER_MS = 1_000_000
START = 10_000 * NS_PER_MS
STOP = 40_000 * NS_PER_MS


def csv_rows(path):
    with path.open(newline="") as f:
        yield from csv.DictReader(f)


def archive_rows(tar, member):
    with io.TextIOWrapper(tar.extractfile(member), encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def archive_json(tar, member):
    return json.load(tar.extractfile(member))


def read_json(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_metrics(path):
    with tarfile.open(path, "r:gz") as tar:
        return raw_metrics_archive(tar)


def raw_metrics_archive(tar):
    frames, sent, targets = {}, {}, []
    for row in archive_rows(tar, "sender.csv"):
        time = int(row["elapsed_ns"])
        if row["event"] == "frame_created" and START <= time < STOP:
            frames[int(row["frame"])] = time
        if row["event"] == "media_sent" and int(row["frame"]) in frames:
            sent[int(row["sequence"])] = (time, int(row["frame"]), int(row["index"]))
        if row["event"] == "rate_tick":
            targets.append((time, int(row["target_bps"])))

    feedback, seen, delays, base = 0, set(), [], set()
    timely = 0
    for row in archive_rows(tar, "receiver.csv"):
        time = int(row["elapsed_ns"])
        if row["event"] == "feedback_sent" and START <= time < STOP:
            assert int(row["ip_bytes"]) == 100 and int(row["application_bytes"]) == 72
            feedback += int(row["ip_bytes"])
        if row["event"] == "media_received":
            seq = int(row["sequence"])
            if seq in sent and seq not in seen:
                seen.add(seq)
                emitted, frame, index = sent[seq]
                delays.append(time - emitted)
                if time <= frames[frame] + 150 * NS_PER_MS:
                    timely += 500
                    if index < 2:
                        base.add((frame, index))
    assert len(frames) == 1500
    completed = sum((frame, 0) in base and (frame, 1) in base for frame in frames)
    p95 = sorted(delays)[((len(delays) * 95 + 99) // 100) - 1] if delays else None
    obs = (feedback, completed, timely, p95)
    summary = archive_json(tar, "summary.json")
    assert feedback == summary["feedback_ip_bytes"]
    assert completed == summary["base_frames_complete"]
    assert timely * 8 / 30 == summary["on_time_payload_goodput_bps"]
    assert p95 == summary["packet_p95_delay_ns"]
    assert len(sent) - len(seen) == summary["media_losses"]
    return obs, targets


def settling(targets, scenario):
    change = 20_000 * NS_PER_MS
    dwell = 500 * NS_PER_MS
    cutoff = change + 5_000 * NS_PER_MS
    down = scenario == "downstep"
    low, high = (712_500, 969_000) if down else (1_350_000, 1_836_000)
    before = [rate for time, rate in targets if time < change][-1]
    if (down and before <= high) or (not down and before >= low):
        return 5000, "precondition_failed"
    # A tick exactly on the end of dwell may leave the band.
    # Each target sample spans up to but excluding the next tick.
    in_band_since = None
    entered = False
    for sample, subsequent in zip([(change, before)] + [
              t for t in targets if change <= t[0] < cutoff],
              [t for t in targets if change <= t[0] < cutoff] + [(cutoff, 0)]):
        start, rate = sample
        finish, _ = subsequent
        if start >= finish:
            continue
        if low <= rate <= high:
            entered = True
            if in_band_since is None:
                in_band_since = start
            if finish - in_band_since >= dwell:
                return (in_band_since + dwell - change) // NS_PER_MS, "settled"
        else:
            in_band_since = None
    return 5000, "entered_but_not_settled" if entered else "never_entered"


def inventory(root):
    index = {}
    rows = list(csv_rows(root / "run-list.csv"))
    expected = {(scenario.stem, seed, arm)
                for scenario in Path("config/scenarios/held-out").glob("*.json")
                for seed in SEEDS for arm in ARMS}
    plan = read_json(root / "plan.json")
    freeze_path = root / "freeze.json"
    frozen = read_json(freeze_path)
    assert plan["freeze_sha256"] == sha(freeze_path)
    assert plan["build_sha256"] == frozen["build_sha256"] == sha(root / "build" / "bandwidth-control.jar")
    assert plan["profile_sha256"]["base"] == frozen["profile_sha256"] == sha(root / "profiles" / "base.json")
    assert plan["scenario_sha256"] == frozen["scenario_sha256"]
    assert frozen["seeds"] == list(SEEDS) and set(frozen["arms"]) == set(ARMS)
    assert plan["java_version_output"] == frozen["java_version_output"]
    assert {tuple(task[:3]) for task in plan["tasks"]} == expected
    assert all(task[3] == "base" for task in plan["tasks"])
    freeze_time = datetime.fromisoformat(frozen["recorded_utc"])
    scenario_digests = {name: sha(Path("config/scenarios/held-out") / f"{name}.json")
                        for name, _, _ in expected}
    assert scenario_digests == frozen["scenario_sha256"]
    assert [(r["scenario"], int(r["seed"]), r["arm"]) for r in rows] == sorted(expected)
    build_hashes = set()
    profile_hashes = set()
    traces = {}
    expected_members = {"sender.csv", "receiver.csv", "links.csv", "summary.json", "emission.csv",
                        "manifest.json", "scenario.json", "profile.json", "stdout.txt", "stderr.txt"}
    assert len(rows) == len(expected)
    assert all(row["status"] == "complete" for row in rows), "held-out matrix is still running"
    def inspect(row):
        key = (row["scenario"], int(row["seed"]), row["arm"])
        assert key in expected, key
        assert row["status"] == "complete" and row["arm"] == row["simulator_arm"], key
        archive = root / row["archive"]
        assert archive.is_file() and archive.stat().st_size == int(row["archive_bytes"]), key
        # Traverse gzip once and retain only the four small metadata artifacts.
        retained = {"manifest.json", "scenario.json", "profile.json", "summary.json"}
        found = {}
        names = []
        with tarfile.open(archive, "r|gz", bufsize=128 * 1024) as tar:
            for member in tar:
                assert member.isfile(), (key, member.name)
                names.append(member.name)
                if member.name in retained:
                    with tar.extractfile(member) as file:
                        found[member.name] = file.read()
        assert set(names) == expected_members and len(names) == len(expected_members), key
        manifest = json.loads(found["manifest.json"])
        assert manifest["arm"] == key[2] and manifest["seed"] == key[1]
        assert datetime.fromisoformat(manifest["startUtc"]) >= freeze_time, key
        assert json.loads(found["scenario.json"])["scenarioId"] == key[0], key
        for member, field, row_field in (
            ("scenario.json", "scenarioSha256", "scenario_sha256"),
            ("profile.json", "profileSha256", "profile_sha256"),
        ):
            assert manifest[field] == row[row_field] == hashlib.sha256(found[member]).hexdigest(), key
        summary = found["summary.json"]
        assert hashlib.sha256(summary).hexdigest() == row["summary_sha256"], key
        assert json.loads(summary)["feedback_ip_bytes"] == int(row["feedback_ip_bytes"]), key
        for archive_field, index_field in (("buildSha256", "build_sha256"),
                                           ("exogenousTraceSha256", "trace_sha256")):
            assert manifest[archive_field] == row[index_field], key
        return (key, archive)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for row, (key, archive) in zip(rows, pool.map(inspect, rows), strict=True):
            assert key not in index, key
            assert row["scenario_sha256"] == scenario_digests[key[0]]
            build_hashes.add(row["build_sha256"])
            profile_hashes.add(row["profile_sha256"])
            span = key[:2]
            old = traces.setdefault(span, row["trace_sha256"])
            assert old == row["trace_sha256"], key
            index[key] = archive
    assert set(index) == expected and len(index) == 11 * 30 * 7
    assert build_hashes == {frozen["build_sha256"]}
    assert profile_hashes == {frozen["profile_sha256"]}
    print(f"Inventory {len(index)} complete runs, {len(traces)} exact matched scenario/seed traces; "
          f"build {build_hashes.pop()}, profile {profile_hashes.pop()}.", flush=True)
    return index


def calibration(root):
    counts = {}
    rows = list(csv_rows(root / "run-list.csv"))
    periods = range(100, 501, 50)
    tuning_seeds = range(520101, 520111)
    arm_names = ("adaptive", *(f"matched-budget-{period}" for period in periods))
    expected = {(period, seed) for period in (0, *periods) for seed in tuning_seeds}
    shared = {}
    base_profile = read_json(root / "profiles" / "base.json")
    selected_rows = [row for row in rows
                     if row["scenario"] == "tune-variable" and row["arm"] in arm_names]
    assert len(selected_rows) == 100
    assert all(row["status"] == "complete" for row in selected_rows), "calibration matrix is still running"
    for row in selected_rows:
        with tarfile.open(root / row["archive"], "r:gz") as tar:
            profile = archive_json(tar, "profile.json")
            period = 0 if row["arm"] == "adaptive" else profile["hybrid"]["matchedPeriodMs"]
            assert row["arm"] == ("adaptive" if period == 0 else f"matched-budget-{period}")
            expected_profile = (base_profile if period == 0 else base_profile | {
                "hybrid": base_profile["hybrid"] | {"matchedPeriodMs": period},
            })
            if period != 0:
                expected_profile = expected_profile | {
                    "profileId": f"{base_profile['profileId']}-matched-{period}",
                }
            assert profile == expected_profile, (period, profile)
            seed = int(row["seed"])
            key = (period, seed)
            assert key in expected and key not in counts and row["status"] == "complete", key
            manifest = archive_json(tar, "manifest.json")
            assert manifest["seed"] == seed and manifest["arm"] == row["simulator_arm"]
            assert manifest["exogenousTraceSha256"] == shared.setdefault(seed, row["trace_sha256"])
            emitted = sum(int(record["ip_bytes"]) for record in archive_rows(tar, "receiver.csv")
                          if record["event"] == "feedback_sent" and START <= int(record["elapsed_ns"]) < STOP)
            assert emitted == int(row["feedback_ip_bytes"]), key
            counts[key] = emitted
    assert set(counts) == expected, sorted(expected - set(counts))
    totals = {period: sum(counts[period, seed] for seed in tuning_seeds) for period in (0, *periods)}
    selected = min(periods, key=lambda period: (abs(totals[period] - totals[0]), period))
    print("Raw calibration totals for adaptive (0) and each interval:", totals, flush=True)
    print("Selected period by exact difference and shorter tie:", selected, flush=True)


def audit_gate(stack, index, gates=GATES):
    for scenario in gates:
        rows = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            tasks = {key: pool.submit(raw_metrics, index[key]) for key in index
                     if key[0] == scenario and key[2] in ("periodic", "adaptive")}
            for seed in SEEDS:
                p, p_ticks = tasks[scenario, seed, "periodic"].result()
                a, a_ticks = tasks[scenario, seed, "adaptive"].result()
                p_step = settling(p_ticks, scenario) if scenario != "fluctuate" else (0, "none")
                a_step = settling(a_ticks, scenario) if scenario != "fluctuate" else (0, "none")
                rows.append([seed, *p, *a, *p_step, *a_step])
        output = stack.output / f"{scenario}-raw-pairs.csv"
        with output.open("w", newline="") as f:
            out = csv.writer(f)
            out.writerow(("seed", "p_bytes", "p_completed", "p_on_time_bytes", "p_p95_ns",
                          "a_bytes", "a_completed", "a_on_time_bytes", "a_p95_ns",
                          "p_step_ms", "p_step_state", "a_step_ms", "a_step_state"))
            out.writerows(rows)
        print(f"Raw recomputed {scenario}: {len(rows)} full pairs.", flush=True)
        raw = subprocess.check_output(
            [stack.java, str(Path(__file__).with_name("GateBootstrap.java")), str(output)], text=True)
        (stack.output / f"{scenario}-bootstrap.txt").write_text(raw)
        print(raw, end="", flush=True)


def compare_report(production, evidence):
    gates = read_json(production)["gates"]
    expected = {
        ("fluctuate", "feedback_savings"): ("feedback_savings_pct", .01),
        ("fluctuate", "completion_loss_pp"): ("completion_loss_pp", 1),
        ("fluctuate", "goodput_retention"): ("goodput_retention", 1),
        ("fluctuate", "p95_delay_increase_ms"): ("p95_increase_ms", 1),
        ("downstep", "truncated_step_time_increase_ms"): ("settle_increase_ms", 1),
        ("upstep", "truncated_step_time_increase_ms"): ("settle_increase_ms", 1),
    }
    assert len(gates) == 8
    for gate in gates:
        scenario, metric = gate["scenario"], gate["metric"]
        text = (evidence / f"{scenario}-bootstrap.txt").read_text()
        if metric == "settled_counts":
            row = re.search(r"periodic=(\d+) adaptive=(\d+) count_pass=(true|false)", text)
            assert int(row[1]) == gate["periodic_settled"]
            assert int(row[2]) == gate["adaptive_settled"]
            assert (row[3] == "true") == (gate["status"] == "clears")
            continue
        label, scale = expected.pop((scenario, metric))
        line = next(part for part in text.splitlines() if part.startswith(label + " "))
        if "inconclusive" in line:
            assert gate["status"] == "inconclusive"
            continue
        vals = [float(value) * scale for value in re.findall(r"-?\d+\.\d+", line)]
        for name, result in zip(("estimate", "lower95", "upper95", "ci95_low", "ci95_high"),
                                vals, strict=True):
            assert isclose(gate[name], result, abs_tol=1e-10, rel_tol=1e-12), (metric, name, result, gate[name])
        side = gate["required_bound"]
        bound = vals[1] if side == "lower" else vals[2]
        passes = bound >= gate["margin"] if side == "lower" else bound <= gate["margin"]
        assert passes == (gate["status"] == "clears"), gate
    assert not expected
    print("All eight production gates matched the independent raw-log and bootstrap audit.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--held-out", type=Path)
    parser.add_argument("--single-gate", choices=GATES,
                        help="Audit one complete, frozen gate before the full campaign finishes")
    parser.add_argument("--java", default="java")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-analysis", type=Path)
    stack = parser.parse_args()
    if stack.calibration:
        calibration(stack.calibration)
    if stack.held_out:
        assert stack.output is not None
        stack.output.mkdir(parents=True)
        if stack.single_gate:
            root = stack.held_out
            assert read_json(root / "plan.json")["freeze_sha256"] == sha(root / "freeze.json")
            indexed = {(row["scenario"], int(row["seed"]), row["arm"]): root / row["archive"]
                       for row in csv_rows(root / "run-list.csv")
                       if row["scenario"] == stack.single_gate and row["status"] == "complete"
                       and row["arm"] in ("periodic", "adaptive")}
            assert len(indexed) == 60, "gate matrix is incomplete"
            audit_gate(stack, indexed, (stack.single_gate,))
        else:
            audit_gate(stack, inventory(stack.held_out))
    if stack.compare_analysis:
        compare_report(stack.compare_analysis, stack.output)


if __name__ == "__main__":
    main()
