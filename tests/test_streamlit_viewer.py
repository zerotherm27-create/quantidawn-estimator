"""Streamlit viewer: render logic at the PDF's own resolution, and a headless smoke run of app.py."""
import io
import fitz
from core.render import page_info, render_crop, view_window

pdfs = {"boxshall": "samples/boxshall-brighton/marked_up.pdf",
        "bangholm": "/Users/jojo/Downloads/BC26140-Turnout-Rooms-Bangholm-Training-Camp-Marked-Up.pdf"}
for name, pdf in pdfs.items():
    pw, ph, cap, n = page_info(pdf, 0)
    for zoom in (1.0, 4.0, 12.0):
        x, y, w, h = view_window(pw, ph, zoom, 50, 50)
        png, z, capped = render_crop(pdf, 0, x, y, w, h, 1600 / w)
        px = fitz.Pixmap(io.BytesIO(png))
        print(f"{name:9} zoom {zoom:>4}x  view {w:6.0f}x{h:4.0f} pt  -> {px.width}x{px.height} px  z={z:.2f} (file allows {cap:.2f})  capped_by_source={capped}")
    assert view_window(pw, ph, 1.0, 0, 0) == (0, 0, pw, ph)

from streamlit.testing.v1 import AppTest
at = AppTest.from_file("app.py", default_timeout=60).run()
print("app boots with no upload:", not at.exception, "| warning shown:", [w.value for w in at.warning][:1])
