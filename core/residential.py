"""Residential takeoff rules: turns a marked-up legend of a house or duplex into takeoff lines grouped by trade.

The structural rules (core/takeoff_builder.py) know footings and slabs. A house legend is roofing, brick, linings, doors,
windows, tiles and paint, and each legend row is one measured quantity: a length, an area or a count. A rule says which
trade the row belongs to, how its dimensions are read from the label, and which further quantities follow from the same
measurement (a slab area also gives concrete volume, mesh, EPS and vapour barrier).

Every line carries the inputs of its formula (core/calc.py), so a re-measured length updates the lines that depend on it.

The per-unit allowances below (bricks per m2, blocks per m2 ...) are the ones in the BC26122 Clinton duplex BOQ, the only
residential job the app has seen. They are estimating conventions, not facts of the drawing: confirm them for your own jobs.
Rows with no rule are returned in `skipped` with a reason instead of being guessed.
"""
import re

from core.calc import n_of, qty
from core.models import TakeoffLine

ALLOWANCE = {
    "bricks_per_m2": 400 / 7,           # 4148.4 bricks on 72.597 m2
    "blocks_per_m2": 12.5,              # 192.525 blocks on 15.402 m2
    "core_fill_m3_per_m2": 0.04,        # 0.61608 m3 on 15.402 m2
    "block_reo_t_per_m2": 0.019,        # 0.2926 t on 15.402 m2
    "rib_m3_per_m": 0.2,                # waffle-pod ribs and edge beams, 0.2 m3 per m of rib
    "n12_kg_per_m": 0.888,
    "termite_edge_form_h": 0.385,       # edge formwork under the termite barrier, 385 mm
    "batten_screws_per_m": 1 / 0.6,     # one screw each 600 mm of roof batten
    "jamb_stud_h": 2.44,                # jamb studs are not given a length in the legend
}

CONCRETE, MASONRY, STEEL, ROOFING = "CONCRETE", "MASONRY", "STRUCTURAL STEEL", "METAL ROOFING"
GLAZING, DOORS, BLINDS = "ALUMINIUM DOORS, WINDOWS AND GLAZING", "DOORS AND DOOR HARDWARE", "WINDOW FURNISHING"
FLOORS, TILING, WATERPROOF = "FLOOR COVERINGS", "WALL AND FLOOR TILING", "WATERPROOFING"
LININGS, CLADDING, PAINT = "INTERNAL LININGS", "EXTERNAL CLADDING", "INTERNAL AND EXTERNAL PAINT"
JOINERY = "JOINERY AND BENCHTOPS"

NUM = r"(\d+(?:\.\d+)?)"
AREA_IN_LABEL = re.compile(NUM + r"\s*M2", re.I)
OPENINGS = re.compile(r"LESS\s+" + NUM + r"\s*M2\s*OPENINGS", re.I)
HEIGHT = re.compile(r"(\d{3,4})\s*(?:MM)?\s*H\b", re.I)
WIDTH_MM = re.compile(r"(\d{2,4})\s*MM\s*W\b", re.I)
LENGTH_MM = re.compile(r"(\d{3,4})\s*MM\s*L\b", re.I)
DASH_DIMS = re.compile(r"(\d{3,4})\s*-\s*(\d{3,4})(?!\d)")
X_DIMS = re.compile(r"(\d{3,4})\s*[xX]\s*(\d{3,4})")
THICK = re.compile(r"(\d{2,3})\s*MM")
EACH_IN_LABEL = re.compile(r"=\s*" + NUM + r"\s*EACH", re.I)


class Skip(Exception):
    """A row the rules cannot turn into lines; the message says why."""


def _m(rx, label):
    m = rx.search(label)
    return float(m.group(1)) if m else None


def _label_area(label):
    text = OPENINGS.sub("", label)
    a = _m(AREA_IN_LABEL, text)
    if a is None:
        raise Skip("no area written in the label")
    return a


