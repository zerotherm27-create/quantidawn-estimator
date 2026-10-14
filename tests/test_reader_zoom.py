"""The AI reader's second pass: legend located, cropped, zoomed, merged with the whole-page reading. No API calls."""
import os
import tempfile

import fitz

from core import reader

fails = 0
def check(cond, msg):
    global fails
    print(("PASS " if cond else "FAIL ") + msg); fails += (not cond)

PAGE = fitz.Rect(0, 0, 1191, 842)

# clean_box: only a plausible legend rectangle survives
check(reader.clean_box([0.6, 0.02, 0.98, 0.3]) == [0.6, 0.02, 0.98, 0.3], "a good box is kept")
check(reader.clean_box(None) is None and reader.clean_box("x") is None and reader.clean_box([1, 2]) is None, "missing or malformed box is dropped")
check(reader.clean_box([0.5, 0.5, 0.51, 0.9]) is None and reader.clean_box([0.1, 0.5, 0.9, 0.5]) is None, "a sliver is not a legend")
check(reader.clean_box([-0.2, 0.1, 1.4, 0.4]) == [0.0, 0.1, 1.0, 0.4], "a box off the page is clamped")
check(reader.clean_box([0.1, float("nan"), 0.9, 0.4]) is None, "nan is dropped")

# legend_tiles: padded, inside the page, tall legends split with an overlap
t = reader.legend_tiles(PAGE, [0.6, 0.02, 0.98, 0.15])
check(len(t) == 1 and t[0].x0 < 0.6 * 1191 and t[0].x1 <= 1191 and t[0].y0 >= 0, "a short legend is one padded strip")
t = reader.legend_tiles(PAGE, [0.81, 0.02, 1.0, 0.3])
check(t[0].width >= reader.MIN_LEGEND_W * 1191 and t[0].x1 == 1191, "a box that is only the quantity column is widened to the left to a legend's minimum width")
t = reader.legend_tiles(PAGE, [0.29, 0.005, 0.62, 0.115])
check(t[0].x0 < 0.23 * 1191 and t[0].x1 > 0.62 * 1191, "a tight centre box still covers the labels to its left")
t = reader.legend_tiles(PAGE, [0.1, 0.02, 0.9, 0.3])
check(abs(t[0].x0 - 0.05 * 1191) < 1 and abs(t[0].x1 - 0.95 * 1191) < 1, "a box already wider than the minimum only gets the side padding")
t = reader.legend_tiles(PAGE, [0.6, 0.02, 0.98, 0.62])
check(len(t) >= 2 and all(s.height <= reader.TILE_MAX_HEIGHT_PT + 0.01 for s in t), "a tall legend is split into strips of at most the cap")
check(all(t[i + 1].y0 < t[i].y1 for i in range(len(t) - 1)) and t[-1].y1 >= 0.62 * 842, "strips overlap and cover the whole legend")
t = reader.legend_tiles(PAGE, [0.0, 0.0, 1.0, 1.0])
check(t[0].y0 == 0 and t[-1].y1 == 842 and all(s.x0 == 0 and s.x1 == 1191 for s in t), "padding never leaves the page")

# merge_readings
def E(label, q, u="M"):
    return {"label": label, "quantity": q, "unit": u}

ents, ver = reader.merge_readings(
    [[E("Mchugh Ceiling Battens - U2", 183.7, "SQ M"), E("Valley Board - U2", 4.1)]],
    [E("Mchugh Ceiling Battens - U2", 193.7, "SQ M"), E("Valley Board - U2", 1.0, "EA"), E("Only On Whole Page", 9.0)])
q = {e["label"]: e["quantity"] for e in ents}
check(q["Mchugh Ceiling Battens - U2"] == 183.7 and q["Valley Board - U2"] == 4.1, "the zoomed value wins over the whole-page value")
check(q.get("Only On Whole Page") == 9.0, "a row only the whole-page reading found is kept")
check({v["label"] for v in ver} == {"Mchugh Ceiling Battens - U2", "Valley Board - U2"}
      and all(v["why"] == "whole page differs" for v in ver), "rows where the readings disagree are listed to verify, with both numbers")
check(next(v for v in ver if v["label"].startswith("Valley"))["readings"] == [4.1, 1.0], "verify keeps the zoomed number first")

