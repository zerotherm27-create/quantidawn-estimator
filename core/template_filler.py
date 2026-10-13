"""Fills a client's Excel template from TakeoffLines without breaking it.

Rules: formulas are never overwritten; only typed-input cells found by the template
reader are written; lines that match no template row go to an 'Unassigned' sheet;
template rows we cannot safely fill are reported, not guessed.
"""
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

import openpyxl

from core.legend import parse_mark
from core.template_reader import Item, read_template

SECTION_WORDS = {
    "excavat": "excavation", "polythene": "polythene", "sand": "sand", "blinding": "blinding",
    "concrete": "concrete", "precast": "precast", "reinforc": "reinforcement", "post": "post tension",
    "form": "formwork", "pile": "pile",
}
DIM_FIELDS = (("length", "length"), ("width", "width"), ("height", "height"), ("diameter", "diameter"),
              ("area", "area"), ("count", "count"))


def _sect(text):
    t = (text or "").lower()
    return {v for k, v in SECTION_WORDS.items() if k in t}


def _tok(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


@dataclass
class Match:
    item: Item
    line: object
    score: float
    why: str


def match_lines(items, lines, min_score=0.55):
    """Greedy best-first matching of takeoff lines to template rows (one line per row)."""
    cands = []
    for li, l in enumerate(lines):
        lm = parse_mark(l.description) or (l.mark if re.fullmatch(r"[A-Z]{1,3}\d+[a-z]?", l.mark or "") else "")
        for ii, it in enumerate(items):
            ls, ts = _sect(l.group), _sect(it.section)
            same_sec = (ls == ts and bool(ls)) or _tok(l.group) == _tok(it.section)
            if not same_sec and ls and ts:
                continue                                   # different work-item section: never match
            im = parse_mark(it.description)
            desc = SequenceMatcher(None, _tok(l.description), _tok(it.description)).ratio()
            score, why = desc * 0.7, "description"
            if lm and im and lm == im:
                score, why = 0.9 + 0.1 * desc, "mark " + lm
            elif lm and im and lm != im:
                continue
            if same_sec:
                score += 0.05
            if score >= min_score:
                cands.append((score, li, ii, why))
    cands.sort(reverse=True)
    used_l, used_i, out = set(), set(), []
    for score, li, ii, why in cands:
        if li in used_l or ii in used_i:
            continue
        used_l.add(li); used_i.add(ii)
        out.append(Match(items[ii], lines[li], round(score, 2), why))
    unmatched_lines = [l for i, l in enumerate(lines) if i not in used_l]
    unmatched_items = [it for i, it in enumerate(items) if i not in used_i]
    return out, unmatched_lines, unmatched_items


def fill_template(template_path, out_path, lines, only_empty=False):
    """Returns a report dict. Writes out_path; the template file is never modified."""
    tpl = read_template(template_path)
    wb = openpyxl.load_workbook(template_path)
    items = [i for i in tpl.line_items() if i.input_cells]
    matches, un_lines, un_items = match_lines(items, lines)
    roles = {s.name: s.columns for s in tpl.sheets}
    written, skipped, rates_written = 0, [], 0
    for m in matches:
        ws = wb[m.item.sheet]
        cols = roles[m.item.sheet]
        if "qty" not in cols:                              # takeoff sheet: write dimension columns
            for field, role in DIM_FIELDS:
                v, col = getattr(m.line, field), cols.get(role)
                if v is None or not col:
                    continue
                cell = ws[f"{col}{m.item.row}"]
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    continue
                if only_empty and cell.value not in (None, ""):
                    continue
                cell.value = round(v, 4)
                written += 1
        elif len(m.item.input_cells) == 1:                 # BOQ row fed by a single typed cell
            sh, addr = m.item.input_cells[0]
            q = m.line.quantity()
            if q is None:
                skipped.append((m.item.description, "line has no quantity"))
            else:
                wb[sh][addr].value = round(q, 4)
                written += 1
        else:
            skipped.append((m.item.description,
                            f"quantity is built from {len(m.item.input_cells)} input cells; needs dimensions mapped by hand"))
    # unit rates: only into a rate column the client already has, never over a formula, and only when pricing is on
    for m in matches:
        cols = roles[m.item.sheet]
        rate = getattr(m.line, "rate", None)
        if rate is None or "rate" not in cols or "qty" not in cols:
            continue
        ws = wb[m.item.sheet]
        cell = ws[f"{cols['rate']}{m.item.row}"]
        if isinstance(cell.value, str) and cell.value.startswith("="):
            continue
        cell.value = round(rate, 4)
        rates_written += 1
        src = cols.get("rate_source")
        if src and getattr(m.line, "rate_source", ""):
            ws[f"{src}{m.item.row}"] = m.line.rate_source
    # labour: hours = the row's quantity (with wastage when the template has it) x hours per unit
    labour_written = 0
    for m in matches:
        cols = roles[m.item.sheet]
        hpu = getattr(m.line, "labour_hours", None)
        lrate = getattr(m.line, "labour_rate", None)
        if "qty" not in cols:
            continue
        ws = wb[m.item.sheet]
        wrote = False
        hc = cols.get("labour_hrs")
        if hpu is not None and hc:
            cell = ws[f"{hc}{m.item.row}"]
            if not (isinstance(cell.value, str) and cell.value.startswith("=")):
                qcol = cols.get("qty_waste") or cols["qty"]
                cell.value = f"={qcol}{m.item.row}*{round(hpu, 4)}"
                wrote = True
        rc = cols.get("labour_rate")
        if lrate is not None and rc:
            cell = ws[f"{rc}{m.item.row}"]
            if not (isinstance(cell.value, str) and cell.value.startswith("=")):
                cell.value = round(lrate, 4)
                wrote = True
        labour_written += wrote
    if un_lines:
        ws = wb.create_sheet("Unassigned")
        ws.append(["Group", "Mark", "Description", "Unit", "Quantity", "Note"])
        for l in un_lines:
            ws.append([l.group, l.mark, l.description, l.unit, l.quantity(), "no matching row in template"])
    wb.save(out_path)
    return {"matched": len(matches), "cells_written": written, "rates_written": rates_written, "labour_written": labour_written, "unmatched_lines": len(un_lines),
            "unfilled_rows": len(un_items), "skipped": skipped, "matches": matches}
