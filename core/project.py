"""Builds the JSON the web app works with: pages, scales, takeoff lines, AI cross-check.

Sample projects run the real pipeline over the legend entries read from their marked-up plans.
Uploaded projects get rendered pages and per-sheet scale detection; takeoff lines come later
(AI measuring is not built yet), so they start empty.
"""
import copy
import json
import re
from pathlib import Path

import fitz

from core.calc import measure_type, n_of, qty
from core.models import TakeoffLine
from core.reconcile import reconcile
from core.scale import effective_scale, paper_of
from core.takeoff_builder import PROFILES, build_takeoff, group_order

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = {k: v for k, v in {"boxshall": "boxshall-brighton", "westgarth": "82-westgarth"}.items()
           if (ROOT / "samples" / v / "marked_up.pdf").exists()}   # absent on a deployment that leaves client plans out
import os
DATA = Path(os.environ.get("QD_DATA") or ("/tmp/qd-data" if os.environ.get("VERCEL") else ROOT / "data"))   # Vercel has no persistent disk: only scratch space   # QD_DATA moves all stored files (uploads, saved state, shares) to a mounted volume
BASIS = {"prism": "plan area x depth", "plan": "plan area", "edge": "edge length x height",
         "face2": "length x 2 faces x height", "len": "length", "pt": "area x kg/m2"}


def native_z(page):
    """Pixels per PDF point at the page's own embedded image resolution (8 for vector pages)."""
    imgs = page.get_images(full=True)
    if not imgs:
        return 8.0
    w_px = max(i[2] for i in imgs)
    return round(min(8.0, max(2.0, w_px / page.rect.width)), 2)


def render_pages(pdf, out_dir, zoom=2):
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf)
    sizes = []
    for i, p in enumerate(doc):
        f = out_dir / f"p{i + 1}.png"
        if not f.exists():
            p.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(str(f))
        sizes.append((p.rect.width, p.rect.height))
    return sizes


def _scale_json(text, w, h):
    r = effective_scale(text, w, h)
    status = {"auto": "ok", "needs_check": "check", "unknown": "unknown"}[r.status]
    # even a clean 'auto' result still waits for a human; only confirmation makes it ok
    status = "check" if status == "ok" else status
    return {"ratio": r.ratio, "status": status, "src": r.source, "note": " ".join(r.notes)}


def line_json(i, l, sheet, legend, profile):
    n = n_of(l)
    box = None
    if legend and sheet == legend["page"]:
        y = legend["y0"] + (legend["first"] + legend["row_h"] * l.element) * 1.0
        box = [legend["x0"], round(y, 2), legend["w"], legend["h"]]
    return {"id": i, "el": l.element, "g": l.group, "mk": (l.mark if "_" not in l.mark else l.kind.replace("_", " ")).upper(),
            "ds": l.description, "u": l.unit, "q": round(l.quantity() or 0, 4), "n": n, "calc": l.calc,
            "mt": measure_type(l), "kind": l.kind, "basis": BASIS.get(l.calc, ""), "sheet": sheet, "src": box,
            "st": "pn", "ag": "unrun", "aiq": None, "agr": ""}


def _entries(sample):
    pages = json.load(open(ROOT / "samples" / sample / "entries_manual.json"))
    flat, page_of = [], []
    for p in pages:
        for e in p["entries"]:
            flat.append(e)
            page_of.append(p["page"])
    return flat, page_of