class Ctx:
    """One legend row, and the lines being built from it."""

    def __init__(self, entry, index):
        self.label = entry["label"].strip()
        self.q = float(entry["quantity"])
        self.unit = entry["unit"].upper().replace(".", "").strip()
        self.up = self.label.upper()
        self.index, self.lines, self.measure = index, [], "res_area"

    def need(self, *units):
        if self.unit not in units:
            raise Skip(f"expected {' or '.join(units)}, the legend gives {self.unit or 'no unit'}")

    def add(self, group, what, unit, calc, mark, **n):
        """One takeoff line. n holds the formula's inputs (L W H N A K O R); the quantity is computed from them."""
        t = TakeoffLine(group=group, mark=mark, description=f"{self.label} - {what}" if what else self.label, unit=unit,
                        kind=self.measure, element=self.index, calc=calc, length=n.get("L"), width=n.get("W"),
                        height=n.get("H"), area=n.get("A"), k=n.get("K"), openings=n.get("O"), factor=n.get("R"),
                        count=n.get("N"))
        value = qty(calc, n_of(t))
        if unit == "m2":
            t.area = value
        elif unit == "m3":
            t.volume = value
        elif unit == "m":
            t.length = value if calc in ("each",) else t.length if t.length is not None else value
        else:                                   # each, tons: the result is the count
            t.count = value
        self.lines.append(t)
        return t


# ---------------------------------------------------------------- the rules, one function per legend family
def slab(c):
    c.need("EA", "SQ M")
    area, t = _label_area(c.label), float(THICK.search(c.up).group(1)) / 1000
    mesh = re.search(r"\((SL\d+)\s*MESH\)", c.up)
    c.measure = "res_area"
    c.add(CONCRETE, f"{t * 1000:.0f}mm slab pour", "m2", "plan", "SLAB", A=area)
    c.add(CONCRETE, "concrete volume", "m3", "prism", "SLAB", A=area, H=t)
    c.add(CONCRETE, f"reinforcement {mesh.group(1) + ' mesh' if mesh else 'mesh'}", "m2", "plan", "MESH", A=area)
    c.add(CONCRETE, "expanded polystyrene (EPS)", "m2", "plan", "EPS", A=area)
    c.add(CONCRETE, "vapour barrier", "m2", "plan", "VAPOUR BARRIER", A=area)


def slab_reo(c):
    c.need("M")
    bars = _m(re.compile(r"\bX\s*(\d+)\b", re.I), c.label) or 1
    c.measure = "res_len"
    c.add(CONCRETE, f"N12 x {bars:.0f} bars, tonnes", "tons", "bar", "SLAB REO", A=c.q * bars, K=ALLOWANCE["n12_kg_per_m"])
    c.add(CONCRETE, "waffle-pod ribs and edge beams, concrete", "m3", "edge", "RIBS", L=c.q, H=ALLOWANCE["rib_m3_per_m"])


def termite(c):
    c.need("M")
    c.measure = "res_len"
    c.add(CONCRETE, "", "m", "len", "TERMITE", L=c.q)
    c.add(CONCRETE, f"{ALLOWANCE['termite_edge_form_h'] * 1000:.0f}mm slab edge formwork", "m2", "edge", "EDGE FORM",
          L=c.q, H=ALLOWANCE["termite_edge_form_h"])


def edge_form(c):
    c.need("M")
    t = float(THICK.search(c.up).group(1)) / 1000
    c.measure = "res_len"
    c.add(CONCRETE, "", "m2", "edge", "EDGE FORM", L=c.q, H=t)


def blockwork(c):
    c.need("M")
    h = _m(HEIGHT, c.label) / 1000
    c.measure = "res_len"
    c.add(MASONRY, "area", "m2", "edge", "BLOCKWORK", L=c.q, H=h)
    c.add(MASONRY, "blocks", "each", "edge", "BLOCKS", L=c.q, H=h, R=ALLOWANCE["blocks_per_m2"])
    c.add(MASONRY, "core fill", "m3", "edge", "CORE FILL", L=c.q, H=h, R=ALLOWANCE["core_fill_m3_per_m2"])
    c.add(MASONRY, "reinforcement, tonnes", "tons", "edge", "BLOCK REO", L=c.q, H=h, R=ALLOWANCE["block_reo_t_per_m2"])


