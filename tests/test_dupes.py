"""Look-alike lines are flagged for a person; real twins and derived sibling lines are not."""
from core.dupes import find_duplicates


def L(i, ds, q=412.0, u="m2", g="PAINT", el=None):
    return {"id": i, "el": i if el is None else el, "ds": ds, "q": q, "u": u, "g": g}


# the Clinton case: one legend row read twice, one copy with a typo
d = find_duplicates([L(1, "Interna Wall Paint Finish-U1 = 412m2"), L(2, "Internal Wall Paint Finish-U1 = 412m2")])
assert d == {1: [2], 2: [1]}, d
print("PASS a row read twice (typo'd copy) is flagged")

assert find_duplicates([L(1, "Wall Paint Finish-U1", 412), L(2, "Wall Paint Finish-U2", 412)]) == {}
print("PASS U1 and U2 are real twins")

assert find_duplicates([L(1, "Window - 600-1800 - U1", 2.16), L(2, "Window - 1200-1800 - U1", 2.16)]) == {}
print("PASS different sizes are different items")

assert find_duplicates([L(1, "Wall Paint Finish-U1", 412), L(2, "Wall Paint Finish-U1", 380)]) == {}
print("PASS different quantities are not flagged")

assert find_duplicates([L(1, "Brickworks", 4142.5, "each", el=7), L(2, "Brickworks", 4142.5, "each", el=7)]) == {}
print("PASS two lines made from one legend row are siblings")

assert find_duplicates([L(1, "Door", 2, "each", g="DOORS"), L(2, "Door", 2, "each", g="JOINERY")]) == {}
assert find_duplicates([L(1, "Door", 2, "each"), L(2, "Door", 2, "m")]) == {}
print("PASS different group or unit is not flagged")
print("ALL PASSED")