def sample_project(pid):
    folder = SAMPLES[pid]
    meta = json.load(open(ROOT / "samples" / folder / "meta.json"))
    sizes = render_pages(ROOT / "samples" / folder / "marked_up.pdf", DATA / "pages" / pid)
    profile = PROFILES[meta["profile"]]
    entries, page_of = _entries(folder)
    lines, skipped = build_takeoff(entries, profile)
    counts = {}
    pages = []
    for i, (w, h) in enumerate(sizes):
        sh = meta["sheets"][i] if i < len(meta["sheets"]) else {"n": f"Sheet {i + 1}", "t": "", "scale": ""}
        pages.append({"n": sh["n"], "t": sh["t"], "w": w, "h": h, "paper": paper_of(w, h),
                      "img": f"/pages/{pid}/p{i + 1}.png", "scale": _scale_json(sh["scale"], w, h),
                      "max_z": native_z(fitz.open(ROOT / "samples" / folder / "marked_up.pdf")[i])})
    out = []
    for i, l in enumerate(lines, 1):
        sheet = page_of[l.element]
        counts[sheet] = counts.get(sheet, 0) + 1
        out.append(line_json(i, l, sheet, meta.get("legend"), profile))
    for p_i, p in enumerate(pages, 1):
        p["c"] = counts.get(p_i, 0)
    return {"id": pid, "name": meta["name"], "engineer": meta["engineer"], "rev": meta["rev"],
            "status": meta["status"], "sample": True, "profile": meta["profile"],
            "groups": [g for g in group_order(profile) if any(x["g"] == g for x in out)] or group_order(profile),
            "pages": pages, "lines": out,
            "skipped": [f"{d}: {why}" for d, why in skipped], "ai": "not run"}


def from_json(lj):
    """Rebuild a TakeoffLine from the UI's JSON so export and template fill use the edited numbers."""
    n, u = lj["n"], lj["u"]
    q = lj["q"]
    t = TakeoffLine(group=lj["g"], mark=lj["mk"], description=lj["ds"], unit=u,
                    length=n.get("L"), width=n.get("W"), height=n.get("H"), calc=lj["calc"],
                    status="Verified" if lj.get("st") == "ok" else "PENDING", k=n.get("K"),
                    kind=lj.get("kind", ""), element=lj.get("el", -1), rate=lj.get("rate"), rate_source=lj.get("rate_source") or "",
                    labour_hours=lj.get("lhpu"), labour_rate=lj.get("lrate"))
    if lj["calc"] != "pt" and n.get("N") not in (None, 1):
        t.count = n["N"]
    if n.get("A") is not None:
        t.area = n["A"]
    if u == "m3":
        t.volume = q
    elif u == "m2":
        t.area = q
    elif u == "m":
        t.length = q if t.length is None else t.length
    elif u == "tons":
        t.count = q
    return t


# ---- demo second opinion: planted, clearly labelled disagreements until a real AI run exists
def demo_ai(project):
    lines = project["lines"]
    logic = [from_json(l) for l in lines]
    ai = copy.deepcopy(logic)
    def find(sub, grp):
        return next((a for a in ai if sub in a.description and a.group.startswith(grp)), None)
    big = max((a for a in ai if a.kind == "bulk_excavation"), key=lambda a: a.height or 0, default=None)
    if big:
        big.height = (big.height or 0) * 0.91
        big.volume = (big.area or 0) * big.height
    pad = next((a for a in ai if a.kind == "pad_footing" and a.count and a.count > 1), None)
    if pad:   # one pad fewer changes every line that derives from that element
        el, ratio = pad.element, (pad.count - 1) / pad.count
        for a in ai:
            if a.element == el:
                a.count -= 1
                for f in ("volume", "area"):
                    if getattr(a, f) is not None:
                        setattr(a, f, getattr(a, f) * ratio)
    drop = next((a for a in ai if a.kind == "edge_form"), None)
    if drop:
        ai.remove(drop)
    ai.append(TakeoffLine("BLINDING" if project["profile"] == "boxshall" else logic[0].group, "RAFT",
                          "RAFT FOOTING CONCRETE BLINDING-300mm THICK", "m3", area=65.8, height=0.3, volume=19.74,
                          kind="raft_footing", element=-2, calc="prism"))
    rows = reconcile(logic, ai)
    byk = {(r.line.group, r.line.description, r.line.mark): r for r in rows}
    nid = max(l["id"] for l in lines)
    for l in lines:
        r = byk.get((l["g"], l["ds"], logic[l["id"] - 1].mark))
        if r:
            l["ag"], l["aiq"], l["agr"] = r.status, (round(r.ai_qty, 2) if r.ai_qty is not None else None), r.reason
    for r in rows:
        if r.status == "ai_only":
            nid += 1
            ln = line_json(nid, r.line, 1, None, None)
            ln.update(ag="ai_only", aiq=round(r.ai_qty, 2), agr=r.reason, st="pn", el=-2, mt="area")
            lines.append(ln)
    project["ai"] = "DEMO: planted disagreements, not a real AI run"
    return project
