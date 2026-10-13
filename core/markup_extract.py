"""Reads the legend/quantity boxes off flattened marked-up plan pages.

The marked-up PDFs have no text layer or annotations (see docs/markup_probe.md),
so each page is rendered to an image and sent to Claude vision.
"""
import base64
import json

import anthropic
import fitz

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

SYSTEM = (
    "You read construction plan pages that an estimator has marked up. Each page "
    "may have a legend box listing takeoff items, each with a colour swatch, a "
    "label and a measured quantity with unit (e.g. '47.3 SQ M', '86.8 M', '4.0 EA'). "
    "Respond with ONLY valid JSON: "
    '{"sheet": "drawing sheet number or title", "entries": [{"label": "full label '
    'text exactly as printed", "quantity": number, "unit": "SQ M|M|EA|M3|other"}]}. '
    "Only include entries you can read clearly. Never invent or estimate a quantity. "
    "If the page has no legend, return an empty entries list."
)


def render_page(pdf_path, page_index, zoom=2.5):
    doc = fitz.open(pdf_path)
    pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    return pix.tobytes("png")


def extract_page(client, png_bytes):
    resp = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=4000, system=SYSTEM,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(png_bytes).decode()}},
            {"type": "text", "text": "Extract the legend entries from this page."}]}])
    raw = "".join(b.text for b in resp.content if b.type == "text")
    return json.loads(raw.replace("```json", "").replace("```", "").strip())


def extract_pdf(pdf_path):
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set (add it to .env)")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    n = len(fitz.open(pdf_path))
    pages = []
    for i in range(n):
        data = extract_page(client, render_page(pdf_path, i))
        data["page"] = i + 1
        pages.append(data)
    return pages