def brickwork(c):
    c.need("M")
    h, o = _m(HEIGHT, c.label) / 1000, _m(OPENINGS, c.label)
    c.measure = "res_len"
    c.add(MASONRY, "area", "m2", "wall", "BRICKWORK", L=c.q, H=h, O=o)
    c.add(MASONRY, "bricks", "each", "wall", "BRICKS", L=c.q, H=h, O=o, R=ALLOWANCE["bricks_per_m2"])
    c.add(MASONRY, "brick cleaning", "each", "wall", "BRICK CLEAN", L=c.q, H=h, O=o, R=ALLOWANCE["bricks_per_m2"])


def length(group, mark):
    def rule(c):
        c.need("M")
        c.measure = "res_len"
        c.add(group, "", "m", "len", mark, L=c.q)
    return rule


def wall_frame(c):
    c.need("M")
    h, o = _m(HEIGHT, c.label) / 1000, _m(OPENINGS, c.label)
    c.measure = "res_len"
    c.add(STEEL, "", "m2", "wall", "WALL FRAME", L=c.q, H=h, O=o)


def area_row(group, mark, extra=()):
    """A row measured as an area. extra: more (group, what, mark) lines that take the same area."""
    def rule(c):
        c.need("SQ M")
        c.measure = "res_area"
        c.add(group, "", "m2", "plan", mark, A=c.q)
        for g, what, m in extra:
            c.add(g, what, "m2", "plan", m, A=c.q)
    return rule


def area_in_label(group, mark, extra=()):
    """A row whose quantity column is a count of 1 and whose area is written in the label ("... = 409m2", "- 343m2")."""
    def rule(c):
        c.need("EA", "SQ M")
        a = _label_area(c.label)
        c.measure = "res_area"
        c.add(group, "", "m2", "plan", mark, A=a)
        for g, what, m in extra:
            c.add(g, what, "m2", "plan", m, A=a)
    return rule


def edge_row(group, mark, extra=(), height=None):
    """A length that is a wall, lining or tile run: area = length x height read from the label."""
    def rule(c):
        c.need("M")
        h = height if height is not None else (_m(HEIGHT, c.label) or 0) / 1000
        if not h:
            raise Skip("no height in the label")
        c.measure = "res_len"
        c.add(group, "", "m2", "edge", mark, L=c.q, H=h)
        for g, what, m in extra:
            c.add(g, what, "m2", "edge", m, L=c.q, H=h)
    return rule


def soffit(c):
    c.need("M")
    w = _m(WIDTH_MM, c.label)
    if not w:
        raise Skip("no width in the label")
    c.measure = "res_len"
    c.add(PAINT, "", "m2", "edge", "SOFFIT", L=c.q, H=w / 1000)


def counted(group, mark, per=None):
    """A row that is a count. per: a length each item has (posts, jamb studs), which makes the line a length."""
    def rule(c):
        c.need("EA")
        n = _m(EACH_IN_LABEL, c.label) or c.q
        h = per(c) if per else None
        c.measure = "res_count"
        if h is None:
            c.add(group, "", "each", "each", mark, N=n)
        else:
            c.add(group, f"{n:g} x {h:g} m", "m", "each", mark, N=n, H=h)
    return rule


def post(c):
    h = _m(HEIGHT, c.label)
    if not h:
        raise Skip("no height in the label")
    counted(STEEL, "POST", per=lambda _: h / 1000)(c)


def brace(c):
    h = _m(LENGTH_MM, c.label)
    if not h:
        raise Skip("no length in the label")
    counted(STEEL, "BRACING", per=lambda _: h / 1000)(c)


def downpipe(c):
    h = _m(LENGTH_MM, c.label)
    if not h:
        raise Skip("no length in the label")
    counted(ROOFING, "DOWNPIPE", per=lambda _: h / 1000)(c)