ents, ver = reader.merge_readings(
    [[E("Plastering and Sanding Walls - U2 -Supply and Install", 1.0, "EA")]],
    [E("Plastering and Sanding Walls - U2 -Supply and Install = 409m2", 1.0, "EA")])
check(len(ents) == 1 and not ver, "a label that lost its trailing text is the same row, not a duplicate")

ents, ver = reader.merge_readings(
    [[E("Row A", 5.0), E("Row B", 6.0)], [E("Row B", 6.0), E("Row C", 7.0)]], [])
check([e["label"] for e in ents] == ["Row A", "Row B", "Row C"] and not ver, "overlapping strips are merged without duplicates")
ents, ver = reader.merge_readings([[E("Row B", 6.0)], [E("Row B", 8.0)]], [])
check(len(ents) == 1 and ver and ver[0]["why"] == "strips disagree" and ver[0]["readings"] == [6.0, 8.0], "overlapping strips that disagree are flagged")
ents, ver = reader.merge_readings([[E("Row B", 6.02)]], [E("Row B", 6.0)])
check(not ver, "rounding noise is not a disagreement")
ents, ver = reader.merge_readings([[E("Wall linings U1", 126.5)], [E("Wall linings U2", 128.1)]], [])
check(len(ents) == 2, "U1 and U2 rows stay separate")


# a "label" that is only a quantity means the crop lost the label column
ents, dropped = reader.split_entries([E("112.0 M", 112.0), E("26.0 EA", 26.0, "EA"), E("3.7 SQ M", 3.7, "SQ M"), E("Wall Frame U2", 112.0)])
check(dropped == 3 and [e["label"] for e in ents] == ["Wall Frame U2"], "number-only labels are dropped and counted")
check(reader.clean_entries([E("ok", 3.0)]) == [E("ok", 3.0)] and reader.clean_entries([E("U1", 3.0)]) == [E("U1", 3.0)], "short real labels survive")

# a disagreement the label explains is not flagged
ents, ver = reader.merge_readings([[E("Roof Trusses - U1 - 343m2", 1.0, "EA")]], [E("Roof Trusses - U1 - 343m2", 343.0, "SQ M")])
check(not ver and ents[0]["quantity"] == 1.0, "a quantity written in the label is not a disagreement")
ents, ver = reader.merge_readings([[E("Battens - U2", 183.7)]], [E("Battens - U2", 193.7)])
check(len(ver) == 1, "a real disagreement is still flagged")

# the model's reply can have a sentence before the JSON, or fences around it
check(reader._json_object('{"a": 1}') == {"a": 1}, "plain JSON is parsed")
check(reader._json_object('Looking at the image, the legend is on the right.\n\n```json\n{"legend_box": [0.8, 0.0, 1.0, 0.5]}\n```') == {"legend_box": [0.8, 0.0, 1.0, 0.5]}, "JSON after a sentence and inside fences is found")
check(reader._json_object('see {braces} first, then {"entries": []}') == {"entries": []}, "a stray brace before the JSON is skipped")
check(reader._json_object("no json here") is None and reader._json_object('{"cut off": [1, 2') is None and reader._json_object("[1, 2]") is None, "text without a JSON object is rejected")

# read_page_entries with the API replaced: every path
tmp = tempfile.mkdtemp()
pdf = os.path.join(tmp, "p.pdf")
d = fitz.open(); d.new_page(width=1191, height=842).insert_text((700, 60), "x", fontsize=8); d.save(pdf)

calls = []
def script(*answers):
    """A fake reader._post that returns/raises the given answers in order, recording each call."""
    it = iter(answers)
    def fake(system, image, text, max_tokens=4000):
        calls.append((text, len(image)))
        a = next(it)
        if isinstance(a, Exception):
            raise a
        return a
    return fake

