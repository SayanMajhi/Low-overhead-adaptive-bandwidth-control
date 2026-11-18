"""Regenerate paired analysis from the controlled study archived Java simulator outputs."""
import argparse
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import tarfile

import numpy as np

ARMS = (
    "fixed", "aimd", "periodic", "adaptive", "heartbeat-only",
    "matched-budget", "no-delay-periodic",
)
TUNING = range(520101, 520111)
HELD_OUT = range(630201, 630231)
CANDIDATE_MS = range(100, 501, 50)
BOOTSTRAP_SOURCE = Path(__file__).with_name("BootstrapIndices.java")
PRIMARY = (
    ("feedback_ip_bytes", "feedback_savings", "savings", "lower", 0.25, 1),
    ("base_completion_ratio", "completion_loss_pp", "control_loss", "upper", 2, 100),
    ("on_time_payload_goodput_bps", "goodput_retention", "ratio", "lower", 0.95, 1),
    ("packet_p95_delay_ns", "p95_delay_increase_ms", "difference", "upper", 20, 1e-6),
)
LINK_SERIES = ("forward", "reverse")


def member(archive, filename):
    hits = [info for info in archive.getmembers() if info.isfile() and Path(info.name).name == filename]
    if len(hits) != 1:
        raise ValueError(f"{archive.name}: expected one {filename}, found {len(hits)}")
    return archive.extractfile(hits[0])


def flatten(summary, prefix=""):
    result = {}
    for key, value in summary.items():
        field = prefix + key
        if isinstance(value, dict):
            result.update(flatten(value, field + "."))
        else:
            result[field] = value
    return result


def step_outcome(targets, transition_ms, pre_capacity, post_capacity):
    """Rate immediately before the step must be on the old side of the band."""
    transition = transition_ms * 1_000_000
    deadline = transition + 5_000_000_000
    dwell = 500_000_000
    upper = post_capacity * 102
    lower = post_capacity * 75
    before = [rate for time, rate in targets if time < transition]
    if not before:
        raise ValueError("Step trace missing pretransition target")
    old_rate = before[-1]
    downstep = post_capacity < pre_capacity
    if (downstep and old_rate * 100 <= upper) or (not downstep and old_rate * 100 >= lower):
        return 5000, "precondition_failed"

    start = None
    entered = False
    for time, rate in targets:
        if time < transition or time > deadline:
            continue
        if start is not None and start + dwell <= time:
            return (start + dwell - transition) / 1_000_000, "settled"
        if lower <= rate * 100 <= upper:
            entered = True
            if start is None:
                start = time
        else:
            start = None
    if start is not None and start + dwell <= deadline:
        return (start + dwell - transition) / 1_000_000, "settled"
    reason = "never_entered"
    if start is not None:
        reason = "dwell_incomplete"
    elif entered:
        reason = "entered_then_exited"
    return 5000, reason


def read_run(path, scenario_name, seed, arm):
    with tarfile.open(path, "r:gz") as archive:
        with member(archive, "manifest.json") as source:
            manifest = json.load(source)
        with member(archive, "summary.json") as source:
            summary_bytes = source.read()
            summary = flatten(json.loads(summary_bytes))
        expected_arm = "matched-budget" if arm.startswith("matched-budget-") else arm
        if manifest["seed"] != seed or manifest["arm"] != expected_arm:
            raise ValueError(f"Trial identity does not match archive path: {path}")
        with member(archive, "scenario.json") as source:
            raw = source.read()
        if hashlib.sha256(raw).hexdigest() != manifest["scenarioSha256"]:
            raise ValueError(f"Scenario snapshot digest does not match manifest: {path}")
        scenario = json.loads(raw)
        if scenario["scenarioId"] != scenario_name:
            raise ValueError(f"Scenario ID does not match path: {path}")
        with member(archive, "profile.json") as source:
            raw = source.read()
        if hashlib.sha256(raw).hexdigest() != manifest["profileSha256"]:
            raise ValueError(f"Profile snapshot digest does not match manifest: {path}")
        if arm.startswith("matched-budget-"):
            profile = json.loads(raw)
            if profile["hybrid"]["matchedPeriodMs"] != int(arm.rsplit("-", 1)[1]):
                raise ValueError(f"Grid period does not match archive path: {path}")
        outcome = {}
        if scenario_name in {"downstep", "upstep", "tune-downstep", "tune-upstep"}:
            with io.TextIOWrapper(member(archive, "sender.csv")) as source:
                targets = [
                    (int(row["elapsed_ns"]), int(row["target_bps"]))
                    for row in csv.DictReader(source)
                    if row["event"] == "rate_tick"
                ]
            start, end = scenario["forward"]["capacity"]
            step_direction = "down" if end["bps"] < start["bps"] else "up"
            step_time, step_status = step_outcome(targets, end["atMs"], start["bps"], end["bps"])
            outcome = {"step_direction": step_direction, "step_time_ms": step_time, "step_status": step_status}
    return {
        "scenario": scenario_name, "seed": seed, "arm": arm, "archive": str(path),
        "summarySha256": hashlib.sha256(summary_bytes).hexdigest(),
        **{key: manifest[key] for key in (
            "runId", "schemaVersion", "clock", "buildSha256", "profileSha256",
            "scenarioSha256", "exogenousTraceSha256", "javaVendor", "javaVersion",
        )}, **summary, **outcome,
    }


