"""The exported pricing sheet must equal an independent calculation (materials + labour, then allowances)."""
import io, json, re
import openpyxl
from server import app
from core.project import sample_project

c = app.test_client(); lines = sample_project("boxshall")["lines"]
with open("samples/pricing/EXAMPLE_price_list_not_real_rates.csv", "rb") as f:
    j = c.post("/api/project/boxshall/prices", data={"prices": (f, "e.csv"), "lines": json.dumps(lines)}, content_type="multipart/form-data").json
by = {m["id"]: m for m in j["matches"]}
lab_default = 80.0
mat = lab = 0.0
for l in lines:
    m = by[l["id"]]
    if m["status"] == "matched":
        l["rate"] = m["rate"]; l["lhpu"] = m["lhpu"]; l["lrate"] = m["lrate"] if m["lrate"] is not None else (lab_default if m["lhpu"] is not None else None)
        mat += l["q"] * l["rate"]
        if l["lhpu"] is not None and l["lrate"] is not None:
            lab += l["q"] * l["lhpu"] * l["lrate"]
sub = mat + lab
exp_ex = sub * 1.25; exp_tot = exp_ex * 1.10
r = c.post("/api/project/boxshall/export", json={"lines": lines, "pricing": {"on": True, "pct": {"contingency": 10, "margin": 15, "gst": 10}}})
wb = openpyxl.load_workbook(io.BytesIO(r.data)); cache = {}
def val(sh, ref):
    k = (sh, ref)
    if k not in cache:
        v = wb[sh][ref].value
        cache[k] = ev(sh, v[1:]) if isinstance(v, str) and v.startswith("=") else (v if isinstance(v, (int, float)) else 0)
    return cache[k]
def ev(sh, f):
    def sm(m):
        s = (m.group(1) or sh).strip("'"); return str(sum(val(s, f"{m.group(2)}{r}") for r in range(int(m.group(3)), int(m.group(5)) + 1)))
    f = re.sub(r"SUM\((?:('?[^'!]+'?)!)?([A-Z]+)(\d+):([A-Z]+)(\d+)\)", sm, f)
    f = re.sub(r"(?:('?[A-Za-z ]+'?)!)?([A-Z]{1,2})(\d+)", lambda m: str(val((m.group(1) or sh).strip("'"), m.group(2) + m.group(3))), f)
    return eval(f)
ps = wb["Pricing summary"]
rows = {ps[f"A{i}"].value: [val("Pricing summary", f"{c}{i}") for c in "BCD"] for i in range(4, ps.max_row + 1)}
sb = rows["Subtotal"]
print(f"materials  expected {mat:12,.2f}  workbook {sb[0]:12,.2f}")
print(f"labour     expected {lab:12,.2f}  workbook {sb[1]:12,.2f}")
print(f"total incl GST expected {exp_tot:12,.2f}  workbook {rows['Total (incl. GST)'][2]:12,.2f}")
assert abs(sb[0] - mat) < .01 and abs(sb[1] - lab) < .01 and abs(rows['Total (incl. GST)'][2] - exp_tot) < .01, 'workbook totals differ from the independent calculation'
print("labour hours per line carried:", sum(1 for l in lines if l.get('lhpu') is not None), "lines")
