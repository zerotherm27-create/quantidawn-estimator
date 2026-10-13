"""One place that defines how a line's quantity follows from its dimensions.

The web UI re-implements exactly these formulas so a drawn measurement can update every line
that depends on it; tests/test_calc.py proves both agree with the pipeline's own numbers.
n = {L, W, H, N, A, K}: length, width, height/depth/thickness, count, plan area, kg per m2.
"""


def n_of(line):
    return {"L": line.length, "W": line.width, "H": line.height,
            "N": line.count if line.calc != "pt" and line.count is not None else 1,
            "A": line.area if line.length is None and line.area is not None else None,
            "K": line.k}


def base(n):
    if n.get("A") is not None:
        return n["A"]
    return (n.get("L") or 0) * (n.get("W") or 0) * (n.get("N") or 1)


def qty(calc, n):
    if calc == "prism":
        return base(n) * (n.get("H") or 0)
    if calc == "plan":
        return base(n)
    if calc == "edge":
        return (n.get("L") or 0) * (n.get("H") or 0)
    if calc == "face2":
        return (n.get("L") or 0) * 2 * (n.get("H") or 0)
    if calc == "len":
        return n.get("L") or 0
    if calc == "pt":
        return (n.get("A") or 0) * (n.get("K") or 0) / 1000
    raise ValueError(calc)


MEASURE_OF = {  # how an element is measured on the plan
    "strip_footing": "len", "wall": "len", "hob": "len", "edge_form": "len", "joint": "len",
    "pad_footing": "count", "column": "count",
    "raft_footing": "area", "bulk_excavation": "area", "slab_on_ground": "area", "ramp_slab": "area",
    "sand_blinding": "area", "poly_membrane": "area", "pt_slab": "area",
}


def measure_type(line):
    if line.kind == "pad_footing" and line.area is not None and line.length is None:
        return "area"
    return MEASURE_OF.get(line.kind, "area")
