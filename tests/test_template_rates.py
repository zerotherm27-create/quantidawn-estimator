"""Rates written into a client's own BOQ template: only the rate column, never a formula, with the source noted."""
import io
import openpyxl
from core.models import TakeoffLine
from core.template_filler import fill_template
from core.template_reader import read_template

D = "/Users/jojo/Downloads/"
for f in ("BC26137-Lot-801-Knoble-Road-Thurgoona-NSW-2640-Childcare-Centre-BOQ.xlsx",
          "BC26122-Hillclose-estate-Sienna-Built-BOQ-Rev-1.xlsx"):
    tpl = read_template(D + f)
    boq = [s for s in tpl.sheets if s.kind == "boq"][0]
    print(f[:14], "| rate column:", boq.columns.get("rate"), "| source column:", boq.columns.get("rate_source"))
    items = [i for i in boq.items if i.input_cells and len(i.input_cells) == 1][:6]
    # pretend our takeoff produced these rows, priced
    lines = [TakeoffLine(group=i.section, mark="", description=i.description, unit=i.unit, area=10.0, kind="x",
                         rate=12.5 + n, rate_source="Example price list entry", labour_hours=0.25 + n / 100, labour_rate=88.0) for n, i in enumerate(items)]
    out = "/private/tmp/claude-501/-Volumes-Jojo-SSD-QUANTIDAWN-AI-ESTIMATOR/290f1ae7-49c3-48e6-8dcf-2918a68d3afa/scratchpad/rates_out.xlsx"
    rep = fill_template(D + f, out, lines)
    wb_in, wb_out = openpyxl.load_workbook(D + f), openpyxl.load_workbook(out)
    ws_i, ws_o = wb_in["BOQ"], wb_out["BOQ"]
    g = boq.columns["rate"]
    wrote = [(m.item.row, ws_o[f"{g}{m.item.row}"].value) for m in rep["matches"]]
    # every formula in the template must be untouched
    changed = [c.coordinate for row in ws_i.iter_rows() for c in row
               if isinstance(c.value, str) and c.value.startswith("=") and ws_o[c.coordinate].value != c.value]
    print("   matched", rep["matched"], "| rates written", rep["rates_written"], "|", wrote[:3], "| formulas changed:", len(changed))
    src = boq.columns.get("rate_source")
    if src:
        print("   source note:", ws_o[f"{src}{rep['matches'][0].item.row}"].value)
    ic, jc = boq.columns.get("labour_hrs"), boq.columns.get("labour_rate")
    r0 = rep["matches"][0].item.row
    print("   labour hrs col", ic, "->", ws_o[f"{ic}{r0}"].value, "| labour rate col", jc, "->", ws_o[f"{jc}{r0}"].value, "| labour written", rep["labour_written"])
    assert ic and jc and rep["labour_written"] == rep["matched"]
    assert ws_o[f"{ic}{r0}"].value.startswith("=") and ws_o[f"{jc}{r0}"].value == 88.0
    assert rep["rates_written"] == rep["matched"] > 0 and not changed
    # pricing off -> no rate on the line -> nothing written
    for l in lines:
        l.rate = l.labour_hours = l.labour_rate = None
    rr = fill_template(D + f, out, lines)
    assert rr["rates_written"] == 0 and rr["labour_written"] == 0
print("rates only written when pricing is on; formulas untouched: OK")
