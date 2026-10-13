"""Compares two independent sets of takeoff lines and says where they agree.

`logic` is the deterministic result (rules, vector geometry); `ai` is an independent
second opinion. The AI never overwrites the logic's numbers: it only agrees, disputes,
adds a line the logic missed, or stays silent. A human then reviews, disputed first.
"""
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from core.legend import parse_mark
from core.models import TakeoffLine

AGREED, DISPUTED, UNCONFIRMED, AI_ONLY = "agreed", "disputed", "unconfirmed", "ai_only"
PRIORITY = {DISPUTED: 0, AI_ONLY: 1, UNCONFIRMED: 2, AGREED: 3}
DIMS = ("length", "width", "height", "diameter", "area", "count")


@dataclass
class Reconciled:
    line: TakeoffLine                      # the logic line (or the AI's when ai_only)
    status: str
    ai_qty: Optional[float] = None
    logic_qty: Optional[float] = None
    delta_pct: Optional[float] = None
    reason: str = ""
    differing: list = field(default_factory=list)

    @property
    def confidence(self):
        """Derived from agreement, never from a number the model reports about itself."""
        if self.status == AGREED:
            return round(0.97 - min(abs(self.delta_pct or 0), 2) / 100, 2)
        return {DISPUTED: 0.40, AI_ONLY: 0.35, UNCONFIRMED: 0.60}[self.status]


def _tok(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _mark(l):
    return parse_mark(l.description) or (l.mark if re.fullmatch(r"[A-Z]{1,3}\d+[a-z]?", l.mark or "") else "")


def _close(a, b, rel, floor):
    return abs(a - b) <= max(floor, rel * abs(b))


def _pair(logic, ai, min_desc=0.72):
    """Best-first pairing: same group and mark, else same group and similar description."""
    cands = []
    for i, l in enumerate(logic):
        for j, a in enumerate(ai):
            if _tok(l.group) != _tok(a.group):
                continue
            lm, am = _mark(l), _mark(a)
            d = SequenceMatcher(None, _tok(l.description), _tok(a.description)).ratio()
            if lm and am:
                if lm != am:
                    continue
                score = 1.0 + d
            elif d >= min_desc:
                score = d
            else:
                continue
            # prefer the pair whose quantities are closest when several share a mark
            qa, ql = a.quantity(), l.quantity()
            near = 0.0 if None in (qa, ql) else 1 / (1 + abs(qa - ql) / max(abs(ql), 1e-9))
            cands.append((score + 0.01 * near, i, j))
    cands.sort(reverse=True)
    used_l, used_a, pairs = set(), set(), []
    for _, i, j in cands:
        if i in used_l or j in used_a:
            continue
        used_l.add(i); used_a.add(j); pairs.append((i, j))
    return pairs, used_l, used_a


def reconcile(logic, ai, rel_tol=0.02, abs_floor=0.06):
    pairs, used_l, used_a = _pair(logic, ai)
    out = []
    for i, j in pairs:
        l, a = logic[i], ai[j]
        ql, qa = l.quantity(), a.quantity()
        diff = [d for d in DIMS if getattr(l, d) is not None and getattr(a, d) is not None
                and not _close(getattr(a, d), getattr(l, d), rel_tol, abs_floor)]
        if ql is None or qa is None:
            out.append(Reconciled(l, UNCONFIRMED, qa, ql, None, "one side has no quantity for this line"))
        elif _close(qa, ql, rel_tol, abs_floor):
            out.append(Reconciled(l, AGREED, qa, ql, (qa - ql) / ql * 100 if ql else 0))
        else:
            pct = (qa - ql) / ql * 100 if ql else float("inf")
            why = f"AI {qa:.2f} vs logic {ql:.2f} ({pct:+.1f}%)"
            if diff:
                why += "; differs in " + ", ".join(diff)
            out.append(Reconciled(l, DISPUTED, qa, ql, pct, why, diff))
    for i, l in enumerate(logic):
        if i not in used_l:
            out.append(Reconciled(l, UNCONFIRMED, None, l.quantity(), None, "AI did not report this line"))
    for j, a in enumerate(ai):
        if j not in used_a:
            out.append(Reconciled(a, AI_ONLY, a.quantity(), None, None, "AI found a line the logic did not produce"))
    out.sort(key=lambda r: (PRIORITY[r.status], r.line.group, r.line.mark))
    return out


def summary(rows):
    s = {k: 0 for k in PRIORITY}
    for r in rows:
        s[r.status] += 1
    s["total"] = len(rows)
    return s