def quantile(samples, probability):
    index = math.ceil(probability * len(samples)) - 1
    return float(np.partition(samples, index)[index])


def bootstrap_indices(java, size):
    raw = subprocess.check_output([java, str(BOOTSTRAP_SOURCE), str(size)])
    indices = np.frombuffer(raw, dtype=np.uint8).reshape(100_000, size)
    return indices, hashlib.sha256(raw).hexdigest()


def interval(control, challenger, indices, operation="difference", scale=1):
    control = np.array(control, dtype=float) * scale
    challenger = np.array(challenger, dtype=float) * scale
    control_means = control[indices].mean(axis=1)
    challenger_means = challenger[indices].mean(axis=1)
    if operation in {"savings", "ratio"}:
        with np.errstate(divide="ignore", invalid="ignore"):
            replicates = challenger_means / control_means
        estimate = challenger.mean() / control.mean() if control.mean() != 0 else math.nan
        if operation == "savings":
            replicates = 1 - replicates
            estimate = 1 - estimate
    else:
        pairs = challenger - control
        if operation == "control_loss":
            pairs = -pairs
        replicates = pairs[indices].mean(axis=1)
        estimate = pairs.mean()
    invalid = int((~np.isfinite(replicates)).sum())
    result = {
        "estimate": float(estimate) if np.isfinite(estimate) else None,
        "undefined_pairs": int((~(np.isfinite(control) & np.isfinite(challenger))).sum()),
        "undefined_replicates": invalid,
    }
    if invalid or result["estimate"] is None:
        return result
    return dict(result, lower95=quantile(replicates, .05), upper95=quantile(replicates, .95),
                ci95_low=quantile(replicates, .025), ci95_high=quantile(replicates, .975))


