"""Validate the exact tuning and held-out workload inventory."""
import json
from pathlib import Path

def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result

def bad_number(token):
    raise ValueError(f"noninteger number: {token}")

def intervals(rows, *columns):
    return [dict(zip(("fromMs", "untilMs", *columns), row, strict=True))
            for row in rows]

def link(queue, cap, delay, jitter, loss=0, burst=(0, 1_000_000),
         blackout=(), extra=()):
    return {
        "queueBytes": queue,
        "capacity": [dict(atMs=time, bps=rate) for time, rate in cap],
        "delayMs": delay, "jitterMs": jitter, "lossPpm": loss,
        "burstEnterPpm": burst[0], "burstExitPpm": burst[1],
        "blackouts": intervals(blackout),
        "extraDelay": intervals(extra, "addMs"),
    }

def fwd(cap=((0, 1_800_000),), delay=30, jitter=2, **options):
    return link(16_000, cap, delay, jitter, **options)

def rev(delay=30, jitter=1, **options):
    return link(4_000, ((0, 256_000),), delay, jitter, **options)

H = "held-out"
T = "tuning"
expected = {
    (H, "stable-high"): (fwd(), rev()),
    (H, "stable-low"): (fwd(((0, 950_000),)), rev()),
    (H, "downstep"): (fwd(((0, 1_800_000), (20_000, 950_000))), rev()),
    (H, "upstep"): (fwd(((0, 950_000), (20_000, 1_800_000))), rev()),
    (H, "fluctuate"): (fwd((
        (0, 1_800_000), (12_000, 950_000), (16_000, 1_500_000),
        (20_000, 1_000_000), (24_000, 1_800_000), (29_000, 800_000),
        (33_000, 1_400_000), (37_000, 1_800_000),
    )), rev()),
    (H, "delay-jitter"): (fwd(delay=85, jitter=20), rev(60, 15)),
    (H, "random-loss"): (fwd(loss=30_000), rev()),
    (H, "burst-loss"): (fwd(burst=(150, 20_000)), rev()),
    (H, "cross-traffic"): (fwd(), rev()),
    (H, "feedback-loss"): (fwd(), rev(
        loss=200_000, blackout=((18_000, 20_000), (29_000, 30_000)),
    )),
    (H, "feedback-delay"): (fwd(), rev(
        extra=((15_000, 25_000, 250), (30_000, 35_000, 250)),
    )),
    (T, "tune-stable-high"): (fwd(((0, 1_650_000),)), rev()),
    (T, "tune-stable-low"): (fwd(((0, 1_050_000),)), rev()),
    (T, "tune-variable"): (fwd((
        (0, 1_650_000), (14_000, 1_050_000),
        (22_000, 1_550_000), (31_000, 1_050_000),
    )), rev()),
    (T, "tune-feedback-delay-loss"): (fwd(), rev(
        loss=50_000, extra=((21_000, 23_000, 100),),
    )),
    (T, "tune-downstep"): (
        fwd(((0, 1_650_000), (18_000, 1_050_000)), jitter=1), rev(),
    ),
    (T, "tune-upstep"): (
        fwd(((0, 1_050_000), (18_000, 1_650_000)), jitter=1), rev(),
    ),
}

def check_types(value):
    if type(value) is dict:
        for child in value.values():
            check_types(child)
    elif type(value) is list:
        for child in value:
            check_types(child)
    else:
        assert type(value) in (str, int), value

root = Path("config/scenarios")
paths = sorted(root.rglob("*.json"))
assert {(path.parent.name, path.stem) for path in paths} == expected.keys()
assert len(paths) == len(expected) == len({path.stem for path in paths})
for path in paths:
    group, name = path.parent.name, path.stem
    actual = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys,
        parse_float=bad_number, parse_constant=bad_number,
    )
    check_types(actual)
    forward, reverse = expected[group, name]
    background = (
        intervals(((15_000, 24_000, 1000, 10), (29_000, 33_000, 1000, 10)),
                  "packetIpBytes", "intervalMs")
        if name == "cross-traffic" else []
    )
    assert actual == {
        "schemaVersion": 1, "scenarioId": name,
        "warmupMs": 10_000, "measureMs": 30_000, "drainMs": 2_000,
        "media": {
            "frameIntervalMs": 20, "deadlineMs": 150, "basePackets": 2,
            "enhancementPackets": 8, "payloadBytesPerPacket": 500,
        },
        "forward": forward, "reverse": reverse, "background": background,
    }, path
assert set(range(520101, 520111)).isdisjoint(range(630201, 630231))
print("PASS: 17 exact scenario objects (6 tuning, 11 held-out); seed ranges disjoint")
