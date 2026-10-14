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


# A bare "1:100" can also be a fall or slope, so a ratio is the sheet's scale only with the word "scale" just before it.
SLOPE_WORDS = re.compile(r"fall|slope|pitch|grad|crossfall|falls to|drain", re.I)
PAPER_RES = [                                                   # strongest first
    re.compile(r"(?:page|paper|sheet|print|plot|original)\s*size\s*(?:[:\-\u2013=]|is)?\s*(A[0-4])\b", re.I),   # PAGE SIZE - A2
    re.compile(r"\b(A[0-4])\s*(?:size|sheet|paper)\b", re.I),                                                    # A1 SHEET
    re.compile(r"(?:at|@)\s*(A[0-4])\b", re.I),                                                                    # 1:100 at A1
    re.compile(r"\(\s*(A[0-4])\s*\)", re.I),                                                                      # 1:100 (A1)
]


def _ratios(text):
    """[(ratio, labelled, snippet)] for every ratio in the text, leaving out slopes and falls."""
    out = []
    for m in re.finditer(r"(?<![\d.:/])1\s*(?::|/|\bto\b)\s*(\d{1,4})(?!\d)", text or "", re.I):
        pre = text[max(0, m.start() - 20):m.start()]
        sep = m.group(0)[1:].strip()[:1]
        labelled = bool(re.search(r"scale", pre, re.I))
        if sep == "/" and not labelled:                         # "1/100" is only a scale when labelled ("1/2" is a fraction)
            continue
        if SLOPE_WORDS.search(pre[-16:]) and not labelled:
            continue
        r = int(m.group(1))
        if r < 2:
            continue
        k = pre.lower().rfind("scale")
        start = m.start() - len(pre) + k if labelled and k >= 0 else m.start()          # "Scale 1:100", not the words around it
        out.append((float(r), labelled, " ".join(text[start:m.end()].split())))
    return out


def find_scale(text, field=False):
    """What a sheet says about its scale -> {ratio, paper, labelled, others, snippet}, or None when it states no ratio.

    field=True: the text is already a scale field (a title-block cell), so every ratio in it counts as labelled.
    Otherwise (a whole page of text) a ratio counts as the sheet's scale only with the word 'scale' just before it;
    bare ones (could be a fall or a slope) are used only when nothing is labelled, and are flagged for a person.
    Several different scales on one sheet (plan 1:100, detail 1:20): the most frequent wins and the rest are listed."""
    found = _ratios(text)
    if field:
        found = [(r, True, sn) for r, _l, sn in found]
    if not found:
        return None
    labelled = [f for f in found if f[1]]
    pool = labelled or found
    counts = {}
    for r, _l, _sn in pool:
        counts[r] = counts.get(r, 0) + 1
    best = max(counts, key=lambda r: (counts[r], -[x[0] for x in pool].index(r)))        # most frequent, then first in reading order
    snippet = next(sn for r, _l, sn in pool if r == best)
    paper = None
    for rx in PAPER_RES:
        m = rx.search(text or "")
        if m:
            paper = m.group(1).upper()
            break
    return {"ratio": best, "paper": paper, "labelled": bool(labelled),
            "others": sorted({r for r in counts if r != best}), "snippet": snippet}


def parse_stated(text):
    """'1:100 at A1' / 'SCALE: 1:50 @ A3' -> (100, 'A1'); 'As indicated' -> (None, None)."""
    f = find_scale(text, field=True)
    return (f["ratio"], f["paper"]) if f else (None, None)


def snap(r):
    best = min(STANDARD, key=lambda s: abs(s - r) / s)
    return float(best) if abs(best - r) / best < 0.04 else r


def effective_scale(stated_text, w_pt, h_pt, field=True):
    """Scale of the page as rendered. field=True: stated_text is a scale cell; False: it is the whole page's text."""
    f = find_scale(stated_text, field)
    pdf_paper = paper_of(w_pt, h_pt)
    if f is None:
        return ScaleResult(None, "unknown", "no scale ratio found on the sheet", ["calibrate against a known dimension"])
    ratio, stated_paper = f["ratio"], f["paper"]
    where = "title block" if field else "sheet text"
    src = f"{where} '{f['snippet']}'" if not field else f"title block {stated_text.strip()}"
    notes = []
    if f["others"]:
        notes.append("the sheet also shows 1:" + ", 1:".join(f"{o:g}" for o in f["others"]) + " (details?): check this is the plan's scale")
    if not f["labelled"]:
        notes.append("this ratio is not labelled 'scale' (it could be a fall or slope)")
        return ScaleResult(ratio, "needs_check", src, notes)
    if not stated_paper:
        notes.append("the sheet does not say which sheet size the ratio is for")
        return ScaleResult(ratio, "needs_check", src, notes)
    if not pdf_paper:
        notes.append(f"PDF page is not a standard size ({w_pt * PT_TO_MM:.0f} x {h_pt * PT_TO_MM:.0f} mm)")
        return ScaleResult(ratio, "needs_check", src, notes)
    k = max(PAPER_MM[stated_paper]) / max(PAPER_MM[pdf_paper])
    eff = round(snap(ratio * k), 1)                      # 141.4, not 141.428571...
    if abs(k - 1) > 0.02:
        notes.append(f"sheet is {stated_paper} but this PDF is {pdf_paper}: drawn scale is {k:.2f}x smaller than stated")
        return ScaleResult(eff, "needs_check", f"{src}, PDF is {pdf_paper}", notes)
    return ScaleResult(eff, "needs_check" if f["others"] else "auto", f"{src}, PDF is {pdf_paper}", notes)


def implied_ratio(p1, p2, real_m):
    """Two clicked points on the page (PDF points) and the real distance in metres -> implied ratio."""
    d_pt = ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5
    return real_m * 1000 / (d_pt * PT_TO_MM)


def measure(p_a, p_b, ratio):
    """Real length in metres between two points on the page at a given ratio."""
    d_pt = ((p_a[0] - p_b[0]) ** 2 + (p_a[1] - p_b[1]) ** 2) ** 0.5
    return d_pt * PT_TO_MM * ratio / 1000
