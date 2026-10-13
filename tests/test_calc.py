import json
from core.calc import n_of, qty
from core.takeoff_builder import PROFILES, build_takeoff

bad = total = 0
for sample, prof in (("82-westgarth", "westgarth"), ("boxshall-brighton", "boxshall")):
    pages = json.load(open(f"samples/{sample}/entries_manual.json"))
    lines, _ = build_takeoff([e for p in pages for e in p["entries"]], PROFILES[prof])
    for l in lines:
        total += 1
        got, want = qty(l.calc, n_of(l)), l.quantity()
        if want is None or abs(got - want) > max(1e-6, 1e-6 * abs(want)):
            bad += 1
            print("MISMATCH", sample, l.group[:14], l.mark, l.calc, got, want)
print(f"{total - bad}/{total} lines: calc.qty equals the builder's quantity")
assert bad == 0
