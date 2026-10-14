from core.ground_truth_boq import load_boq, measured_primitives

lines = load_boq("samples/22l524sp-clinton/expected.xlsx")
assert len(lines) > 300, len(lines)
assert {l.section for l in lines} >= {"A.CONCRETE", "B.MASONRY", "D.METAL ROOFING"}

slab = [l for l in lines if l.section == "A.CONCRETE" and l.sub == "Concrete Volume-Supply"]
assert [round(l.quantity, 3) for l in slab][:2] == [13.814, 13.038] and slab[0].unit == "m3"
assert slab[0].primary == 138.14 and slab[0].primary_kind == "area"

termite = [l for l in lines if l.sub == "Termite Protection"]
assert [l.quantity for l in termite] == [50.8, 42.22] and termite[0].unit == "m"

prim = measured_primitives(lines)
slab_areas = [l.primary for l in prim if l.section == "A.CONCRETE" and l.primary_kind == "area"]   # the two identical 41.14 garage lines collapse to one
assert len(slab_areas) == 5 and len(prim) < len(lines), (len(slab_areas), len(prim), len(lines))
print("ok", len(lines), "lines,", len(prim), "distinct measurements")
