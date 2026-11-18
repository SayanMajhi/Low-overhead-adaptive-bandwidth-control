"""Run three repetitions of each periodic/adaptive UDP pair."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess

from netns import CASES

RUNNER = Path(__file__).with_name("run.sh")


def pair(root, jar, profile, case, repetition):
    folder = root / case / str(repetition)
    folder.mkdir(parents=True, exist_ok=True)
    arms = ("periodic", "adaptive") if repetition % 2 else ("adaptive", "periodic")
    for arm in arms:
        output = folder / arm
        if (output / "summary.json").is_file():
            recorded = json.loads((output / "trial.json").read_text())
            if recorded["case"] != case or recorded["arm"] != arm:
                raise RuntimeError(f"cannot reuse inconsistent trial {output}")
            continue
        attempt = 1
        while output.exists():
            preserved = folder / f"{arm}.incomplete-{attempt}"
            attempt += 1
            if not preserved.exists():
                output.rename(preserved)
        subprocess.run(["bash", RUNNER, case, "--out", output, "--jar", jar, "--profile", profile,
                        "--arm", arm], check=True)
        print(f"complete {case} {repetition} {arm}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jar", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1, choices=(1, 2))
    config = parser.parse_args()
    tasks = [(case, number) for case in CASES for number in range(1, 4)]
    with ThreadPoolExecutor(config.jobs) as pool:
        futures = [pool.submit(pair, config.out.resolve(), config.jar.resolve(), config.profile.resolve(), *task)
                   for task in tasks]
        for future in as_completed(futures):
            future.result()


if __name__ == "__main__":
    main()