def trial_data(root, dataset, seeds):
    scenarios = sorted((root / "scenarios").glob("*.json"))
    plan_bytes = (root / "plan.json").read_bytes()
    plan = json.loads(plan_bytes)
    if plan["campaign"] != dataset:
        raise ValueError("Archive campaign does not match selected dataset")
    if dataset == "held-out" and not plan["freeze_sha256"]:
        raise ValueError("Held-out plan is missing its freeze hash")
    scenario_hashes = {path.stem: hashlib.sha256(path.read_bytes()).hexdigest() for path in scenarios}
    if scenario_hashes != plan["scenario_sha256"]:
        raise ValueError("Scenario snapshots do not match plan")
    base_bytes = (root / "profiles" / "base.json").read_bytes()
    profile_hashes = {"base": hashlib.sha256(base_bytes).hexdigest()}
    if dataset == "tuning":
        for period in CANDIDATE_MS:
            key = f"matched-budget-{period}"
            raw = (root / "profiles" / (key + ".json")).read_bytes()
            profile = json.loads(raw)
            expected = json.loads(base_bytes)
            expected["profileId"] = profile["profileId"]
            expected["hybrid"]["matchedPeriodMs"] = period
            if profile != expected:
                raise ValueError(f"Grid profile changes settings beyond matched period and profile ID: {key}")
            profile_hashes[key] = hashlib.sha256(raw).hexdigest()
    if profile_hashes != plan["profile_sha256"]:
        raise ValueError("Profile snapshots do not match plan")
    files = {}
    for scenario in scenarios:
        for seed in seeds:
            available = list(ARMS)
            if dataset == "tuning":
                available.remove("matched-budget")
                if scenario.stem == "tune-variable":
                    available += [f"matched-budget-{period}" for period in CANDIDATE_MS]
            for arm in available:
                key = scenario.stem, seed, arm
                files[key] = root / "runs" / scenario.stem / str(seed) / f"{arm}.tar.gz"
    if len(scenarios) != (6 if dataset == "tuning" else 11):
        raise ValueError("Scenario inventory does not match prespecified case count")
    expected = set(files.values())
    observed = set((root / "runs").rglob("*.tar.gz"))
    if expected != observed:
        raise ValueError(f"Archive inventory mismatch: {len(expected-observed)} missing, {len(observed-expected)} extra")
    planned_tasks = {(scenario, seed, arm): profile for scenario, seed, arm, profile in plan["tasks"]}
    if len(plan["tasks"]) != len(planned_tasks) or planned_tasks.keys() != files.keys():
        raise ValueError("Trial inventory does not match plan")
    with (root / "run-list.csv").open(encoding="utf-8", newline="") as source:
        completed = list(csv.DictReader(source))
    run_list = {(row["scenario"], int(row["seed"]), row["arm"]): row for row in completed}
    if len(completed) != len(run_list) or run_list.keys() != files.keys():
        raise ValueError("Trial inventory does not match run list")
    records = {}
    for key, path in files.items():
        row = read_run(path, *key)
        if (row["scenarioSha256"] != scenario_hashes[key[0]]
                or row["buildSha256"] != plan["build_sha256"]
                or row["profileSha256"] != profile_hashes[planned_tasks[key]]):
            raise ValueError(f"Trial identity does not match plan: {path}")
        entry = run_list[key]
        if (entry["status"] != "complete" or entry["archive"] != str(path.relative_to(root))
                or int(entry["archive_bytes"]) != path.stat().st_size
                or int(entry["feedback_ip_bytes"]) != row["feedback_ip_bytes"]
                or entry["summary_sha256"] != row["summarySha256"]
                or entry["trace_sha256"] != row["exogenousTraceSha256"]):
            raise ValueError(f"Trial or summary does not match completed run list: {path}")
        records[key] = row
    regular = [row for row in records.values() if not row["arm"].startswith("matched-budget-")]
    for key in ("buildSha256", "profileSha256", "javaVendor", "javaVersion"):
        if len({row[key] for row in regular}) != 1:
            raise ValueError(f"Main trials must share {key}")
    for row in records.values():
        control = records[row["scenario"], row["seed"], "periodic"]
        equal = ("scenarioSha256", "exogenousTraceSha256", "buildSha256", "javaVendor", "javaVersion", "schemaVersion", "clock")
        if any(row[key] != control[key] for key in equal):
            raise ValueError(f"Run incompatible with its paired periodic comparator: {row['archive']}")
        if not row["arm"].startswith("matched-budget-") and row["profileSha256"] != control["profileSha256"]:
            raise ValueError(f"Run profile differs from its comparator: {row['archive']}")
    return records, [path.stem for path in scenarios], hashlib.sha256(plan_bytes).hexdigest()