def opening(group, mark, extra=()):
    """A window or door row: area = width x height x count, read from "1800-1800" or "870 x 2100"."""
    def rule(c):
        c.need("EA")
        m = DASH_DIMS.search(c.label) or X_DIMS.search(c.label)
        if not m:
            raise Skip("no size in the label")
        a, b = sorted((int(m.group(1)) / 1000, int(m.group(2)) / 1000), reverse=True)
        c.measure = "res_count"
        c.add(group, f"{a * 1000:.0f} x {b * 1000:.0f}", "m2", "plan", mark, L=a, W=b, N=c.q)
        for g, what, mk in extra:
            c.add(g, what, "m2", "plan", mk, L=a, W=b, N=c.q)
    return rule


def joinery(c):
    c.need("EA")
    c.measure = "res_count"
    c.add(JOINERY, "", "each", "each", "JOINERY", N=c.q)


RULES = [
    ("slab", re.compile(r"^\d+MM SLAB SUPPLY AND POUR"), slab),
    ("slab reo", re.compile(r"GROUND SLAB REO"), slab_reo),
    ("termite", re.compile(r"TERMITE PROTECTION"), termite),
    ("edge form", re.compile(r"EDGE FORM"), edge_form),
    ("blockwork", re.compile(r"BLOCKWORKS?\b"), blockwork),
    ("brickwork", re.compile(r"BRICKWORKS?\b"), brickwork),
    ("brick sill", re.compile(r"BRICK SILL"), length(MASONRY, "BRICK SILL")),
    ("gable frame", re.compile(r"GABLE WALL FRAME"), area_row(STEEL, "GABLE FRAME", [(CLADDING, "gable cladding", "GABLE CLAD")])),
    ("wall frame", re.compile(r"WALL FRAME"), wall_frame),
    ("wall bracing", re.compile(r"WALL BRACING"), brace),
    ("plywood bracing", re.compile(r"STRUCTURAL PLYWOOD"), edge_row(STEEL, "BRACING")),
    ("roof trusses", re.compile(r"ROOF TRUSSES"), area_in_label(STEEL, "TRUSSES")),
    ("ceiling battens", re.compile(r"CEILING BATTENS"), area_row(STEEL, "CEILING BATTENS")),
    ("roof battens", re.compile(r"ROOF BATTENS"), None),                      # set below: a length plus its screws
    ("post in pier", re.compile(r"POST INSIDE PIER"), post),
    ("beams and lintels", re.compile(r"ROOF BEAM|HEADERS|LINTELS"), length(STEEL, "BEAM")),
    ("jamb studs", re.compile(r"JAMB STUDS"), counted(STEEL, "JAMB STUDS", per=lambda c: ALLOWANCE["jamb_stud_h"])),
    ("steel accessories", re.compile(r"TIE-?DOWNS|BEAM TO BEAM BOLTS|DYNABOLTS|POST TO SHOE BOLTS|HEAVY DUTY STIRRUPS|\bCRANE\b"),
     counted(STEEL, "ACCESSORIES")),
    ("roof sheeting", re.compile(r"METAL ROOF SHEETING"), area_in_label(ROOFING, "ROOF SHEETING")),
    ("roof blanket", re.compile(r"ROOF BLANKET"), area_in_label(ROOFING, "ROOF BLANKET")),
    ("roof trim", re.compile(r"CAPPINGS?/FLASHINGS?|VALLEY BOARD|FASCIA|BARGES"), length(ROOFING, "ROOF TRIM")),
    ("downpipe", re.compile(r"DOWNPIPE"), downpipe),
    ("window", re.compile(r"SLIDING WINDOW"), opening(GLAZING, "WINDOW", [(BLINDS, "blockout roller blind", "BLIND")])),
    ("sliding door", re.compile(r"SLIDING DOOR/"), opening(GLAZING, "SLIDING DOOR")),
    ("screen and mirror", re.compile(r"SHOWER SCREEN|VANITY MIRROR"), edge_row(GLAZING, "GLASS")),
    ("threshold", re.compile(r"DOOR THRESHOLD"), length(GLAZING, "THRESHOLD")),
    ("panel lift door", re.compile(r"PANEL LIFT"), opening(DOORS, "GARAGE DOOR")),
    ("doors", re.compile(r"SOLID CORE DOOR|HOLLOW CORE DOOR|CAVITY SLIDING DOOR"), opening(DOORS, "DOOR")),
    ("exposed aggregate", re.compile(r"EXPOSED AGGREGATE"),
     area_row(FLOORS, "AGGREGATE", [(FLOORS, "mechanical grinding", "GRINDING")])),
    ("carpet", re.compile(r"FLOOR\s*-\s*CARPET"),
     area_row(FLOORS, "CARPET", [(FLOORS, "floor levelling compound", "LEVELLING"), (FLOORS, "moisture mitigation", "MOISTURE")])),
    ("wet area floor", re.compile(r"WET AREA FLOOR"), area_row(TILING, "FLOOR TILES", [(WATERPROOF, "floor waterproofing", "WATERPROOF")])),
    ("floor tiles", re.compile(r"FLOOR\s*-\s*TILED"), area_row(TILING, "FLOOR TILES")),
    ("skirting and trims", re.compile(r"SKIRTINGS|TRANSITION TRIMS"), length(FLOORS, "TRIM")),
    ("wall tiles", re.compile(r"WALL TILES|SPLASHBACK TILED"),
     edge_row(TILING, "WALL TILES", [(WATERPROOF, "waterproofing", "WATERPROOF")])),
    ("wall linings", re.compile(r"WALL LININGS"), edge_row(LININGS, "WALL LINING")),
    ("wall insulation", re.compile(r"WALL-?\s*INSULATION"), edge_row(LININGS, "INSULATION")),
    ("ceiling linings", re.compile(r"CEILING LININGS"), area_row(LININGS, "CEILING LINING")),
    ("plaster ceiling", re.compile(r"PLASTERING AND SANDING CEILING"), area_row(LININGS, "PLASTER")),
    ("plaster walls", re.compile(r"PLASTERING AND SANDING WALLS"), area_in_label(LININGS, "PLASTER")),
    ("cornice", re.compile(r"\d+MM CORNICE"), length(LININGS, "CORNICE")),
    ("soffit paint", re.compile(r"SOFFIT PAINT"), soffit),
    ("ceiling paint", re.compile(r"CEILING PAINT FINISH"), area_row(PAINT, "CEILING PAINT")),
    ("gable paint", re.compile(r"GABLE CLADDING PAINT"), area_row(PAINT, "GABLE PAINT")),
    ("trim paint", re.compile(r"BEADS AND CORNICE PAINT|CORNICE PAINT"), length(PAINT, "TRIM PAINT")),
    ("wall paint", re.compile(r"WALL PAINT FINISH"), area_in_label(PAINT, "WALL PAINT")),
    ("door paint", re.compile(r"DOOR PAINT FINISH"), area_in_label(PAINT, "DOOR PAINT")),
    ("shelving", re.compile(r"WIP SHELVING|WIR CUPBOARD"), area_row(JOINERY, "SHELVING")),
    ("joinery", re.compile(r"BENCHTOP|CUPBOARD|PANTRY|ISLAND|SLIDERS?\b|INSTALL JOINERY|WHITEGOODS"), joinery),
]


def batten(c):
    c.need("M")
    c.measure = "res_len"
    c.add(STEEL, "", "m", "len", "ROOF BATTENS", L=c.q)
    c.add(STEEL, "type 17 batten screws", "each", "len", "SCREWS", L=c.q, R=ALLOWANCE["batten_screws_per_m"])


RULES = [(n, rx, batten if f is None else f) for n, rx, f in RULES]


def build_residential(entries, profile):
    """(lines, skipped): the same shape as core.takeoff_builder.build_takeoff. A legend row can give several lines."""
    lines, skipped = [], []
    for i, e in enumerate(entries):
        c = Ctx(e, i)
        for _name, rx, rule in RULES:
            if rx.search(c.up):
                try:
                    rule(c)
                except Skip as why:
                    skipped.append((c.label, str(why)))
                except (AttributeError, TypeError, ValueError):     # a label the rule expected a number in and did not have
                    skipped.append((c.label, "could not read its dimensions from the label"))
                else:
                    lines.extend(c.lines)
                break
        else:
            skipped.append((c.label, f"no residential rule ({c.unit or 'no unit'})"))
    order = list(profile.groups)
    lines.sort(key=lambda l: order.index(l.group))
    return lines, skipped
