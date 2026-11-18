#!/usr/bin/env python3
"""Execute a fixed simulation matrix and archive every run without modifying its contents."""

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_ARMS = ("adaptive", "aimd", "fixed", "heartbeat-only", "no-delay-periodic", "periodic")
TUNING_SCENARIOS = {
    "tune-downstep", "tune-feedback-delay-loss", "tune-stable-high",
    "tune-stable-low", "tune-upstep", "tune-variable",
}
HELD_OUT_SCENARIOS = {
    "burst-loss", "cross-traffic", "delay-jitter", "downstep", "feedback-delay",
    "feedback-loss", "fluctuate", "random-loss", "stable-high", "stable-low", "upstep",
}
REQUIRED_ARTIFACTS = {
    "emission.csv", "links.csv", "manifest.json", "profile.json",
    "receiver.csv", "scenario.json", "sender.csv", "summary.json",
    "stdout.txt", "stderr.txt",
}
COLUMNS = [
    "scenario", "seed", "arm", "simulator_arm", "archive", "status", "archive_bytes",
    "scenario_sha256", "profile_sha256", "trace_sha256", "build_sha256",
    "feedback_ip_bytes", "summary_sha256",
]


def digest(blob):
    return hashlib.sha256(blob).hexdigest()


def sha_file(path):
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def durable_replace(path, blob):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        file.write(blob)
        temp_path = Path(file.name)
    temp_path.replace(path)


def preserve(path, blob):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != blob:
            raise ValueError(f"existing frozen input differs: {path}")
    else:
        with path.open("xb") as target:
            target.write(blob)


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign", choices=("tuning", "held-out"))
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--scenario-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--freeze", type=Path)
    parser.add_argument("--jobs", type=int, default=3)
    parsed = parser.parse_args()
    if not 1 <= parsed.jobs <= 16:
        parser.error("--jobs must be 1–16")
    if parsed.campaign == "held-out" and parsed.freeze is None:
        parser.error("--freeze is required to run the held-out campaign")
    return parsed


def initialize(args):
    jar = args.jar.resolve(strict=True)
    java = Path(shutil.which("java") or "java").resolve(strict=True)
    runtime = subprocess.check_output([java, "-version"], stderr=subprocess.STDOUT, text=True).strip()
    if not re.search(r'version "21\.', runtime):
        raise ValueError("the simulator campaign requires Java 21")
    candidate = args.profile.read_bytes()
    profiles = {"base": candidate}
    if args.campaign == "tuning":
        seeds = range(520101, 520111)
        expected_names = TUNING_SCENARIOS
        for interval in range(100, 501, 50):
            variant = json.loads(candidate)
            variant["profileId"] += f"-matched-{interval}"
            variant["hybrid"]["matchedPeriodMs"] = interval
            profiles[f"matched-budget-{interval}"] = encoded(variant)
    else:
        seeds = range(630201, 630231)
        expected_names = HELD_OUT_SCENARIOS
    scenarios = {p.stem: p.read_bytes() for p in args.scenario_dir.glob("*.json")}
    if scenarios.keys() != expected_names:
        raise ValueError(f"scenario set differs from protocol: {sorted(scenarios.keys() ^ expected_names)}")
    for scenario, blob in scenarios.items():
        if json.loads(blob)["scenarioId"] != scenario:
            raise ValueError(f"scenarioId does not match filename: {scenario}")
    scenario_hashes = {key: digest(blob) for key, blob in scenarios.items()}
    profile_hashes = {key: digest(blob) for key, blob in profiles.items()}
    build_hash = sha_file(jar)
    freeze_hash = None
    if args.freeze:
        freeze_blob = args.freeze.read_bytes()
        freeze = json.loads(freeze_blob)
        if (
            freeze["scenario_sha256"] != scenario_hashes
            or freeze["profile_sha256"] != profile_hashes["base"]
            or freeze["build_sha256"] != build_hash
            or freeze["java_version_output"] != runtime
            or freeze["seeds"] != list(seeds)
            or sorted(freeze["arms"]) != sorted((*BASE_ARMS, "matched-budget"))
        ):
            raise ValueError("supplied inputs do not match the freeze manifest")
        freeze_hash = digest(freeze_blob)
    tasks = []
    for scenario in sorted(scenarios):
        arms = list(BASE_ARMS)
        if args.campaign == "held-out":
            arms.append("matched-budget")
        elif scenario == "tune-variable":
            arms.extend(set(profiles) - {"base"})
        for seed in seeds:
            for arm in sorted(arms):
                profile = arm if arm.startswith("matched-budget-") else "base"
                tasks.append((scenario, seed, arm, profile))
    plan = {
        "schema_version": 1,
        "campaign": args.campaign,
        "java_version_output": runtime,
        "java_binary": str(java),
        "build_sha256": build_hash,
        "runner_sha256": sha_file(Path(__file__)),
        "profile_sha256": profile_hashes,
        "scenario_sha256": scenario_hashes,
        "freeze_sha256": freeze_hash,
        "tasks": tasks,
    }
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=True)
    preserve(root / "plan.json", encoded(plan))
    if args.freeze:
        preserve(root / "freeze.json", freeze_blob)
    for name, blob in scenarios.items():
        preserve(root / "scenarios" / f"{name}.json", blob)
    for name, blob in profiles.items():
        preserve(root / "profiles" / f"{name}.json", blob)
    frozen_jar = root / "build" / "bandwidth-control.jar"
    preserve(frozen_jar, jar.read_bytes())
    return root, java, frozen_jar, plan, tasks


