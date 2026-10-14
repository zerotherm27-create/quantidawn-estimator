"""Reads the legend/quantity box off one marked-up plan page with Claude vision.

Uses the HTTP API directly (no extra packages) so it runs on the hosted app. It only reports what is printed on the
page; the entries then go through the same rules pipeline as the sample projects, and a person verifies every line.

A whole sheet is rendered small enough for one image, and legend quantities are tiny, so a full-page reading drops or
swaps digits (a 183.7 read as 193.7). The page is therefore read twice: once whole, which also says where the legend
is, then again with the legend cropped and zoomed. Where the two readings disagree the line is listed in "verify".
If anything in the second pass fails, the whole-page reading is returned as before.
"""
import base64
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor

import fitz
import requests

MODEL = os.environ.get("QD_AI_MODEL", "claude-sonnet-4-6")
MAX_IMAGE_BYTES = 4_500_000         # the API accepts up to 5 MB per image
MAX_ENTRIES = 200
TILE_LONG_EDGE = 3200               # pixels on the long side of a zoomed legend crop
TILE_MAX_ZOOM = 5.0
TILE_MAX_HEIGHT_PT = 250            # about 25 legend rows per image
TILE_OVERLAP_PT = 50
LOCATE_TRIES = 3                    # asks for the legend's position this often when the whole-page reading found no rows
WIDEN_LEFT = 0.25                   # how far the legend box is pushed left when its strips come back without labels
BOX_PAD = 0.04                      # the model's legend box is approximate: widen it by this share of the page
BOX_PAD_X = 0.05                    # (sideways too)
MIN_LEGEND_W = 0.45                 # the model's vertical extent has been reliable but its horizontal one is not (it can return
                                    # only the quantity column), and labels are long: never crop narrower than this, leftwards

