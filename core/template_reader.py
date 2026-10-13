"""Reads a client's Excel template: which sheets hold line items, which columns mean what,
and which cells are the real inputs (following BOQ -> Calculations formula chains).

Nothing here writes to the file; see core/template_filler.py for that.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

import openpyxl
from openpyxl.utils import get_column_letter, column_index_from_string

ROLES = {
    "description": ("description", "item description", "particulars", "work item"),
    "unit": ("unit", "units", "uom"),
    "qty": ("qty", "quantity", "takeoff", "qty (each)"),
    "wastage": ("wastage",),
    "qty_waste": ("qty with wastage",),
    "rate": ("rate", "material rate", "unit rate", "material rate ($)", "rate ($)", "unit rate ($)"),
    "rate_source": ("rate source / basis", "rate source", "rate basis"),
    "labour_hrs": ("labor (hrs)", "labour (hrs)", "labour hrs", "labour hours", "labor hours"),
    "labour_rate": ("labour rate ($)", "labor rate ($)", "labour rate", "labor rate"),
    "total": ("total", "amount", "total cost"),
    "length": ("length", "length (m.)"),
    "width": ("width", "width /thickness", "width /thickness(m.)", "thickness"),
    "height": ("height", "height (m.)", "depth"),
    "area": ("area", "area (m2)"),
    "count": ("count", "count (each)", "quantity (each)"),
    "volume": ("volume", "volume (m3)"),
}
UNITS = {"m", "m2", "m3", "m²", "m³", "sqm", "sq m", "lm", "each", "ea", "no", "nr", "item", "ls",
         "t", "ton", "tons", "kg", "hr", "hrs", "sum", "set", "day", "week"}
REF_RE = re.compile(r"^=\s*(?:'?([^'!]+)'?!)?\$?([A-Z]{1,3})\$?(\d+)\s*$")


@dataclass
class Item:
    sheet: str
    row: int
    no: str
    description: str
    unit: str
    qty_cell: Optional[str]            # address on this sheet
    qty_is_formula: bool
    input_cells: list = field(default_factory=list)   # [(sheet, address)] the estimator should type into
    section: str = ""


@dataclass
class SheetSpec:
    name: str
    kind: str                           # boq | takeoff | calculation | quotation | other
    header_row: Optional[int]
    columns: dict                       # role -> column letter
    items: list = field(default_factory=list)
    sections: list = field(default_factory=list)   # (row, title)


@dataclass
class Template:
    path: str
    sheets: list

    def line_items(self):
        return [i for s in self.sheets for i in s.items]


def _norm(v):
    return re.sub(r"\s+", " ", str(v or "")).strip().lower()


def _find_header(ws, max_row=25):
    best = (0, None, {})
    for r in range(1, min(ws.max_row, max_row) + 1):
        cols = {}
        for c in ws[r]:
            t = _norm(c.value)
            for role, names in ROLES.items():
                if t in names and role not in cols:
                    cols[role] = c.column_letter
        if len(cols) > best[0]:
            best = (len(cols), r, cols)
    return (best[1], best[2]) if best[0] >= 2 else (None, {})


CELL_RE = re.compile(r"(?:'?([A-Za-z0-9_ &]+)'?!)?\$?([A-Z]{1,3})\$?(\d+)(?::\$?([A-Z]{1,3})\$?(\d+))?")


def _resolve(wb_f, sheet, addr, depth=0, seen=None):
    """Return the typed-input cells [(sheet, addr)] that a quantity ultimately depends on.

    Single references are followed; formulas over several cells (=C7*E7, =SUM(C7:C12))
    contribute all the constant cells they read. Empty list when nothing typed is found.
    """
    seen = seen if seen is not None else set()
    if (sheet, addr) in seen or depth > 6:
        return []
    seen.add((sheet, addr))
    v = wb_f[sheet][addr].value
    if not (isinstance(v, str) and v.startswith("=")):
        return [(sheet, addr)]
    out = []
    if not CELL_RE.search(v[1:]):
        return [(sheet, addr)]          # typed arithmetic such as =451.5*2: the cell itself is the input
    for m in CELL_RE.finditer(v[1:]):
        sh = (m.group(1) or sheet).strip()
        if sh not in wb_f.sheetnames:
            continue
        c1, r1, c2, r2 = m.group(2), int(m.group(3)), m.group(4), m.group(5)
        if c2:
            cells = [f"{get_column_letter(c)}{r}" for c in range(column_index_from_string(c1), column_index_from_string(c2) + 1)
                     for r in range(r1, int(r2) + 1)][:60]
        else:
            cells = [f"{c1}{r1}"]
        for a in cells:
            out += _resolve(wb_f, sh, a, depth + 1, seen)
    return out


def _classify(ws, header_row, cols, n_items):
    if header_row is None:
        return "quotation" if ws.max_row < 60 and any(
            "quotation" in _norm(c.value) for r in ws.iter_rows(max_row=15) for c in r) else "other"
    if "qty" in cols and "unit" in cols and "description" in cols:
        return "boq"
    if "unit" in cols and "description" in cols and {"length", "width", "height"} & set(cols):
        return "takeoff"
    if {"length", "width"} <= set(cols) or "area" in cols:
        return "calculation"
    return "other"


def read_template(path):
    wb_f = openpyxl.load_workbook(path)                    # formulas
    wb_v = openpyxl.load_workbook(path, data_only=True)    # cached display values
    sheets = []
    for name in wb_f.sheetnames:
        wf, wv = wb_f[name], wb_v[name]
        hr, cols = _find_header(wf)
        spec = SheetSpec(name, "other", hr, cols)
        if hr and "unit" in cols and "description" in cols:
            section = ""
            dcol, ucol = cols["description"], cols["unit"]
            qcol = cols.get("qty") or cols.get("volume") or cols.get("area")
            for r in range(hr + 1, wf.max_row + 1):
                desc = wv[f"{dcol}{r}"].value or wf[f"{dcol}{r}"].value
                unit = _norm(wv[f"{ucol}{r}"].value)
                first = wv[f"A{r}"].value
                if unit in UNITS and desc:
                    q = f"{qcol}{r}" if qcol else None
                    qv = wf[q].value if q else None
                    isf = isinstance(qv, str) and qv.startswith("=")
                    inputs = _resolve(wb_f, name, q) if q else []
                    dims = [c for r_ in ("length", "width", "height", "count", "area", "diameter") if (c := cols.get(r_))]
                    if "qty" not in cols and dims:        # takeoff sheets: typed cells are the dimensions
                        inputs = [(name, f"{c}{r}") for c in dims
                                  if not (isinstance(wf[f"{c}{r}"].value, str) and str(wf[f"{c}{r}"].value).startswith("="))]
                    spec.items.append(Item(name, r, str(first or ""), str(desc).strip(), unit, q, isf, inputs, section))
                elif first and not unit and isinstance(first, str) and not _norm(first).startswith("subtotal"):
                    title = wv[f"A{r}"].value
                    if title:
                        section = str(title).strip()
                        spec.sections.append((r, section))
        spec.kind = _classify(wf, hr, cols, len(spec.items))
        sheets.append(spec)
    return Template(path, sheets)


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        t = read_template(p)
        print("=====", p.split("/")[-1])
        for s in t.sheets:
            traced = sum(1 for i in s.items if i.input_cells)
            print(f"  {s.name:32} {s.kind:12} header row {s.header_row}  cols {sorted(s.columns)}  "
                  f"items {len(s.items)}  inputs traced {traced}  sections {len(s.sections)}")
