"""Reconcile logic vs a simulated AI second opinion with known, planted disagreements."""
import copy
import json

from core.reconcile import AGREED, AI_ONLY, DISPUTED, UNCONFIRMED, reconcile, summary
from core.takeoff_builder import PROFILES, build_takeoff

pages = json.load(open("samples/boxshall-brighton/entries_manual.json"))
logic, _ = build_takeoff([e for p in pages for e in p["entries"]], PROFILES["boxshall"])

def pick(mark, group_prefix):
    return next(l for l in logic if l.mark == mark and l.group.startswith(group_prefix))

ai = copy.deepcopy(logic)
by = lambda m, g: next(l for l in ai if m in l.description and l.group.startswith(g))

# planted cases
by("4400mm D", "DETAILED").height = 4.0; by("4400mm D", "DETAILED").volume = by("4400mm D", "DETAILED").area * 4.0   # depth read differently
by("SF1-", "DETAILED").volume *= 1.015                                                                  # inside tolerance
by("PF2-", "DETAILED").count = 8; by("PF2-", "DETAILED").volume *= 8 / 9                                 # one pad fewer
ai.remove(by("LIFT PIT WALL", "FORMWORKS"))                                                                      # AI silent on this one
from core.models import TakeoffLine
ai.append(TakeoffLine("BLINDING", "RAFT", "RAFT FOOTING CONCRETE BLINDING-300mm THICK", "m3", area=65.8, height=0.3, volume=19.74))

rows = reconcile(logic, ai)
s = summary(rows)
print(s)
def st(desc, group):
    return next(r.status for r in rows if desc in r.line.description and r.line.group.startswith(group))

checks = {
    "BULK depth dispute": st("4400mm D", "DETAILED") == DISPUTED,
    "PF2 count dispute": st("PF2-", "DETAILED") == DISPUTED,
    "SF1 1.5% agrees": st("SF1-", "DETAILED") == AGREED,
    "wall formwork unconfirmed": st("LIFT PIT WALL", "FORMWORKS") == UNCONFIRMED,
    "raft blinding is ai_only": any(r.status == AI_ONLY and r.line.mark == "RAFT" for r in rows),
    "disputed sorted first": rows[0].status == DISPUTED,
    "counts add up": s["total"] == len(rows) == 36,
}
for k, v in checks.items():
    print(("PASS " if v else "FAIL ") + k)
for r in rows[:5]:
    print(f"  {r.status:11} {r.line.group[:14]:14} {r.line.mark:6} conf {r.confidence}  {r.reason}")
assert all(checks.values())
print("all checks passed")
