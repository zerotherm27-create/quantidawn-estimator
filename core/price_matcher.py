"""Optional pricing: match takeoff lines to a client's price list. Never guesses a rate silently.

Statuses
  matched   confident (score >= AUTO): the rate is applied
  suggested plausible (SUGGEST <= score < AUTO): shown, but a person must accept it
  none      nothing credible: the rate stays blank for manual entry
A price entry is only considered when its unit equals the line's unit.
"""
import csv
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

AUTO, SUGGEST = 0.8, 0.55
UNIT_ALIASES = {"m3": "m3", "m³": "m3", "cu m": "m3", "cum": "m3", "m2": "m2", "m²": "m2", "sqm": "m2", "sq m": "m2",
                "m": "m", "lm": "m", "lin m": "m", "t": "tons", "ton": "tons", "tons": "tons", "tonne": "tons",
                "ea": "each", "each": "each", "no": "each", "nr": "each", "item": "item", "ls": "item"}
COLS = {"description": ("description", "item", "item description", "name"),
        "category": ("category", "trade", "group", "section"),
        "unit": ("unit", "units", "uom"),
        "rate": ("rate", "unit rate", "price", "unit price", "material rate", "rate ($)", "rate (aud)"),
        "labour_hours": ("labour hours", "labour hrs", "labour (hrs)", "labor hours", "labor (hrs)", "hours per unit", "hrs per unit", "hrs/unit"),
        "labour_rate": ("labour rate", "labour rate ($)", "labor rate", "labour $/hr", "labour rate per hour")}
# Work types, in priority order. A line may only take a rate of the same (or a compatible) work type:
# an excavation rate can never price concrete.
WORK_TYPES = [("blinding", ("blinding",)), ("polythene", ("polythene", "membrane", "vapour")),
              ("sand", ("sand", "bedding")), ("excavation", ("excavat", "earthwork")),
              ("formwork", ("formwork", "form ", "edge form")), ("post tension", ("post tension", "post-tension")),
              ("reinforcement", ("reinforc", "mesh", "rebar", "steel")), ("precast", ("precast",)),
              ("pile", ("pile", "spoil")), ("concrete", ("concrete", "slab", "supply"))]
COMPATIBLE = {"concrete": {"concrete", "precast"}, "precast": {"precast", "concrete"}}
KIND_WORK = {"poly_membrane": "polythene", "sand_blinding": "sand", "edge_form": "formwork", "joint": "concrete",
             "bulk_excavation": "excavation", "pt_slab": "post tension"}


@dataclass
class PriceEntry:
    description: str
    category: str
    unit: str
    rate: float
    labour_hours: Optional[float] = None     # hours per unit of quantity
    labour_rate: Optional[float] = None      # $ per hour


@dataclass
class PriceMatch:
    status: str                       # matched | suggested | none
    rate: Optional[float] = None
    entry: str = ""
    score: float = 0.0
    note: str = ""
    labour_hours: Optional[float] = None
    labour_rate: Optional[float] = None


def norm_unit(u):
    return UNIT_ALIASES.get(re.sub(r"\s+", " ", (u or "").strip().lower()), (u or "").strip().lower())


def _tok(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _header(row):
    cols = {}
    for i, c in enumerate(row):
        t = _tok(str(c))
        for role, names in COLS.items():
            if t in {_tok(n) for n in names} and role not in cols:
                cols[role] = i
    return cols


def load_price_list(path):
    """CSV or XLSX with description, unit, rate (and optionally category). Rows with no valid rate are skipped."""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
        rows = [[c.value for c in r] for r in ws.iter_rows()]
    else:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    for hi, row in enumerate(rows[:10]):
        cols = _header(row)
        if {"description", "unit", "rate"} <= set(cols):
            break
    else:
        raise ValueError("The price list needs columns for description, unit and rate.")
    out, skipped = [], 0
    for row in rows[hi + 1:]:
        if len(row) <= max(cols.values()):
            continue
        d, u, r = row[cols["description"]], row[cols["unit"]], _num(row[cols["rate"]])
        if not d or r is None or r < 0:
            skipped += 1
            continue
        def opt(key):
            v = _num(row[cols[key]]) if key in cols and cols[key] < len(row) else None
            return v if v is not None and v >= 0 else None
        out.append(PriceEntry(str(d).strip(), str(row[cols["category"]]).strip() if "category" in cols and row[cols["category"]] else "",
                              norm_unit(str(u)), r, opt("labour_hours"), opt("labour_rate")))
    if not out:
        raise ValueError("No usable rows found in the price list.")
    return out


def work_type(text):
    t = " " + (text or "").lower() + " "
    for name, words in WORK_TYPES:
        if any(w in t for w in words):
            return name
    return None


def line_work_type(line):
    kind = getattr(line, "kind", "") or ""
    return KIND_WORK.get(kind) or work_type(line.group)


def match_line(line, entries):
    """Best price for one TakeoffLine. Needs .group, .description, .unit (and .kind when known)."""
    u = norm_unit(line.unit)
    wt = line_work_type(line)
    ok_types = COMPATIBLE.get(wt, {wt})
    words = [w for w in _tok((getattr(line, "kind", "") or "").replace("_", " ")).split() if len(w) > 3]
    best, best_score = None, 0.0
    for e in entries:
        if e.unit != u:
            continue
        ewt = work_type(f"{e.category} {e.description}")
        if wt and ewt not in ok_types:
            continue                                            # wrong kind of work: never considered
        kw = (sum(w[:5] in _tok(e.description) for w in words) / len(words)) if words else 0.0
        desc = SequenceMatcher(None, _tok(line.description), _tok(e.description)).ratio()
        exact_type = 1.0 if ewt == wt else 0.0
        score = 0.45 + 0.1 * exact_type + 0.35 * max(kw, desc) + 0.1 * (1.0 if kw >= 0.99 else 0.0)
        if score > best_score:
            best, best_score = e, score
    if best is None:
        return PriceMatch("none", note="no price of the same kind of work and unit")
    status = "matched" if best_score >= AUTO else "suggested" if best_score >= SUGGEST else "none"
    if status == "none":
        return PriceMatch("none", None, "", round(best_score, 2), "nothing close enough")
    return PriceMatch(status, best.rate, best.description, round(best_score, 2),
                      "" if status == "matched" else "check this match before using the rate",
                      best.labour_hours, best.labour_rate)


def price_lines(lines, entries):
    return [match_line(l, entries) for l in lines]
