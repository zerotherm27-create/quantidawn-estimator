"""Scores the AI legend reading of a marked-up PDF against the estimator's BOQ workbook (Calculations layout).

usage: python -m tests.eval_boq samples/22l524sp-clinton            # uses ai_entries.json if present
       python -m tests.eval_boq samples/22l524sp-clinton --read     # reads the legend with the AI first (API calls, costs money)

An entry agrees when its quantity equals a measured number on the matching BOQ line (area, length, total length,
volume, result) or a quantity written in its own label ("... = 409m2"). Anything else is listed so a person can look
at the sheet: the BOQ can be right, the AI can have misread a digit, or the BOQ may convert units (count x length).
"""
import json
import re
import sys
from pathlib import Path

from core.ground_truth_boq import load_boq

MEASURED = ("area", "length", "lenght", "total", "volume", "qty", "quantity")
LABEL_QTY = re.compile(r"(\d+(?:\.\d+)?)\s*m2", re.I)      # "... = 409m2", "... - 343m2"


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def close(a, b):
    return abs(a - b) <= max(0.06, 0.02 * abs(b))


def candidates(lines):
    out = set()
    for l in lines:
        for v in (l.primary, l.quantity):
            if v:
                out.add(round(v, 3))
        for head, v in l.values.items():
            if head.strip().lower().startswith(MEASURED):
                out.add(round(v, 3))
    return out


def find_lines(label, boq):
    n = norm(label)
    exact = [l for l in boq if norm(l.description)[:22] == n[:22]]
    return exact or [l for l in boq if n[:14] and (n[:14] in norm(l.description) or norm(l.description)[:14] in n)]


def score(entries, boq):
    """-> list of (status, entry, candidates) with status agree | differs | no_boq_line."""
    out = []
    for e in entries:
        lines = find_lines(e["label"], boq)
        if not lines:
            out.append(("no_boq_line", e, set()))
            continue
        cands = candidates(lines)
        label_q = [float(m) for m in LABEL_QTY.findall(e["label"])]
        ok = any(close(e["quantity"], c) for c in cands) or any(close(q, c) for q in label_q for c in cands)
        out.append(("agree" if ok else "differs", e, cands))
    return out


def main(sample, read=False):
    sample = Path(sample)
    boq = load_boq(sample / "expected.xlsx")
    cache = sample / "ai_entries.json"
    if read or not cache.exists():
        from core import reader
        meta = json.loads((sample / "meta.json").read_text())
        pages = []
        for i, sh in enumerate(meta["sheets"]):
            try:
                res = reader.read_page_entries(str(sample / "marked_up.pdf"), i)
                pages.append({"page": i + 1, "sheet": sh["n"], "entries": res["entries"]})
            except reader.ReaderError as exc:
                pages.append({"page": i + 1, "sheet": sh["n"], "error": str(exc), "entries": []})
        cache.write_text(json.dumps(pages, indent=1))
    pages = json.loads(cache.read_text())
    tally = {"agree": 0, "differs": 0, "no_boq_line": 0}
    for p in pages:
        if p.get("error"):
            print(f"page {p['page']} (sheet {p['sheet']}): NOT READ - {p['error']}")
        rows = score(p["entries"], boq)
        for status, e, cands in rows:
            tally[status] += 1
            if status != "agree":
                near = sorted(cands, key=lambda c: abs(c - e["quantity"]))[:3]
                print(f"page {p['page']} (sheet {p['sheet']}) {status}: {e['quantity']} {e['unit']}  {e['label'][:60]}  BOQ: {near}")
    n = sum(tally.values())
    print(f"\n{n} entries read | agree {tally['agree']} | differs {tally['differs']} | no BOQ line {tally['no_boq_line']}")
    return tally


if __name__ == "__main__":
    main(sys.argv[1], "--read" in sys.argv)