BOX = [0.55, 0.02, 0.98, 0.2]
orig = reader._post
try:
    calls.clear()
    reader._post = script({"sheet": "7", "entries": [E("A", 183.7), E("B", 1.0)], "legend_box": BOX},
                          {"entries": [E("A", 193.7), E("B", 4.1)]})
    r = reader.read_page_entries(pdf, 0)
    q = {e["label"]: e["quantity"] for e in r["entries"]}
    check(q == {"A": 193.7, "B": 4.1} and r["sheet"] == "7", "zoomed readings replace the whole-page ones")
    check([v["label"] for v in r["verify"]] == ["A", "B"] and len(calls) == 2, "disagreements are flagged, one whole-page call and one strip")

    calls.clear()
    reader._post = script({"sheet": "7", "entries": [E("A", 1.0)], "legend_box": None})
    r = reader.read_page_entries(pdf, 0)
    check(r["entries"] == [E("A", 1.0)] and r["verify"] == [] and len(calls) == 1, "no legend box: the whole-page reading is returned, nothing more is called")

    calls.clear()
    reader._post = script({"sheet": "", "entries": [], "legend_box": None}, {"legend_box": None}, {"legend_box": None}, {"legend_box": None})
    r = reader.read_page_entries(pdf, 0)
    check(r["entries"] == [] and len(calls) == 4, "a page with no legend costs the read and three cheap locate calls")

    calls.clear()
    reader._post = script({"sheet": "", "entries": [], "legend_box": None}, {"legend_box": [0.8, 0.0, 1.0, 0.1]}, {"entries": []}, {"entries": []},
                          {"legend_box": None}, {"legend_box": BOX}, {"entries": [E("Legend row", 7.0)]})
    r = reader.read_page_entries(pdf, 0)
    check(r["entries"] == [E("Legend row", 7.0)], "a wrong box that yields nothing is skipped and the next located box is tried")

    calls.clear()
    reader._post = script({"sheet": "", "entries": [], "legend_box": None}, {"legend_box": BOX}, {"entries": []}, {"entries": []},
                          {"legend_box": BOX}, {"legend_box": [0.1, 0.5, 0.4, 0.7]}, {"entries": []}, {"entries": []})
    r = reader.read_page_entries(pdf, 0)
    check(r["entries"] == [] and len(calls) == 8, "a box located twice is read once (the second pass of each is the widened retry)")

    calls.clear()
    reader._post = script({"sheet": "", "entries": [], "legend_box": None}, {"legend_box": BOX}, {"entries": [E("A", 5.0)]})
    r = reader.read_page_entries(pdf, 0)
    check(r["entries"] == [E("A", 5.0)], "an empty whole-page reading is rescued by locating the legend and reading it zoomed")

    reader._post = script(reader.ReaderError("The AI answer could not be understood. Try this sheet again."), {"legend_box": BOX},
                          {"entries": [E("A", 5.0)]})
    check(reader.read_page_entries(pdf, 0)["entries"] == [E("A", 5.0)], "an unreadable whole-page answer is rescued the same way")

    reader._post = script(reader.ReaderError("The AI answer could not be understood. Try this sheet again."),
                          {"legend_box": None}, {"legend_box": None}, {"legend_box": None})
    try:
        reader.read_page_entries(pdf, 0); raised = False
    except reader.ReaderError as e:
        raised = "could not be understood" in str(e)
    check(raised, "if nothing can be located the original error is raised")

    reader._post = script({"sheet": "7", "entries": [E("A", 183.7)], "legend_box": BOX}, reader.ReaderError("The AI service is busy. Wait a moment and try again."))
    r = reader.read_page_entries(pdf, 0)
    check(r["entries"] == [E("A", 183.7)] and r["verify"] == [], "a failed zoom pass falls back to the whole-page reading")

    reader._post = script(reader.ReaderError("The AI account is out of credit. Add credit at console.anthropic.com, then try again."))
    try:
        reader.read_page_entries(pdf, 0); raised = False
    except reader.ReaderError as e:
        raised = "out of credit" in str(e)
    check(raised, "no credit, rejected key and busy errors still reach the user")


    # a box that missed the label column: the strips come back as bare quantities, so the box is widened to the left once
    lost = [E("%d.0 M" % i, float(i)) for i in range(1, 6)]
    widths = []
    def narrow(system, image, text, max_tokens=4000):
        if text == "Where is the legend?":
            return {"legend_box": [0.8, 0.0, 1.0, 0.3]}
        widths.append(len(image))
        if len(widths) <= 2:                            # the first pass is two strips
            return {"entries": lost}
        return {"entries": [E("Real label %d" % i, float(i)) for i in range(1, 6)]}
    answers = iter([{"sheet": "6", "entries": [], "legend_box": [0.8, 0.0, 1.0, 0.3]}])
    def two_stage(system, image, text, max_tokens=4000):
        try:
            return next(answers)
        except StopIteration:
            return narrow(system, image, text, max_tokens)
    reader._post = two_stage
    r = reader.read_page_entries(pdf, 0)
    check([e["label"] for e in r["entries"]] == ["Real label %d" % i for i in range(1, 6)], "strips without labels trigger one wider retry, which is kept")

    # a box that returns nothing at all (only the quantity column was inside it) is widened too
    asked = []
    def empty_then_rows(system, image, text, max_tokens=4000):
        asked.append(1)
        if len(asked) == 1:
            return {"sheet": "", "entries": [], "legend_box": [0.8, 0.0, 1.0, 0.1]}
        return {"entries": []} if len(asked) == 2 else {"entries": [E("Row", 1.0)]}
    reader._post = empty_then_rows
    r = reader.read_page_entries(pdf, 0)
    check(r["entries"] == [E("Row", 1.0)] and len(asked) == 3, "a box that returned no rows is widened to the left and read again")

    # a box that really holds few rows is not retried forever
    count = []
    def always_lost(system, image, text, max_tokens=4000):
        count.append(1)
        if len(count) == 1:
            return {"sheet": "", "entries": [], "legend_box": [0.8, 0.0, 1.0, 0.3]}
        return {"entries": lost}
    reader._post = always_lost
    try:
        reader.read_page_entries(pdf, 0); got = "returned"
    except reader.ReaderError:
        got = "raised"
    check(len(count) == 1 + 4 + reader.LOCATE_TRIES, "a failed widening is tried once: the whole-page call, two passes of two strips, then the locate retries")


    # given a box, only that area is read: no whole-page call, no locating
    calls.clear()
    reader._post = script({"entries": [E("A", 183.7), E("B", 4.1)]})
    r = reader.read_page_entries(pdf, 0, box=BOX)
    check(len(calls) == 1 and {e["label"] for e in r["entries"]} == {"A", "B"} and r["legend_box"] == BOX and r["verify"] == [],
          "with a box the legend area is read in one strip call, nothing else")
    calls.clear()
    reader._post = script({"entries": []}, {"entries": []}, {"sheet": "7", "entries": [E("A", 5.0)], "legend_box": BOX}, {"entries": [E("A", 5.0)]})
    r = reader.read_page_entries(pdf, 0, box=[0.1, 0.5, 0.4, 0.7])
    check([e["label"] for e in r["entries"]] == ["A"] and r["sheet"] == "7", "a box that gives nothing falls back to reading the whole sheet")
    calls.clear()
    reader._post = script({"sheet": "7", "entries": [E("A", 5.0)], "legend_box": BOX}, {"entries": [E("A", 5.0)]})
    r = reader.read_page_entries(pdf, 0, box="junk")
    check(len(calls) == 2 and r["legend_box"] == BOX, "a malformed box is ignored")
    reader._post = script({"sheet": "7", "entries": [E("A", 5.0)], "legend_box": BOX}, {"entries": [E("A", 5.0)]})
    check(reader.read_page_entries(pdf, 0)["legend_box"] == BOX, "the box that worked is returned so it can be remembered")

    # a tall legend: the strips are read in parallel and merged
    big = os.path.join(tmp, "big.pdf")
    d = fitz.open(); d.new_page(width=1191, height=842); d.save(big)
    n = []
    def per_strip(system, image, text, max_tokens=4000):
        n.append(1)
        if len(n) == 1:
            return {"sheet": "", "entries": [], "legend_box": [0.5, 0.0, 1.0, 0.7]}
        return {"entries": [E("Row %d" % (len(n) % 2), 1.0), E("Shared", 2.0)]}
    reader._post = per_strip
    r = reader.read_page_entries(big, 0)
    check(len(n) >= 3 and sum(1 for e in r["entries"] if e["label"] == "Shared") == 1, "a tall legend is read in several strips and merged")
finally:
    reader._post = orig

print("ALL PASSED" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
