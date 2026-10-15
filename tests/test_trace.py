"""Wall tracing: a click takes a wall's whole straight run, joined across the pieces the drawing splits it into. The function lives in
the page (web/index.html), so it is run here under node."""
import json, re, shutil, subprocess, sys
from pathlib import Path

node = shutil.which("node")
if not node:
    print("SKIP node is not installed")
    sys.exit(0)

html = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text()
fn = html.split("// TRACE_START")[1].split("// TRACE_END")[0]

segs = [
    [100, 100, 160, 100, 1.0], [160.5, 100, 240, 100, 1.0], [241, 100, 300, 100, 1.0],      # one wall in three pieces, small joins
    [320, 100, 400, 100, 1.0],                                                              # the same line after a 20 pt door gap
    [100, 110, 300, 110, 1.0],                                                              # the wall's other face, 10 pt away
    [300, 100, 300, 200, 1.0],                                                              # a wall at right angles
    [100, 150, 300, 150, 0.1],                                                              # hairline detail: not a wall
    [100, 160, 104, 160, 1.0], [106, 160, 110, 160, 1.0],                                   # tiny dashes: not a wall
    [50, 300, 250, 340, 0.99],                                                              # a sloping wall
]
js = fn + f"""
const segs={json.dumps(segs)};
const r=(p,reach=5)=>traceRun(segs,p,reach);
console.log(JSON.stringify({{
  mid:r([200,101]), nearEnd:r([102,99]), faceB:r([200,111]), across:r([300,150]), hair:r([200,150]), dash:r([102,160]),
  nothing:r([500,500]), after:r([360,100]), slope:r([150,320]) }}));
"""
out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)


def close(run, a, b, tol=0.3):
    return run and all(abs(run[i][j] - v) <= tol for i, pt in enumerate((a, b)) for j, v in enumerate(pt))


def ends(run):
    return sorted(map(tuple, run))


assert close(sorted(out["mid"]), (100, 100), (300, 100)), out["mid"]
print("PASS a click mid-wall takes the whole run, joined across split pieces (100 to 300)")
assert close(sorted(out["nearEnd"]), (100, 100), (300, 100)), out["nearEnd"]
print("PASS the same run from a click near its end")
assert close(sorted(out["faceB"]), (100, 110), (300, 110)), out["faceB"]
print("PASS the parallel face 10 pt away is its own run")
assert close(sorted(out["across"], key=lambda p: p[1]), (300, 100), (300, 200)), out["across"]
print("PASS a wall at right angles runs on its own (100 to 200)")
assert close(sorted(out["after"]), (320, 100), (400, 100)), out["after"]
print("PASS a door gap stops the run: the far side is a separate run")
assert out["hair"] is None and out["dash"] is None and out["nothing"] is None
print("PASS hairlines, tiny dashes and empty space give nothing")
sl = sorted(out["slope"])
assert abs(sl[0][0] - 50) < 0.3 and abs(sl[1][0] - 250) < 0.3, out["slope"]
print("PASS a sloping wall is traced along its slope")
