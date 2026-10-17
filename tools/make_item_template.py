"""Make a takeoff item template from the legends of finished jobs.

    python tools/make_item_template.py --id residential-house --name "Residential house" \\
        --description "..." --out core/item_templates/residential-house.json  <legend.json> [<legend.json> ...]

A legend file is what was read off an estimator's marked-up sheets: rows of {label, quantity, unit}, anywhere in the JSON. Each distinct label
becomes one item named as the estimator wrote it. The unit says how it is measured: EA is a count, M a length, SQ M an area. A length whose
label gives a wall height ("... 2550mm H ...") becomes a wall item with that height. Rows in any other unit are listed, not guessed.
Colours are left out on purpose: the app gives each item the next free colour when the template is loaded."""
import argparse
import json
import re
import sys
from pathlib import Path

UNIT_KIND = {"EA": "count", "EACH": "count", "NO": "count", "M": "len", "LM": "len", "SQ M": "area", "M2": "area", "SQM": "area"}
HEIGHT = re.compile(r"(\d{3,4})\s*mm\s*H\b", re.I)
TAG = re.compile(r"\b(U\d)\b", re.I)


def legend_rows(doc):
    """Every dict with a label, a quantity and a unit, wherever it sits in the JSON."""
    out = []

    def walk(x):
        if isinstance(x, dict):
            if "label" in x and "unit" in x and ("quantity" in x or "qty" in x):
                out.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(doc)
    return out


def build(paths):
    items, seen, skipped = [], set(), []
    for path in paths:
        for row in legend_rows(json.loads(Path(path).read_text())):
            label = re.sub(r"\s+", " ", str(row["label"])).strip()
            unit = str(row["unit"]).strip().upper()
            kind = UNIT_KIND.get(unit)
            if not label or kind is None:
                skipped.append((label[:60], unit))
                continue
            item = {"name": label, "kind": kind}
            h = HEIGHT.search(label)
            if kind == "len" and h:
                item["kind"], item["size"] = "wall", round(int(h.group(1)) / 1000, 3)
            tags = TAG.findall(label)
            if tags:
                item["tag"] = tags[-1].upper()
            key = (label.lower(), item["kind"])
            if key not in seen:
                seen.add(key)
                items.append(item)
    return items, skipped


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--id", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("legends", nargs="+")
    a = ap.parse_args(argv)
    items, skipped = build(a.legends)
    doc = {"name": a.name, "description": a.description, "source": ", ".join(sorted({Path(p).parent.name for p in a.legends})), "items": items}
    Path(a.out).write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    kinds = {}
    for i in items:
        kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    print(f"{a.out}: {len(items)} items {kinds}; skipped {len(skipped)} rows" + (f" {skipped[:5]}" if skipped else ""))


if __name__ == "__main__":
    sys.exit(main())
