"""Sharp page rendering shared by the web app's tile endpoint and the Streamlit viewer.

A PDF page that is one embedded raster can never be sharper than that raster, so every
render is capped at the page's own resolution (`native_z`).
"""
import fitz

from core.project import native_z

MAX_PX = 3800


def page_info(pdf_path, page_index=0):
    """(width_pt, height_pt, native px/pt, page count)"""
    doc = fitz.open(pdf_path)
    if not 0 <= page_index < doc.page_count:
        raise IndexError(f"page {page_index} is not in this PDF ({doc.page_count} pages)")
    p = doc[page_index]
    return p.rect.width, p.rect.height, native_z(p), doc.page_count


def render_crop(pdf_path, page_index, x, y, w, h, z, max_px=MAX_PX):
    """PNG bytes of a crop (PDF points) rendered at z px/pt, capped at the file's own resolution.
    Returns (png_bytes, z_used, capped_by_source)."""
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    cap = native_z(page)
    r = page.rect
    x, y = max(0.0, x), max(0.0, y)
    w, h = min(w, r.width - x), min(h, r.height - y)
    if w < 5 or h < 5:
        raise ValueError("crop is too small")
    z_used = max(1.0, min(z, cap))
    while max(w, h) * z_used > max_px:
        z_used *= 0.9
    pix = page.get_pixmap(matrix=fitz.Matrix(z_used, z_used), clip=fitz.Rect(x, y, x + w, y + h))
    return pix.tobytes("png"), z_used, z > cap * 1.02


def view_window(page_w, page_h, zoom, pan_x, pan_y):
    """Visible rectangle (PDF points) for a zoom >= 1 and pan positions in 0..100 percent."""
    vw, vh = page_w / zoom, page_h / zoom
    return (page_w - vw) * pan_x / 100, (page_h - vh) * pan_y / 100, vw, vh
