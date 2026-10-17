"""The takeoff item library: lists of items (name, what is measured, colour) that a team loads into a project in one go.

A job template is data, not code. It holds the items an estimator measures on a kind of job (a house, a slab and footings, ...), so a new
project of that kind starts with the same names, units and colours instead of being built item by item. Templates are either bundled with
the app (core/item_templates/*.json, made by tools/make_item_template.py from finished jobs) or saved by the team in the document store.
Later phases add rules and a workbook layout to the same document; keys this module does not know are kept as they are."""
import json
import math
import re
from pathlib import Path

ITEM_KINDS = ("len", "area", "count", "wall", "vol")      # wall: length x height minus openings (m2); vol: area x depth (m3)
ITEM_PALETTE = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4", "#17bebb", "#f032e6", "#bcbd22", "#469990", "#9a6324",
                "#800000", "#000075", "#808000", "#e377c2", "#7f7f7f", "#2e86ab"]       # bold, easy to tell apart; used when an item has no colour
NAME_MAX, TAG_MAX, MAX_ITEMS, MAX_TEMPLATE_ITEMS = 120, 12, 400, 400
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
TEMPLATE_DIR = Path(__file__).parent / "item_templates"
_BUILTIN = None


def clean_name(raw, limit=NAME_MAX):
    """An item name as it is shown and stored: no markup or control characters, single spaces, at most `limit` characters. May be empty."""
    return re.sub(r"\s+", " ", re.sub(r"[<>&\"'`\x00-\x1f]", "", str(raw or ""))).strip()[:limit]


def clean_item(raw):
    """One item from a template or a request: {name, kind, color?, size?, tag?}. Raises ValueError with a message a person can read."""
    if not isinstance(raw, dict):
        raise ValueError("An item must be an object.")
    name = clean_name(raw.get("name"))
    if not name:
        raise ValueError("Every item needs a name.")
    kind = raw.get("kind")
    if kind not in ITEM_KINDS:
        raise ValueError(f"'{name}': measured as length, area, count, wall or volume.")
    item = {"name": name, "kind": kind}
    colour = str(raw.get("color") or "")
    if colour:
        if not HEX.match(colour):
            raise ValueError(f"'{name}': the colour must look like #e6194b.")
        item["color"] = colour.lower()
    if kind in ("wall", "vol"):                            # a wall needs its height, a volume its depth
        try:
            size = float(raw.get("size"))
        except (TypeError, ValueError):
            raise ValueError(f"'{name}': a wall needs its height and a volume its depth, in metres.")
        if not (math.isfinite(size) and 0.005 <= size <= 100):
            raise ValueError(f"'{name}': the height or depth must be between 0.005 and 100 m.")
        item["size"] = round(size, 3)
    tag = clean_name(raw.get("tag"), TAG_MAX)
    if tag:
        item["tag"] = tag                                  # for example U1 / U2 on a duplex: a later phase splits work by it
    return item


def clean_items(raw, limit=MAX_TEMPLATE_ITEMS):
    """A list of items, validated, with repeats (same name and kind) dropped."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("Add at least one item.")
    if len(raw) > limit:
        raise ValueError(f"A template holds at most {limit} items.")
    out, seen = [], set()
    for r in raw:
        it = clean_item(r)
        key = (it["name"].lower(), it["kind"])
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


def next_colour(taken, index=0):
    """The first palette colour nothing uses yet; when all are used, cycle."""
    taken = {str(c).lower() for c in taken}
    return next((c for c in ITEM_PALETTE if c not in taken), ITEM_PALETTE[index % len(ITEM_PALETTE)])


def merge(existing, template_items, limit=MAX_ITEMS):
    """Add a template's items to a project's items. Returns (items, added, skipped, cut_off): an item already in the project
    (same name and kind) is skipped, a colour the template gives is kept, any other gets the next free palette colour."""
    items = [dict(i) for i in existing]
    have = {(str(i.get("name", "")).lower(), i.get("kind")) for i in items}
    added = skipped = cut = 0
    for t in template_items:
        if (t["name"].lower(), t["kind"]) in have:
            skipped += 1
            continue
        if len(items) >= limit:
            cut += 1
            continue
        item = {"id": max([i["id"] for i in items] + [0]) + 1, "name": t["name"], "kind": t["kind"],
                "color": t.get("color") or next_colour([i.get("color", "") for i in items], len(items))}
        for k in ("size", "tag"):
            if k in t:
                item[k] = t[k]
        items.append(item)
        have.add((t["name"].lower(), t["kind"]))
        added += 1
    return items, added, skipped, cut


def builtin_templates():
    """{id: template} for the templates bundled with the app. Read once; a file that does not validate is skipped, not fatal."""
    global _BUILTIN
    if _BUILTIN is None:
        found = {}
        for f in sorted(TEMPLATE_DIR.glob("*.json")):
            try:
                doc = json.loads(f.read_text())
                found[f.stem] = {**doc, "id": f.stem, "name": clean_name(doc.get("name")) or f.stem, "builtin": True,
                                 "items": clean_items(doc.get("items"))}
            except (ValueError, OSError, json.JSONDecodeError):
                continue
        _BUILTIN = found
    return _BUILTIN


def summary(t):
    return {"id": t["id"], "name": t.get("name", ""), "description": t.get("description", ""), "count": len(t.get("items", [])),
            "builtin": bool(t.get("builtin")), "source": t.get("source", ""), "by": t.get("by", ""), "updated": t.get("updated")}