def inspect(root, jar, plan, task, archive, verify_members=True):
    scenario, seed, arm, profile = task
    simulator_arm = "matched-budget" if arm.startswith("matched-budget-") else arm
    with tarfile.open(archive, "r:gz") as file:
        names = set(file.getnames())
        if names != REQUIRED_ARTIFACTS:
            raise ValueError(f"incorrect artifacts: {archive}")
        if verify_members:
            for member in file.getmembers():
                if not member.isfile() or len(file.extractfile(member).read()) != member.size:
                    raise ValueError(f"unreadable archive member: {archive}/{member.name}")
        manifest = json.load(file.extractfile("manifest.json"))
        summary_blob = file.extractfile("summary.json").read()
        summary = json.loads(summary_blob)
    expected = {
        "seed": seed,
        "arm": simulator_arm,
        "buildSha256": plan["build_sha256"],
        "scenarioSha256": plan["scenario_sha256"][scenario],
        "profileSha256": plan["profile_sha256"][profile],
    }
    if any(manifest[key] != value for key, value in expected.items()):
        raise ValueError(f"mismatched run identity: {archive}")
    if not re.fullmatch(r"[0-9a-f]{64}", manifest["exogenousTraceSha256"]):
        raise ValueError(f"invalid trace hash: {archive}")
    return {
        "scenario": scenario, "seed": seed, "arm": arm, "simulator_arm": simulator_arm,
        "archive": str(archive.relative_to(root)), "status": "complete", "archive_bytes": archive.stat().st_size,
        "scenario_sha256": expected["scenarioSha256"], "profile_sha256": expected["profileSha256"],
        "trace_sha256": manifest["exogenousTraceSha256"], "build_sha256": expected["buildSha256"],
        "feedback_ip_bytes": summary["feedback_ip_bytes"], "summary_sha256": digest(summary_blob),
    }


def run_trial(root, java, jar, plan, task):
    scenario, seed, arm, profile = task
    target = root / "runs" / scenario / str(seed) / f"{arm}.tar.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return inspect(root, jar, plan, task, target), True
    simulator_arm = "matched-budget" if arm.startswith("matched-budget-") else arm
    with tempfile.TemporaryDirectory(prefix="cn-campaign-") as scratch:
        scratch_path = Path(scratch)
        out = scratch_path / "raw"
        command = [java, "-Xms24m", "-Xmx192m", "-jar", jar, "simulate",
                   "--scenario", root / "scenarios" / f"{scenario}.json",
                   "--profile", root / "profiles" / f"{profile}.json",
                   "--arm", simulator_arm, "--seed", str(seed), "--out", out]
        result = subprocess.run(command, capture_output=True, check=False)
        if not out.exists():
            out.mkdir()
        (out / "stdout.txt").write_bytes(result.stdout)
        (out / "stderr.txt").write_bytes(result.stderr)
        if result.returncode != 0:
            failed = root / "failed" / scenario / str(seed) / arm / str(time.time_ns())
            failed.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(out, failed)
            raise RuntimeError(f"simulator exited {result.returncode}. See {failed}")
        scratch_archive = scratch_path / "trial.tar.gz"
        with tarfile.open(scratch_archive, "w:gz", compresslevel=3) as file:
            for item in sorted(out.iterdir()):
                file.add(item, arcname=item.name)
        copied = target.with_name(target.name + ".partial")
        shutil.copyfile(scratch_archive, copied)
        inspect(root, jar, plan, task, copied, verify_members=False)
        copied.replace(target)
    return inspect(root, jar, plan, task, target, verify_members=False), False


def write_list(path, tasks, completed):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS)
    writer.writeheader()
    for scenario, seed, arm, _ in tasks:
        key = (scenario, seed, arm)
        writer.writerow(completed.get(key, {
            "scenario": scenario, "seed": seed, "arm": arm, "status": "pending",
        }))
    durable_replace(path, stream.getvalue().encode())


def run(args):
    root, java, jar, plan, tasks = initialize(args)
    list_path = root / "run-list.csv"
    if list_path.exists():
        with list_path.open(newline="") as prior:
            completed = {
                (row["scenario"], int(row["seed"]), row["arm"]): row
                for row in csv.DictReader(prior)
                if row["status"] == "complete"
            }
    else:
        completed = {}
    write_list(list_path, tasks, completed)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(run_trial, root, java, jar, plan, task): task for task in tasks}
        try:
            for index, future in enumerate(as_completed(futures), start=1):
                row, reused = future.result()
                completed[(row["scenario"], row["seed"], row["arm"])] = row
                if index % 10 == 0 or index == len(tasks):
                    write_list(list_path, tasks, completed)
                    print(f"{index}/{len(tasks)} verified; latest {row['scenario']} {row['seed']} {row['arm']}"
                          + (" (reused)" if reused else ""), flush=True)
        except BaseException:
            for future in futures:
                future.cancel()
            write_list(list_path, tasks, completed)
            raise
    for scenario, seed, _, _ in tasks:
        trace_hashes = {
            row["trace_sha256"] for key, row in completed.items() if key[:2] == (scenario, seed)
        }
        if len(trace_hashes) != 1:
            raise RuntimeError(f"trace differs across paired arms: {scenario} {seed}")
    write_list(list_path, tasks, completed)


if __name__ == "__main__":
    run(options())