def compare(records, scenarios, seeds, indices, dataset):
    effects, gates = [], []
    for scenario in scenarios:
        control = [records[scenario, seed, "periodic"] for seed in seeds]
        comparators = sorted({arm for name, _, arm in records if name == scenario} - {"periodic"})
        for arm in comparators:
            test = [records[scenario, seed, arm] for seed in seeds]
            specification = list(PRIMARY)
            other = sorted({key for row in control + test for key, value in row.items()
                            if isinstance(value, (int, float))}
                           - {item[0] for item in PRIMARY} - {"seed", "schemaVersion"})
            specification += [(key, key + "_difference", "difference", None, None, 1) for key in other]
            if "step_time_ms" in control[0]:
                specification = [(a, b, c, d, e, f) for a, b, c, d, e, f in specification
                                 if a != "step_time_ms"]
                specification += [("step_time_ms", "truncated_step_time_increase_ms", "difference", "upper", 200, 1)]
            for key, name, operation, side, margin, scale in specification:
                if key.startswith("losses_by_cause."):
                    # The runner omits zero-count drop causes.
                    values = [[row.get(key, 0) for row in group] for group in (control, test)]
                else:
                    values = [[row.get(key) for row in group] for group in (control, test)]
                stats = interval(*values, indices, operation, scale)
                row = dict(scenario=scenario, arm=arm, reference="periodic", metric=name, **stats)
                effects.append(row)
                primary = scenario == "fluctuate" and side is not None
                step = scenario in {"upstep", "downstep"} and key == "step_time_ms"
                if dataset == "held-out" and arm == "adaptive" and (primary or step):
                    bound = stats.get(side + "95")
                    gate = dict(row, required_bound=side, margin=margin)
                    if bound is None:
                        gate["status"] = "inconclusive"
                    else:
                        clears = bound >= margin if side == "lower" else bound <= margin
                        gate["status"] = "clears" if clears else "does_not_clear"
                    gates.append(gate)
            if "step_status" in control[0]:
                counts = [sum(row["step_status"] == "settled" for row in group) for group in (control, test)]
                if dataset == "held-out" and arm == "adaptive":
                    clears = counts[0] >= 27 and counts[1] >= 27 and counts[1] >= counts[0]
                    gates.append(dict(scenario=scenario, arm=arm, metric="settled_counts",
                                      periodic_settled=counts[0], adaptive_settled=counts[1],
                                      status="clears" if clears else "does_not_clear"))
    return effects, gates


def write_csv(path, rows):
    columns = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, columns)
        writer.writeheader()
        writer.writerows(rows)


def select_period(records):
    def size(arm):
        return sum(records["tune-variable", seed, arm]["feedback_ip_bytes"] for seed in TUNING)

    budget = size("adaptive")
    rows = []
    for period in CANDIDATE_MS:
        total = size(f"matched-budget-{period}")
        rows.append(dict(period_ms=period, total_feedback_ip_bytes=total, adaptive_total_ip_bytes=budget,
                         mean_feedback_ip_bytes_per_s=total / (len(TUNING) * 30),
                         absolute_gap_ip_bytes=abs(total - budget)))
    chosen = min(rows, key=lambda row: (row["absolute_gap_ip_bytes"], row["period_ms"]))
    return rows, chosen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", choices=("tuning", "held-out"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--java", default="java")
    args = parser.parse_args()
    seeds = TUNING if args.dataset == "tuning" else HELD_OUT
    records, scenarios, plan_digest = trial_data(args.input, args.dataset, seeds)
    # Resetting the Java generator at each scenario yields this same index matrix.
    indices, digest = bootstrap_indices(args.java, len(seeds))
    effects, gates = compare(records, scenarios, seeds, indices, args.dataset)
    args.out.mkdir(parents=True, exist_ok=False)
    write_csv(args.out / "runs.csv", list(records.values()))
    write_csv(args.out / "effects.csv", effects)
    metadata = dict(dataset=args.dataset, input=str(args.input), runs=len(records), resamples=len(indices),
                    plan_sha256=plan_digest, bootstrap_seed=2026111401, bootstrap_indices_sha256=digest,
                    bootstrap_java=subprocess.check_output([args.java, "-version"], stderr=subprocess.STDOUT,
                                                           text=True).strip(),
                    gates=gates)
    if args.dataset == "tuning":
        grid, chosen = select_period(records)
        write_csv(args.out / "matched-budget.csv", grid)
        metadata["selected_period_ms"] = chosen["period_ms"]
    else:
        if len(gates) != 8:
            raise ValueError(f"Expected eight preregistered guardrails, got {len(gates)}")
        metadata["all_guardrails_clear"] = all(row["status"] == "clears" for row in gates)
    (args.out / "analysis.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
    print(f"{args.dataset}: {len(records)} runs, {len(effects)} paired estimates")


if __name__ == "__main__":
    main()
