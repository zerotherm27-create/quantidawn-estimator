"""Classifies a plan-legend label into a structural element with parsed dimensions.

Handles both label styles seen so far: Westgarth ("450x450", "3030mm HEIGHT") and
Boxshall ("2700mm L x 2700mm W x 900mm D", "(1500mm H)", "200mm THIC").
"""
import re
from dataclasses import dataclass, field

MARK_RE = re.compile(
    r"^(?:[A-Z0-9 ]*-\s*)?((?:SF|PC|PF|CC|CB|BP|RP|SC|P)\d+[a-z]?)(?=[-\s]|$)", re.I)
LETTER_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm\s*([LWDH])\b")
DIM2_RE = re.compile(r"(\d{2,4})\s*[xX]\s*(\d{2,4})(?:\s*[dD])?")
DIM3_RE = re.compile(r"(\d{3,5})\s*[xX]\s*(\d{3,5})\s*[xX]\s*(\d{3,5})")
THICK_RE = re.compile(r"(\d{2,3})\s*mm\s*THIC")
PT_THICK_RE = re.compile(r"(\d{2,3})PT\b", re.I)
BARE_MM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm")
KG_M2_RE = re.compile(r"([\d.]+)\s*kg/m2", re.I)
HEIGHT_RE = re.compile(r"(\d{4})\s*mm\s*HEIGHT", re.I)


@dataclass
class Element:
    kind: str
    label: str
    quantity: float
    unit: str
    mark: str = ""
    dims: dict = field(default_factory=dict)   # metres


def parse_mark(label):
    m = MARK_RE.search(label)
    return m.group(1).upper() if m else ""


def _letters(label):
    out = {}
    for v, k in LETTER_DIM_RE.findall(label):
        out.setdefault(k.upper(), float(v) / 1000)
    return out


def _thickness(label, letters):
    for rx in (THICK_RE, PT_THICK_RE):
        m = rx.search(label)
        if m:
            return int(m.group(1)) / 1000
    if "D" in letters:
        return letters["D"]
    m = BARE_MM_RE.search(label)
    return float(m.group(1)) / 1000 if m else None


def classify(entry) -> Element:
    label, q, unit = entry["label"], float(entry["quantity"]), entry["unit"].upper()
    up = label.upper()
    el = Element("other", label, q, unit, parse_mark(label))
    lt = _letters(label)
    d = el.dims
    if "EDGE FORM" in up:
        el.kind = "edge_form"
        t = _thickness(label, lt)
        if t:
            d["thickness"] = t
    elif "SAWCUT" in up or "CONSTRUCTION JOINT" in up:
        el.kind = "joint"
    elif "BULK EXCAVATION" in up:
        el.kind = "bulk_excavation"
        if "D" in lt:
            d["depth"] = lt["D"]
    elif "BLINDING" in up and "SAND" not in up:
        el.kind = "other"            # blinding rule differs per project; reported for review
    elif "RAFT FOOTING" in up:
        el.kind = "raft_footing"
        if "D" in lt:
            d["depth"] = lt["D"]
    elif "STRIP" in up or "CAPPING BEAM" in up:
        el.kind = "strip_footing"
        if "W" in lt and "D" in lt:
            d.update(width=lt["W"], depth=lt["D"])
        elif DIM2_RE.search(label):
            a, b = DIM2_RE.search(label).groups()
            d.update(width=int(a) / 1000, depth=int(b) / 1000)
    elif el.mark[:2] in ("PC", "PF") and "FOOTING" in up:
        el.kind = "pad_footing"
        if {"L", "W", "D"} <= set(lt):
            d.update(length=lt["L"], width=lt["W"], depth=lt["D"])
        elif DIM3_RE.search(label):
            a, b, c = DIM3_RE.search(label).groups()
            d.update(length=int(a) / 1000, width=int(b) / 1000, depth=int(c) / 1000)
    elif re.search(r"SAND (BLINDING|BEDDING)", up) and "THICK" not in up and "MESH" not in up:
        el.kind = "sand_blinding"
        d["thickness"] = _thickness(label, lt) or 0
    elif ("POLYTHENE" in up or "POLTYHENE" in up) and "THICK" not in up and "MESH" not in up:
        el.kind = "poly_membrane"
    elif "RAMP SLAB" in up and "EDGE" not in up:
        el.kind = "ramp_slab"
        t = _thickness(label, lt)
        if t:
            d["thickness"] = t
    elif "SLAB ON GROUND" in up:
        el.kind = "slab_on_ground"
        m = BARE_MM_RE.search(label)          # first size is the slab; later ones are bedding/cover
        if m:
            d["thickness"] = float(m.group(1)) / 1000
    elif "PT SLAB" in up or re.search(r"\d+PT SLAB", up):
        el.kind = "pt_slab"
        k = KG_M2_RE.search(label)
        if k:
            d["kg_m2"] = float(k.group(1))
    elif "COLUMN" in up or el.mark[:2] == "CC":
        el.kind = "column"
        if {"L", "W"} <= set(lt):
            d.update(length=lt["L"], width=lt["W"])
        elif DIM2_RE.search(label):
            a, b = DIM2_RE.search(label).groups()
            d.update(length=int(a) / 1000, width=int(b) / 1000)
        h = HEIGHT_RE.search(label)
        if h:
            d["height"] = int(h.group(1)) / 1000
        elif "H" in lt:
            d["height"] = lt["H"]
    elif "WALL" in up and THICK_RE.search(label):
        el.kind = "wall"
        d["thickness"] = int(THICK_RE.search(label).group(1)) / 1000
        if "H" in lt:
            d["height"] = lt["H"]
    elif "HOB" in up:
        el.kind = "hob"
        t = re.search(r"(\d{2,3})\s*THICK\s*x\s*(\d{2,3})", label, re.I)
        if t:
            d.update(width=int(t.group(1)) / 1000, height=int(t.group(2)) / 1000)
    return el