SYSTEM = (
    "You read construction plan pages that an estimator has marked up. Each page "
    "may have a legend box listing takeoff items, each with a colour swatch, a "
    "label and a measured quantity with unit (e.g. '47.3 SQ M', '86.8 M', '4.0 EA'). "
    "Respond with ONLY valid JSON: "
    '{"sheet": "drawing sheet number or title", "entries": [{"label": "full label '
    'text exactly as printed", "quantity": number, "unit": "SQ M|M|EA|M3|other"}], '
    '"legend_box": [left, top, right, bottom]}. '
    "legend_box is where the whole legend sits on the image, as fractions of the image width "
    "and height from 0 to 1 (null if there is no legend). It must include the item labels on the left "
    "as well as the swatches and quantities on the right. "
    "A legend row has a colour swatch, a label and a measured quantity. Ignore general notes, the title block, "
    "dimensions and any other drawing text: they are not legend rows. "
    "Only include entries you can read clearly. Never invent or estimate a quantity. "
    "If the page has no legend, return an empty entries list."
)
LOCATE = (
    "Where is the estimator's legend on this page: the list of takeoff items, each with a colour swatch, a label and a "
    "measured quantity? The box must include the item labels at the left end of the rows as well as the quantities at "
    "the right end. Respond with ONLY valid JSON: "
    '{"legend_box": [left, top, right, bottom]} as fractions of the image width and height from 0 to 1, '
    'or {"legend_box": null} if there is no such list.'
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


def clip_image(pdf_path, index, clip):
    """A zoomed crop of one region of a page (clip in PDF points)."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[index]
        z = min(TILE_MAX_ZOOM, TILE_LONG_EDGE / max(clip.width, clip.height))
        while True:
            data = page.get_pixmap(matrix=fitz.Matrix(z, z), clip=clip).tobytes("jpeg", jpg_quality=88)
            if len(data) <= MAX_IMAGE_BYTES or z < 0.3:
                return data
            z *= 0.8
    finally:
        doc.close()


NUMBER_ONLY = re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:sq\.? ?m|m2|m3|lm|ea|each|m)?\s*$", re.I)


def split_entries(raw):
    """(entries, dropped): a "label" that is only a quantity ("112.0 M") means the crop lost the label column."""
    out, dropped = [], 0
    for e in (raw or [])[:MAX_ENTRIES]:
        try:
            label, qty, unit = str(e["label"]).strip()[:300], float(e["quantity"]), str(e.get("unit", "")).strip()[:12]
        except (KeyError, TypeError, ValueError):
            continue
        if not (label and math.isfinite(qty) and qty >= 0):
            continue
        if NUMBER_ONLY.match(label):
            dropped += 1
            continue
        out.append({"label": label, "quantity": qty, "unit": unit})
    return out, dropped


def clean_entries(raw):
    return split_entries(raw)[0]


def clean_box(raw):
    """[left, top, right, bottom] as 0..1 fractions, or None when it is missing or cannot be a legend."""
    try:
        x0, y0, x1, y1 = [float(v) for v in raw]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(1.0, x1), min(1.0, y1)
    if x1 - x0 < 0.05 or y1 - y0 < 0.02:
        return None
    return [x0, y0, x1, y1]


def legend_tiles(page_rect, box):
    """The legend region (a clean_box, padded) cut into overlapping horizontal strips of at most TILE_MAX_HEIGHT_PT."""
    w, h = page_rect.width, page_rect.height
    left = min(box[0], box[2] - MIN_LEGEND_W)
    x0, y0 = max(0.0, (left - BOX_PAD_X) * w), max(0.0, (box[1] - BOX_PAD) * h)
    x1, y1 = min(w, (box[2] + BOX_PAD_X) * w), min(h, (box[3] + BOX_PAD) * h)
    tiles, top = [], y0
    while True:
        bottom = min(y1, top + TILE_MAX_HEIGHT_PT)
        tiles.append(fitz.Rect(x0, top, x1, bottom))
        if bottom >= y1 - 1:
            return tiles
        top = bottom - TILE_OVERLAP_PT


def _key(label):
    return re.sub(r"[^a-z0-9]", "", label.lower())


def _same(a, b):
    """Two readings of one legend row: one label may have lost or gained trailing text."""
    return bool(a) and bool(b) and (a.startswith(b) or b.startswith(a))


def _differs(a, b):
    return abs(a - b) > max(0.06, 0.02 * abs(b))


def _in_label(label, q):
    """True when the label itself carries that number ("... - 343m2"): one reading took it from the label, not an error."""
    return any(not _differs(float(n), q) for n in re.findall(r"\d+(?:\.\d+)?", label))


def merge_readings(tile_lists, full_entries):
    """Combine the zoomed strips and the whole-page reading.

    Returns (entries, verify): the zoomed values win, a row only the whole-page reading found is kept, and every row
    where two readings gave different quantities is listed in verify with both numbers."""
    entries, verify = [], []
    for ents in tile_lists:
        for e in ents:
            k = _key(e["label"])
            hit = next((m for m in entries if _same(_key(m["label"]), k)), None)
            if hit is None:
                entries.append(dict(e))
            elif _differs(e["quantity"], hit["quantity"]) and not (_in_label(hit["label"], e["quantity"]) or _in_label(e["label"], hit["quantity"])):
                verify.append({"label": hit["label"], "readings": [hit["quantity"], e["quantity"]], "why": "strips disagree"})
    zoomed = len(entries)
    for e in full_entries:
        k = _key(e["label"])
        hit = next((m for m in entries[:zoomed] if _same(_key(m["label"]), k)), None)
        if hit is None:
            entries.append(dict(e))
        elif _differs(e["quantity"], hit["quantity"]) and not _in_label(hit["label"], e["quantity"]) and not _in_label(e["label"], hit["quantity"]):
            verify.append({"label": hit["label"], "readings": [hit["quantity"], e["quantity"]], "why": "whole page differs"})
    return entries[:MAX_ENTRIES], verify


def _post(system, image, text, max_tokens=4000):
    """One vision call; returns the parsed JSON object. Raises ReaderError with a message the user can act on."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ReaderError("Reading markups is not set up on this server: ANTHROPIC_API_KEY is missing.")
    body = {"model": MODEL, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.b64encode(image).decode()}},
                {"type": "text", "text": text}]}]}
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
    data = _json_object(text)
    if data is None:
        raise ReaderError("The AI answer could not be understood. Try this sheet again.")
    return data


def _json_object(text):
    """The first JSON object in a reply: the model sometimes writes a sentence before it or fences it."""
    text = text.replace("```json", "").replace("```", "")
    start = text.find("{")
    while start != -1:
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(data, dict):
            return data
        start = text.find("{", start + 1)
    return None


SCALE_SYSTEM = (
    "You transcribe text from construction plan sheets. Respond with ONLY valid JSON: "
    '{"scales": ["text exactly as printed, e.g. Scale 1:100"], "page_size": "text exactly as printed, e.g. PAGE SIZE - A2, or null"}. '
    "scales: every drawing scale printed on the sheet, usually just under a drawing title (GROUND FLOOR PLAN / Scale 1:100) or in "
    "the title block. Copy each exactly as printed, with its 'Scale' word and any 'at A1' or '@ A3' that goes with it. "
    "Do NOT include slopes, falls, pitches or gradients that also look like 1:100 (for example 'fall away from house 1:100'), "
    "dimensions, or the graphic bar scale. If the sheet says 'Not to scale', 'NTS' or 'As indicated' and gives no ratio, return an empty list. "
    "page_size: any statement of the sheet's paper size (for example 'PAGE SIZE - A2' or 'A1 SHEET'), else null. "
    "Never guess: only transcribe what you can read clearly."
)


