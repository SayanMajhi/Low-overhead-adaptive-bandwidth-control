"""Locate preserved evidence after moving or copying a project checkout."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def evidence_path(recorded):
    path = Path(recorded)
    value = path.as_posix()
    mapping = json.loads((ROOT / "experiments/layout-map.json").read_text())
    for previous, current in sorted(mapping.items(), key=lambda item: -len(item[0])):
        if value == previous or value.startswith(previous + "/"):
            return ROOT / (current + value[len(previous):])
        marker = "/" + previous
        index = value.find(marker)
        if path.is_absolute() and index >= 0:
            suffix = value[index + len(marker):]
            if not suffix or suffix.startswith("/"):
                return ROOT / (current + suffix)
    # New records also remain usable after the complete project is copied.
    for directory in ("experiments/results/", "config/profiles/"):
        marker = "/" + directory
        if path.is_absolute() and marker in value:
            return ROOT / (directory + value.split(marker, 1)[1])
    return path if path.is_absolute() else ROOT / path
