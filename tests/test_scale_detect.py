"""Reading the scale a sheet states, in the wordings seen on real plan sets."""
from core.scale import effective_scale, find_scale

A3 = (1191, 842)          # a PDF page in points
cases = [
    # (text, field, expected ratio, expected paper)
    ("GROUND FLOOR PLAN\nScale 1:100\nPAGE SIZE - A2", False, 100, "A2"),                       # Clinton: scale under the drawing title
    ("SLAB PLAN\nScale 1:100\n note: porch slab 1:100 fall away from house", False, 100, None),   # a fall is not the scale
    ("SCALE: 1 : 50 @ A1", False, 50, "A1"),
    ("Scale 1/200 (A3)", False, 200, "A3"),
    ("scale 1 to 100", False, 100, None),
    ("1:100 at A1", True, 100, "A1"),                                                              # a title-block cell
    ("SCALE: at A1 1:100", True, 100, "A1"),
]
for text, field, ratio, paper in cases:
    f = find_scale(text, field)
    assert f and f["ratio"] == ratio and f["paper"] == paper, (text, f)
print("PASS wordings read")

assert find_scale("As indicated", True) is None and find_scale("DO NOT SCALE - use figured dimensions", False) is None
assert find_scale("Pitch 1:3 and fall 1:60, 10:30 am, 1/2 round", False) is None
print("PASS slopes, times, fractions and 'do not scale' are not scales")

# several scales: the most frequent is used and the others are flagged
f = find_scale("Scale 1:100  Scale 1:100  DETAIL A Scale 1:20", False)
assert f["ratio"] == 100 and f["others"] == [20.0]
r = effective_scale("Scale 1:100  DETAIL Scale 1:20", *A3, field=False)
assert r.status == "needs_check" and any("1:20" in n for n in r.notes), r
print("PASS several scales are flagged")

# a bare ratio with no 'scale' word is used only as a last resort and always flagged
r = effective_scale("plan 1:100", *A3, field=False)
assert r.ratio == 100 and r.status == "needs_check" and any("not labelled" in n for n in r.notes), r
print("PASS unlabelled ratio flagged")

# Clinton: stated 1:100 on an A2 sheet, but the PDF is A3, so the drawn scale is ~1:141 and a person checks it
r = effective_scale("GROUND FLOOR PLAN\nScale 1:100\nPAGE SIZE - A2", *A3, field=False)
assert abs(r.ratio - 141.4) < 0.5 and r.status == "needs_check", r
print("PASS A2 sheet in an A3 PDF ->", r.ratio)

# same paper size: reads as auto
r = effective_scale("Scale 1:100 at A3", *A3, field=False)
assert r.ratio == 100 and r.status == "auto", r
print("PASS same paper size -> auto")

assert effective_scale("nothing here", *A3, field=False).status == "unknown"
print("ALL PASSED")
