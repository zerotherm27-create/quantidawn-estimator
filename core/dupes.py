"""Spots lines that look like the same legend row read twice (the AI reading a row, a typo'd copy of it, or a row that really
appears twice on the sheet). It only flags: a person decides, because two identical rows can be real (two doors of one type)."""
import re
from difflib import SequenceMatcher

SPELL = {"muchugh": "mchugh", "interna": "internal", "vapour": "vapor", "polystyrence": "polystyrene", "polystyrenc": "polystyrene"}
MIN_SIMILAR = 0.92          # how alike two descriptions must be (0-1) after tidying
TAG = re.compile(r"\bu\s*-?\s*([12])\b|\bu([12])\b", re.I)


def _tidy(text):
    words = [SPELL.get(w, w) for w in re.findall(r"[a-z0-9.]+", (text or "").lower())]
    return " ".join(words)


def _tag(text):
    return {a or b for a, b in TAG.findall(text or "")}


def _numbers(text):
    return sorted(re.findall(r"\d+(?:\.\d+)?", (text or "").lower()))


def _same_qty(a, b):
    return abs(a - b) <= max(0.06, 0.005 * max(abs(a), abs(b)))


def find_duplicates(lines):
    """lines: dicts with id, el (legend row it came from), ds (description), u (unit), q (quantity), g (group).
    -> {line id: [ids of its look-alikes]}. Lines made from the same legend row (el) are siblings, not duplicates, and two
    descriptions that differ in any number (600-1800 against 1200-1800) are different items."""
    out = {}
    items = [(l["id"], l.get("el"), l.get("g", ""), l.get("u", ""), float(l.get("q") or 0), _tidy(l.get("ds", "")), _tag(l.get("ds", "")),
              _numbers(l.get("ds", ""))) for l in lines]
    for n, (ia, ea, ga, ua, qa, ta, tga, na) in enumerate(items):
        for ib, eb, gb, ub, qb, tb, tgb, nb in items[n + 1:]:
            if ea is not None and ea == eb:
                continue                                # two lines from one legend row
            if ga != gb or ua != ub or tga != tgb or na != nb or not _same_qty(qa, qb):
                continue
            if SequenceMatcher(None, ta, tb).ratio() >= MIN_SIMILAR:
                out.setdefault(ia, []).append(ib)
                out.setdefault(ib, []).append(ia)
    return out
