"""Sheet scale: work out the real scale of a rendered PDF page and say how sure we are.

Title blocks state the scale for the ORIGINAL sheet size ("1:100 at A1"). A PDF exported or
printed at a smaller paper size is scaled down, so the true scale differs. We compare the
PDF page size with the stated sheet size, then a human confirms against a known dimension.
"""
import re
from dataclasses import dataclass, field
from typing import Optional

PT_TO_MM = 25.4 / 72
PAPER_MM = {"A0": (841, 1189), "A1": (594, 841), "A2": (420, 594), "A3": (297, 420), "A4": (210, 297)}
STANDARD = [1, 2, 5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 750, 1000, 1250, 2000, 2500, 5000]


@dataclass
class ScaleResult:
    ratio: Optional[float]                 # real mm per paper mm on the PDF page as rendered, e.g. 200
    status: str                            # "auto" | "needs_check" | "unknown"
    source: str
    notes: list = field(default_factory=list)


def paper_of(w_pt, h_pt, tol=0.03):
    w, h = sorted((w_pt * PT_TO_MM, h_pt * PT_TO_MM))
    for name, (pw, ph) in PAPER_MM.items():
        if abs(w - pw) / pw < tol and abs(h - ph) / ph < tol:
            return name
    return None


def parse_stated(text):
    """'1:100 at A1' / 'SCALE: 1:50 @ A3' -> (100, 'A1'); 'As indicated' -> (None, None)."""
    m = re.search(r"1\s*:\s*(\d+)", text or "")
    ratio = float(m.group(1)) if m else None
    p = re.search(r"(?:at|@)\s*(A[0-4])", text or "", re.I)
    return ratio, (p.group(1).upper() if p else None)


def snap(r):
    best = min(STANDARD, key=lambda s: abs(s - r) / s)
    return float(best) if abs(best - r) / best < 0.04 else r


def effective_scale(stated_text, w_pt, h_pt):
    ratio, stated_paper = parse_stated(stated_text)
    pdf_paper = paper_of(w_pt, h_pt)
    if ratio is None:
        return ScaleResult(None, "unknown", "title block gives no ratio", ["calibrate against a known dimension"])
    notes = []
    if not stated_paper:
        notes.append("title block does not say which sheet size the ratio is for")
        return ScaleResult(ratio, "needs_check", f"title block {stated_text.strip()}", notes)
    if not pdf_paper:
        notes.append(f"PDF page is not a standard size ({w_pt * PT_TO_MM:.0f} x {h_pt * PT_TO_MM:.0f} mm)")
        return ScaleResult(ratio, "needs_check", f"title block {stated_text.strip()}", notes)
    k = max(PAPER_MM[stated_paper]) / max(PAPER_MM[pdf_paper])
    eff = snap(ratio * k)
    if abs(k - 1) > 0.02:
        notes.append(f"sheet is {stated_paper} but this PDF is {pdf_paper}: drawn scale is {k:.2f}x smaller than stated")
        return ScaleResult(eff, "needs_check", f"title block {stated_text.strip()}, PDF is {pdf_paper}", notes)
    return ScaleResult(eff, "auto", f"title block {stated_text.strip()}, PDF is {pdf_paper}", notes)


def implied_ratio(p1, p2, real_m):
    """Two clicked points on the page (PDF points) and the real distance in metres -> implied ratio."""
    d_pt = ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5
    return real_m * 1000 / (d_pt * PT_TO_MM)


def measure(p_a, p_b, ratio):
    """Real length in metres between two points on the page at a given ratio."""
    d_pt = ((p_a[0] - p_b[0]) ** 2 + (p_a[1] - p_b[1]) ** 2) ** 0.5
    return d_pt * PT_TO_MM * ratio / 1000
