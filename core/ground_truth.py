"""Parses an estimator's finished TAKEOFF workbook into TakeoffLine records.

Layout (Westgarth / Boxshall): header row in row 1, a group title is a row
with text only in column A, line rows have a description in B and a unit in C,
SUMMARY rows are skipped.
"""
import openpyxl

from core.models import TakeoffLine

COLS = {"D": "diameter", "E": "length", "F": "width", "G": "height",
        "H": "area", "I": "count", "J": "volume"}


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def load_takeoff(path, sheet="TAKEOFF"):
    ws = openpyxl.load_workbook(path, data_only=True)[sheet]
    lines, group = [], ""
    for row in ws.iter_rows(min_row=2):
        cell = {c.column_letter: c.value for c in row if c.value is not None}
        if not cell:
            continue
        if "B" not in cell or "C" not in cell:
            if "A" in cell and len(cell) == 1:
                group = str(cell["A"]).strip()
            continue
        fields = {name: _num(cell.get(col)) for col, name in COLS.items()}
        lines.append(TakeoffLine(
            group=group, mark=str(cell.get("A", "")).strip(),
            description=str(cell["B"]).strip(), unit=str(cell["C"]).strip(),
            **fields))
    return lines


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        ls = load_takeoff(p)
        groups = {}
        for l in ls:
            groups.setdefault(l.group, []).append(l)
        print(p, len(ls), "lines")
        for g, items in groups.items():
            print(f"  {g}: {len(items)} lines, total {sum(i.quantity() or 0 for i in items):.2f}")
