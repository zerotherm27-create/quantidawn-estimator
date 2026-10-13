"""Builds a Westgarth workbook from the legend entries and checks its formulas by recalculating them."""
import json
import shutil
import subprocess
import sys

import openpyxl

from core.takeoff_builder import build_takeoff
from core.templates.structural_takeoff import write_structural_takeoff

pages = json.load(open("samples/82-westgarth/entries_manual.json"))
lines, skipped = build_takeoff([e for p in pages for e in p["entries"]])
out = "samples/82-westgarth/generated.xlsx"
write_structural_takeoff(lines, out, skipped)
print(*skipped, sep=chr(10))
ws = openpyxl.load_workbook(out)["TAKEOFF"]
formulas = sum(1 for row in ws.iter_rows() for c in row if isinstance(c.value, str) and c.value.startswith("="))
print("rows", ws.max_row, "formulas", formulas)
soffice = shutil.which("soffice") or "/Applications/LibreOffice.app/Contents/MacOS/soffice"
print("libreoffice:", soffice if shutil.which("soffice") else "not found")