def read_scale_text(pdf_path, index):
    """The scale statements printed on a flattened sheet, as one string. The AI only transcribes: the ratio is worked out by
    core.scale from these words, and a person confirms it."""
    data = _post(SCALE_SYSTEM, page_image(pdf_path, index, long_edge=2600),
                 "Transcribe the scale statements and the page size statement on this sheet.", max_tokens=400)
    scales = [str(x)[:60] for x in (data.get("scales") or []) if isinstance(x, (str, int, float))][:6]
    size = data.get("page_size")
    return "\n".join(scales + ([str(size)[:60]] if isinstance(size, str) else []))


def read_full_page(pdf_path, index):
    """The whole page in one image: {sheet, entries, legend_box}."""
    data = _post(SYSTEM, page_image(pdf_path, index), "Extract the legend entries from this page.")
    return {"sheet": str(data.get("sheet", ""))[:60], "entries": clean_entries(data.get("entries")),
            "legend_box": clean_box(data.get("legend_box"))}


def read_page_entries(pdf_path, index, box=None):
    """{sheet, entries, verify, legend_box}: the legend of one page, read whole and then zoomed. See the module docstring.

    With a box (the legend's position, remembered from an earlier read or given by a person) only that area is read, in zoomed
    strips: no whole-page reading and no locating, about half the calls. It has no whole-page reading to compare with, so
    "verify" then holds only disagreements between overlapping strips. If the area gives nothing, the whole sheet is read."""
    box = clean_box(box) if box is not None else None
    if box:
        tile_lists = _read_strips(pdf_path, index, box)
        if any(tile_lists):
            entries, verify = merge_readings(tile_lists, [])
            return {"sheet": "", "entries": entries, "verify": verify, "legend_box": box}
    try:
        full = read_full_page(pdf_path, index)
    except ReaderError as first_error:
        if "could not be understood" not in str(first_error):
            raise                                       # no key, no credit, rejected, busy: nothing a second pass can fix
        full = {"sheet": "", "entries": [], "legend_box": None}
        first_failed = first_error
    else:
        first_failed = None
    candidates = [full["legend_box"]] if full["legend_box"] else []
    tried, locates, tile_lists = [], 0, []
    while True:
        box = next((b for b in candidates if not any(_near(b, t) for t in tried)), None)
        if box is None:
            if full["entries"] or locates >= LOCATE_TRIES:
                break
            locates += 1                                # the whole-page reading found nothing: ask where the legend is. The answer
            try:                                        # varies between calls (the notes block, nothing, the legend): ask again
                located = clean_box(_post(LOCATE, page_image(pdf_path, index), "Where is the legend?", 400).get("legend_box"))
            except ReaderError:
                located = None
            if located:
                candidates.append(located)
            continue
        tried.append(box)
        tile_lists = _read_strips(pdf_path, index, box)
        if any(tile_lists):
            break
    if not any(tile_lists):
        if first_failed:
            raise first_failed
        return {"sheet": full["sheet"], "entries": full["entries"], "verify": [], "legend_box": None}
    entries, verify = merge_readings(tile_lists, full["entries"])
    return {"sheet": full["sheet"], "entries": entries, "verify": verify, "legend_box": tried[-1]}


def _near(a, b):
    return all(abs(x - y) < 0.05 for x, y in zip(a, b))


def _read_strips(pdf_path, index, box):
    """Read the legend box in strips. When the strips come back empty or with many rows that are a bare number, the box
    missed the label column: widen it to the left once and keep whichever reading found more rows."""
    best, dropped, errors = _strips(pdf_path, index, box)
    seen = sum(len(t) for t in best)
    if not errors and (seen == 0 or dropped >= max(3, 0.3 * (seen + dropped))) and box[0] > 0.05:
        wider, _, _ = _strips(pdf_path, index, [max(0.0, box[0] - WIDEN_LEFT)] + box[1:])
        if sum(len(t) for t in wider) > seen:
            return wider
    return best


def _strips(pdf_path, index, box):
    doc = fitz.open(pdf_path)
    try:
        tiles = legend_tiles(doc[index].rect, box)
    finally:
        doc.close()
    images = [clip_image(pdf_path, index, t) for t in tiles]

    def read_tile(img):
        try:
            return split_entries(_post(SYSTEM, img, "Extract the legend entries from this page.").get("entries")) + (0,)
        except ReaderError:                             # that strip is lost; its rows still come from the whole-page reading
            return [], 0, 1

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(read_tile, images))
    return [r[0] for r in results], sum(r[1] for r in results), sum(r[2] for r in results)
