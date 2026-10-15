"""One place that defines how a line's quantity follows from its dimensions.

The web UI re-implements exactly these formulas so a drawn measurement can update every line
that depends on it; tests/test_calc.py proves both agree with the pipeline's own numbers.
n = {L, W, H, N, A, K, O, R}: length, width, height/depth/thickness, count, plan area, kg per m2 (kg per m for bars),
openings m2, per-unit factor.
"""


def n_of(line):
    return {"L": line.length, "W": line.width, "H": line.height,
            "N": line.count if line.calc not in ("pt", "bar") and line.count is not None else 1,
            "A": line.area if line.length is None and line.area is not None else None,
            "K": line.k, "O": line.openings, "R": line.factor}


def base(n):
    if n.get("A") is not None:
        return n["A"]
    return (n.get("L") or 0) * (n.get("W") or 0) * (n.get("N") or 1)


def qty(calc, n):
    if calc == "prism":
        return base(n) * (n.get("H") or 0)
    if calc == "plan":
        return base(n)
    r = n.get("R") if n.get("R") is not None else 1
    if calc == "edge":                      # a length times a height: wall linings, tiles, edge formwork
        return (n.get("L") or 0) * (n.get("H") or 0) * r
    if calc == "wall":                      # the same less the openings in it: brickwork, wall frames
        return ((n.get("L") or 0) * (n.get("H") or 0) - (n.get("O") or 0)) * r
    if calc == "face2":
        return (n.get("L") or 0) * 2 * (n.get("H") or 0)
    if calc == "len":
        return (n.get("L") or 0) * r
    if calc == "each":                      # a count, or a count of things each H long (posts, downpipes)
        return (n.get("N") if n.get("N") is not None else 1) * (n["H"] if n.get("H") is not None else 1)
    if calc in ("pt", "bar"):               # area x kg/m2, or total bar length x kg/m
        return (n.get("A") or 0) * (n.get("K") or 0) / 1000
    raise ValueError(calc)


MEASURE_OF = {  # how an element is measured on the plan
    "res_len": "len", "res_area": "area", "res_count": "count",
    "strip_footing": "len", "markup_len": "len", "item_len": "len", "item_area": "area", "item_count": "count", "item_wall": "len", "item_vol": "area", "wall": "len", "hob": "len", "edge_form": "len", "joint": "len",
    "pad_footing": "count", "column": "count",
    "raft_footing": "area", "bulk_excavation": "area", "slab_on_ground": "area", "ramp_slab": "area",
    "sand_blinding": "area", "poly_membrane": "area", "pt_slab": "area",
}


def measure_type(line):
    if line.kind == "pad_footing" and line.area is not None and line.length is None:
        return "area"
    return MEASURE_OF.get(line.kind, "area")
