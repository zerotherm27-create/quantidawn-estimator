"""Reads the legend/quantity box off one marked-up plan page with Claude vision.

Uses the HTTP API directly (no extra packages) so it runs on the hosted app. It only reports what is printed on the
page; the entries then go through the same rules pipeline as the sample projects, and a person verifies every line.
"""
import base64
import json
import math
import os

import fitz
import requests

MODEL = os.environ.get("QD_AI_MODEL", "claude-sonnet-4-6")
MAX_IMAGE_BYTES = 4_500_000         # the API accepts up to 5 MB per image
MAX_ENTRIES = 200

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


class ReaderError(Exception):
    """A problem the user can act on (no key, no credit, unreadable answer)."""


def page_image(pdf_path, index, long_edge=2600):
    doc = fitz.open(pdf_path)
    try:
        page = doc[index]
        z = long_edge / max(page.rect.width, page.rect.height)
        while True:
            data = page.get_pixmap(matrix=fitz.Matrix(z, z)).tobytes("jpeg", jpg_quality=85)
            if len(data) <= MAX_IMAGE_BYTES or z < 0.3:
                return data
            z *= 0.8
    finally:
        doc.close()


def clean_entries(raw):
    out = []
    for e in (raw or [])[:MAX_ENTRIES]:
        try:
            label, qty, unit = str(e["label"]).strip()[:300], float(e["quantity"]), str(e.get("unit", "")).strip()[:12]
        except (KeyError, TypeError, ValueError):
            continue
        if label and math.isfinite(qty) and qty >= 0:
            out.append({"label": label, "quantity": qty, "unit": unit})
    return out


def read_page_entries(pdf_path, index):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ReaderError("Reading markups is not set up on this server: ANTHROPIC_API_KEY is missing.")
    body = {"model": MODEL, "max_tokens": 4000, "system": SYSTEM,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(page_image(pdf_path, index)).decode()}},
                {"type": "text", "text": "Extract the legend entries from this page."}]}]}
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", json=body, timeout=55,
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    except requests.RequestException:
        raise ReaderError("Could not reach the AI service. Try again.")
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = ""
        if "credit balance" in msg.lower():
            raise ReaderError("The AI account is out of credit. Add credit at console.anthropic.com, then try again.")
        if r.status_code in (401, 403):
            raise ReaderError("The AI key was rejected. Check ANTHROPIC_API_KEY.")
        if r.status_code == 429:
            raise ReaderError("The AI service is busy. Wait a moment and try again.")
        raise ReaderError("The AI service could not read this sheet (error %d)." % r.status_code)
    text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    try:
        data = json.loads(text.replace("```json", "").replace("```", "").strip())
    except ValueError:
        raise ReaderError("The AI answer could not be understood. Try this sheet again.")
    return {"sheet": str(data.get("sheet", ""))[:60], "entries": clean_entries(data.get("entries"))}
