# Markup probe (Step 0), 2026-10-13

PyMuPDF 1.26.5 on the 7 marked-up PDFs in ~/Downloads:
Westgarth (11 pp), Orana (14), BC26104 (7), Hillclose (9), Bangholm (5), Boxshall (8), Knoble Rd (4).

Result for every file: **0 annotations, 0 characters of text layer.**
The markups are flattened (rasterised), so legends, quantities and polygons are pixels only.

Consequence: extraction must be vision-based (render page -> Claude vision to read the legend
rows "label ... quantity unit", plus OCR/vision on titles and schedules). The expected
takeoff .xlsx files are the ground truth for evaluation.

Env: .venv (Python 3.9.6) with pymupdf, openpyxl installed. MVP extracted to _mvp_extract/.
