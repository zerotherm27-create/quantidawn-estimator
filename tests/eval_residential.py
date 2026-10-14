"""Scores the residential rules against the estimator's BOQ: for each line the rules produce, is there a BOQ line of the same
unit with the same quantity? Uses the AI legend reading stored with the sample (so no API calls).

usage: python -m tests.eval_residential samples/22l524sp-clinton [--show]
"""
import json
import sys
from pathlib import Path

from core.ground_truth_boq import load_boq
from core.takeoff_builder import PROFILES, build_takeoff

UNIT = {"m2": "m2", "m3": "m3", "m": "m", "each": "each", "tons": "t"}


def close(a, b):
    return abs(a - b) <= max(0.06, 0.02 * abs(b))


def main(sample, show=False):
    sample = Path(sample)
    boq = [l for l in load_boq(sample / "expected.xlsx") if l.quantity is not None]
    cache = sample / "ai_entries_zoomed.json"
    entries = [e for p in json.loads(cache.read_text()) for e in p["entries"]]
    lines, skipped = build_takeoff(entries, PROFILES["residential"])
    hit, miss, by_group = 0, [], {}
    for l in lines:
        u, q = UNIT[l.unit], l.quantity()
        cands = [b for b in boq if b.unit == u]
        ok = any(close(q, b.quantity) for b in cands)
        g = by_group.setdefault(l.group, [0, 0])
        g[1] += 1
        if ok:
            hit += 1
            g[0] += 1
        else:
            near = sorted({round(b.quantity, 3) for b in cands}, key=lambda v: abs(v - q))[:3]
            miss.append((l.group, l.description[:70], u, round(q, 3), near))
    # chance level: the same test with every quantity shifted by 9-37%, so a match can only be luck
    chance = []
    for k in (0.83, 0.87, 0.91, 1.09, 1.13, 1.19, 1.27, 1.37):
        chance.append(sum(1 for l in lines if any(close(l.quantity() * k, b.quantity) for b in boq if b.unit == UNIT[l.unit])))
    print(f"{len(entries)} legend rows -> {len(lines)} lines, {len(skipped)} rows skipped")
    print(f"chance level (quantities shifted so only luck can match): {min(chance)}-{max(chance)} of {len(lines)}")
    print(f"{hit} of {len(lines)} lines have a BOQ line of the same unit and quantity ({100 * hit / len(lines):.0f}%)")
    for g, (a, n) in by_group.items():
        print(f"  {g[:42]:42} {a}/{n}")
    if show:
        for m in miss:
            print("  NO MATCH", m)
    return hit, len(lines), miss


if __name__ == "__main__":
    main(sys.argv[1], "--show" in sys.argv)
