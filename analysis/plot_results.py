"""Plot paired effects and lowest-seed network timelines."""
import argparse
import csv
import io
import json
from pathlib import Path
import tarfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from evaluate import member
from evidence_paths import evidence_path


COLORS = {"periodic": "#214E78", "adaptive": "#AC4F0D"}


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def save(fig, path):
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def effects_figure(effects, path):
    measures = (
        ("feedback_savings", "Feedback IPv4 bytes saved (%)", 100, 25),
        ("completion_loss_pp", "Base completion loss (percentage points)", 1, 2),
        ("goodput_retention", "On-time payload goodput retained (%)", 100, 95),
        ("p95_delay_increase_ms", "Increase in p95 packet delay (ms)", 1, 20),
    )
    scenarios = sorted({row["scenario"] for row in effects})
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.7), layout="constrained")
    for ax, (metric, label, scale, margin) in zip(axes.flat, measures):
        lookup = {row["scenario"]: row for row in effects
                  if row["arm"] == "adaptive" and row["metric"] == metric}
        for position, scenario in enumerate(scenarios):
            row = lookup.get(scenario)
            if not row or not row["ci95_low"]:
                ax.text(.02, position, "undefined", transform=ax.get_yaxis_transform(), fontsize=8)
                continue
            center = float(row["estimate"]) * scale
            low, high = (float(row[key]) * scale for key in ("ci95_low", "ci95_high"))
            ax.hlines(position, low, high, color=COLORS["adaptive"], linewidth=1.5)
            ax.plot(center, position, "o", color=COLORS["adaptive"], markersize=4)
        ax.axvline(100 if metric == "goodput_retention" else 0, color="#888888", lw=.7)
        if "fluctuate" in scenarios:
            ax.axvline(margin, color="#555555", linestyle="--", lw=.8)
        ax.set(yticks=range(len(scenarios)), yticklabels=scenarios, xlabel=label)
        ax.invert_yaxis()
        ax.grid(axis="y", alpha=.15)
    fig.suptitle("Adaptive versus 100 ms periodic feedback · paired effects and descriptive 95% intervals")
    save(fig, path)


def arm_figures(runs, out):
    names = sorted({row["scenario"] for row in runs})
    baselines = ["fixed", "aimd", "periodic", "adaptive", "heartbeat-only", "matched-budget", "no-delay-periodic"]
    metrics = (
        ("feedback_ip_bps", "Reverse feedback rate (kb/s)", .001),
        ("on_time_payload_goodput_bps", "On-time payload goodput (Mb/s)", 1e-6),
        ("base_completion_ratio", "On-time base completion (%)", 100),
    )
    colors = ("#6d6d6d", "#638434", "#214E78", "#AC4F0D", "#633b98", "#26847e", "#a54b73")
    fig, axes = plt.subplots(1, 3, figsize=(13, 5.5), layout="constrained", sharey=True)
    for ax, (key, label, scale) in zip(axes, metrics):
        for arm_index, arm in enumerate(baselines):
            points, y = [], []
            for scenario_index, name in enumerate(names):
                values = [float(row[key]) for row in runs if row["arm"] == arm
                          and row["scenario"] == name and row[key]]
                if values:
                    points.append(float(np.mean(values)) * scale)
                    y.append(scenario_index + (arm_index - 3) * .095)
            ax.scatter(points, y, color=colors[arm_index], s=12, label=arm)
        ax.set(yticks=range(len(names)), yticklabels=names, xlabel=label)
        ax.grid(axis="y", alpha=.2)
    axes[0].invert_yaxis()
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="outside upper center", ncols=4, frameon=False)
    save(fig, out / "arm-means.png")


def trace_panels(runs, out):
    for name in sorted({row["scenario"] for row in runs}):
        group = [row for row in runs if row["scenario"] == name]
        seed = min(int(row["seed"]) for row in group)
        fig, axes = plt.subplots(3, 1, figsize=(10, 6), sharex=True, layout="constrained")
        for row in group:
            if int(row["seed"]) != seed or row["arm"] not in COLORS:
                continue
            arm = row["arm"]
            color = COLORS[arm]
            with tarfile.open(evidence_path(row["archive"]), "r:gz") as archive:
                with io.TextIOWrapper(member(archive, "sender.csv")) as source:
                    rate = [event for event in csv.DictReader(source) if event["event"] == "rate_tick"]
                with io.TextIOWrapper(member(archive, "links.csv")) as source:
                    queue = [event for event in csv.DictReader(source)
                             if event["direction"] == "forward" and event["waiting_ip_bytes"]]
                with io.TextIOWrapper(member(archive, "receiver.csv")) as source:
                    feedback = [event for event in csv.DictReader(source) if event["event"] == "feedback_sent"]
                with member(archive, "scenario.json") as source:
                    scenario = json.load(source)
            rate_times = [int(event["elapsed_ns"]) / 1e9 for event in rate]
            axes[0].step(rate_times, [int(event["target_bps"]) / 1e6 for event in rate],
                         where="post", label=arm, color=color, lw=1.2)
            axes[1].step([int(event["elapsed_ns"]) / 1e9 for event in queue],
                         [int(event["waiting_ip_bytes"]) / 1000 for event in queue],
                         where="post", color=color, lw=.6, alpha=.72)
            axes[2].plot(rate_times, [float(event["report_age_ns"]) / 1e6 if event["report_age_ns"]
                                     else np.nan for event in rate], color=color, lw=.8)
            offset = .97 if arm == "periodic" else .89
            axes[2].plot([int(event["elapsed_ns"]) / 1e9 for event in feedback], [offset] * len(feedback),
                         "|", color=color, ms=3, alpha=.6, transform=axes[2].get_xaxis_transform())
        schedule = scenario["forward"]["capacity"]
        end_ms = scenario["warmupMs"] + scenario["measureMs"]
        axes[0].step([entry["atMs"] / 1000 for entry in schedule] + [end_ms / 1000],
                     [entry["bps"] / 1e6 for entry in schedule] + [schedule[-1]["bps"] / 1e6],
                     where="post", color="#555555", linestyle="--", lw=1, label="forward capacity")
        axes[0].legend(loc="best", ncols=3, frameon=False, fontsize=9)
        axes[0].set_ylabel("Rate (Mb/s)")
        axes[1].set_ylabel("Waiting FIFO (kB)")
        axes[2].set_ylabel("Sender report age (ms)")
        axes[2].set_xlabel("Simulated seconds · report emissions marked above age traces")
        axes[2].set_xlim(scenario["warmupMs"] / 1000, end_ms / 1000)
        for ax in axes:
            ax.grid(alpha=.17)
        fig.suptitle(f"{name} · seed {seed}")
        save(fig, out / f"{name}-trace.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    args = parser.parse_args()
    figures = args.analysis / "figures"
    figures.mkdir(exist_ok=True)
    runs = read_rows(args.analysis / "runs.csv")
    effects_figure(read_rows(args.analysis / "effects.csv"), figures / "paired-effects.png")
    arm_figures(runs, figures)
    trace_panels(runs, figures)


if __name__ == "__main__":
    main()
