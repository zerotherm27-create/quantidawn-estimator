import json
from core.price_matcher import load_price_list, price_lines
from core.takeoff_builder import PROFILES, build_takeoff

entries = load_price_list("samples/pricing/EXAMPLE_price_list_not_real_rates.csv")
print(len(entries), "price entries (the 'EXAMPLE ONLY' note row is skipped)")
for sample, prof in (("boxshall-brighton", "boxshall"), ("82-westgarth", "westgarth")):
    pages = json.load(open(f"samples/{sample}/entries_manual.json"))
    lines, _ = build_takeoff([e for p in pages for e in p["entries"]], PROFILES[prof])
    res = price_lines(lines, entries)
    counts = {s: sum(r.status == s for r in res) for s in ("matched", "suggested", "none")}
    print(f"\n{sample}: {len(lines)} lines -> {counts}")
    seen = set()
    for l, r in zip(lines, res):
        key = (l.group[:22], l.kind, r.status)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {r.status:9} {l.group[:22]:22} {l.kind[:16]:16} {l.unit:5} -> {r.entry[:38]:38} {r.rate} ({r.score})")
# safety: a unit with no price must never get a rate
from core.models import TakeoffLine
odd = TakeoffLine("CONCRETE", "X", "Mystery item", "kg")
assert price_lines([odd], entries)[0].status == "none" and price_lines([odd], entries)[0].rate is None
print("\nno-price-for-this-unit stays blank: OK")
