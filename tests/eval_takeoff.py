"""Scores extracted legend entries against the estimator's finished takeoff.

usage: python -m tests.eval_takeoff samples/82-westgarth [--cache entries.json]
"""
import json
import re
import sys
from pathlib import Path

from core.ground_truth import load_takeoff
from core.legend import parse_mark
from core.markup_extract import extract_pdf
from core.takeoff_builder import PROFILES, build_takeoff


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def candidates(line):
    return [v for v in (line.length, line.area, line.count, line.volume) if v]


def close(a, b):
    # legends print one decimal, so allow rounding slack as well as 2%
    return abs(a - b) <= max(0.06, 0.02 * abs(b))


def find_truth(label, truth):
    """Match on the element mark (SF4, PC1, CC7) first, else on label text."""
    mark = parse_mark(label)
    if mark:
        hits = [t for t in truth if parse_mark(t.description) == mark]
        if hits:
            return hits
    return [t for t in truth if norm(t.description)[:25] == norm(label)[:25]]


def score(entries, truth):
    matched, rows = 0, []
    for e in entries:
        hits = find_truth(e["label"], truth)
        ok = any(close(e["quantity"], c) for t in hits for c in candidates(t))
        matched += ok
        rows.append((ok, e["label"][:60], e["quantity"], e["unit"], bool(hits)))
    return matched, rows


def score_derived(built, truth):
    """Per group: how many derived lines equal the estimator's own line."""
    out = {}
    for b in built:
        hits = [t for t in find_truth(b.description, truth) if t.group == b.group]
        q = b.quantity()
        ok = q is not None and any(t.quantity() is not None and close(q, t.quantity()) for t in hits)
        g = out.setdefault(b.group, [0, 0, []])
        g[1] += 1
        g[0] += ok
        if not ok:
            g[2].append((b.mark, round(q or 0, 2), [round(t.quantity() or 0, 2) for t in hits]))
    return out


def main(sample, cache=None):
    sample = Path(sample)
    truth = load_takeoff(sample / "expected.xlsx")
    if cache and Path(cache).exists():
        pages = json.loads(Path(cache).read_text())
    else:
        pages = extract_pdf(str(sample / "marked_up.pdf"))
        if cache:
            Path(cache).write_text(json.dumps(pages, indent=1))
    entries = [e for p in pages for e in p["entries"]]
    matched, rows = score(entries, truth)
    for ok, label, q, u, found in rows:
        print("OK  " if ok else ("QTY " if found else "MISS"), label, q, u)
    print(f"{matched}/{len(entries)} legend entries match the estimator's takeoff "
          f"({len(truth)} takeoff lines)")
    built, skipped = build_takeoff(entries, PROFILES[sample.name.split('-')[0] if sample.name.startswith('boxshall') else 'westgarth'])
    print(f"\nDerived {len(built)} takeoff lines, skipped {len(skipped)} legend entries")
    for g, (ok, n, bad) in score_derived(built, truth).items():
        print(f"  {g}: {ok}/{n} lines equal the estimator's")
        for b in bad:
            print("      differs:", b)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else None)
