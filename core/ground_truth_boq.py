"""Parses the 'Calculations' sheet of an estimator's BOQ workbook (the BC26122 layout) into BoqLine records.

Layout: a top section is text alone in column B ("A.CONCRETE"), a subsection is text alone in column B under it,
then a header row (text in C..G, nothing in B), line rows (description in B, numbers in C..G) and a "Total:" row in
column F. The same measured number is repeated under several subsections (slab area as concrete, mesh, EPS,
vapour barrier...), so measured_primitives() collapses those to what was actually measured off the drawing.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

import openpyxl


@dataclass
class BoqLine:
    section: str                 # "A.CONCRETE"
    sub: str                     # "Termite Protection"
    description: str
    unit: str                    # m2 | m3 | m | each | t
    quantity: Optional[float]    # the line's result, column G
    primary: Optional[float]     # the measured number in column C when its header says Area or Length
    primary_kind: str            # "area" | "length" | ""
    row: int
    values: dict = field(default_factory=dict)   # column header -> number, for the C..G columns of this line


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _unit(header):
    h = re.sub(r"[^a-z0-9()]", "", str(header or "").lower())
    if "(m3)" in h:
        return "m3"
    if "(m2)" in h:
        return "m2"
    if "(tons)" in h:
        return "t"
    if "(m)" in h:
        return "m"
    return "each"


def load_boq(path, sheet="Calculations"):
    ws = openpyxl.load_workbook(path, data_only=True)[sheet]
    lines, section, sub, header = [], "", "", {}
    for r in ws.iter_rows(min_row=1):
        cell = {c.column_letter: c.value for c in r if c.value not in (None, "")}
        if not cell:
            continue
        if set(cell) == {"B"}:
            text = str(cell["B"]).strip()
            if re.match(r"^[A-Z]\.[A-Z]", text) and text.upper() == text:
                section, sub = text, ""
            else:
                sub = text
            header = {}
            continue
        if "B" not in cell:
            if isinstance(cell.get("C"), str):                      # a column header row
                header = {k: str(v).strip() for k, v in cell.items()}
            continue                                                # a "Total:" row
        q, primary = _num(cell.get("G")), _num(cell.get("C"))
        ch = header.get("C", "").lower()
        kind = "area" if ch.startswith("area") else ("length" if ch.startswith("length") else "")
        values = {header.get(k, k): _num(cell.get(k)) for k in "CDEFG" if _num(cell.get(k)) is not None}
        lines.append(BoqLine(section, sub, str(cell["B"]).strip(), _unit(header.get("G")), q,
                             primary if kind else None, kind, r[0].row, values))
    return lines


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def measured_primitives(lines):
    """One entry per distinct measurement: the same description with the same measured number is counted once."""
    seen, out = set(), []
    for l in lines:
        if l.primary is None or l.primary <= 0:
            continue
        key = (_norm(l.description)[:40], round(l.primary, 2), l.primary_kind)
        if key not in seen:
            seen.add(key)
            out.append(l)
    return out


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        ls = load_boq(p)
        prim = measured_primitives(ls)
        print(p, len(ls), "lines,", len(prim), "distinct measurements")
        for l in prim:
            print(f"  {l.section[:18]:18} {l.primary_kind:6} {l.primary:9.2f}  {l.description[:70]}")
