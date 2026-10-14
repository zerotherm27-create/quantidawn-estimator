"""Quantidawn estimator web app: serves the UI and the pipeline behind it.

    ./.venv/bin/python server.py        then open http://127.0.0.1:5050
"""
import hmac
import math
import html
import io
import json
import os
import re
import secrets
import tempfile
import time
from urllib.parse import quote, urlparse
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fitz
from flask import Flask, Response, abort, g, jsonify, redirect, request, send_file, send_from_directory, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename

from core import auth, shares
from core import logic_reader, markup_pdf, reader
from core.dupes import find_duplicates
from core.price_matcher import load_price_list, price_lines
from core.project import DATA, ROOT, SAMPLES, _scale_json, apply_entries, demo_ai, from_json, native_z, render_pages, sample_project
from core.render import render_crop
from core.store import blob_file, get_store
from core.scale import find_scale, paper_of
from core.takeoff_builder import PROFILES
from core.template_filler import fill_template, match_lines
from core.template_reader import read_template
from core.templates.structural_takeoff import write_structural_takeoff

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 400 * 1024 * 1024
SECURE = os.environ.get("QD_SECURE_COOKIES") == "1"          # set to 1 when served over HTTPS
app.config.update(SECRET_KEY=auth.secret_key(), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=SECURE, SESSION_COOKIE_NAME="qd_session", PERMANENT_SESSION_LIFETIME=12 * 3600)
if os.environ.get("QD_TRUST_PROXY") == "1":                   # behind Caddy / nginx / a platform load balancer
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
THROTTLE = auth.Throttle()
PUBLIC_PREFIXES = ("/login", "/forgot", "/reset", "/auth/", "/fonts/", "/d/", "/healthz", "/favicon.ico", "/brand/", "/manifest.webmanifest", "/apple-touch-icon", "/offline", "/sw.js")
WEB = ROOT / "web"
_su = urlparse(os.environ.get("SUPABASE_URL", ""))
STORAGE_ORIGIN = f" {_su.scheme}://{_su.netloc}" if _su.netloc and os.environ.get("SUPABASE_SERVICE_KEY") else ""   # signed image and upload links
ID_RE = re.compile(r"^[a-z0-9-]{3,40}$")


def login_token_ok(sent):
    """Constant-time check of the sign-in form token. An empty or missing session token never matches (a default value
    would let a request with no cookie send the same default)."""
    want = session.get("lcsrf") or ""
    return bool(want) and hmac.compare_digest(str(sent or ""), want)


def clean_label(name, limit=80):
    """File names become project names and sheet labels shown in the UI: drop markup and control characters."""
    return re.sub(r"[<>&\"'`\x00-\x1f]", "", str(name or "")).strip()[:limit] or "plan"


def parse_lines(rows):
    try:
        return [from_json(r) for r in rows]
    except (KeyError, TypeError, ValueError, AttributeError):
        abort(400, description="Those takeoff lines are not in the expected format.")


def check_id(pid):
    if not ID_RE.match(pid):
        abort(404)
    return pid


def load_project(pid):
    check_id(pid)
    if pid in SAMPLES:
        return sample_project(pid)
    p = get_store().doc_get("project", pid)
    if p is None:
        abort(404)
    st = get_store()
    ed, ai, lb = st.doc_get("entries", pid), st.doc_get("ai", pid), st.doc_get("labels", pid)
    return apply_entries(p, ed or {}, ai, lb) if (ed or ai) else p


def blob_response(key, max_age=3600):
    """Serve a stored image: a redirect to a short-lived signed link on Supabase (keeps big files off the server),
    or the file itself on a local disk."""
    st = get_store()
    url = st.url(key, 3600)
    if url:
        resp = redirect(url, 302)
        resp.headers["Cache-Control"] = f"private, max-age={max_age // 2}"
        return resp
    f = st.local_path(key)
    if not f:
        abort(404)
    return send_from_directory(f.parent, f.name, max_age=max_age)


def render_png(pdf, idx, zoom=2):
    doc = fitz.open(pdf)
    try:
        return doc[idx].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
    finally:
        doc.close()


# ---------------------------------------------------------------- pages and UI
@app.get("/")
def index():
    text = (WEB / "index.html").read_text()
    meta = (f'<meta name="csrf-token" content="{html.escape(session.get("csrf", ""), quote=True)}">'
            f'<meta name="qd-user" content="{html.escape(g.user or "", quote=True)}">')
    return Response(text.replace("<!--SESSION-->", meta), mimetype="text/html")


@app.get("/fonts/<name>")
def font_file(name):
    """Geist and Geist Mono, bundled so the app needs no internet."""
    if not re.match(r"^[A-Za-z0-9_-]+\.(woff2|css)$", name):
        abort(404)
    return send_from_directory(WEB / "fonts", name, max_age=86400)


@app.get("/pages/<pid>/<name>")
def page_image(pid, name):
    check_id(pid)
    if not re.match(r"^p\d{1,3}\.png$", name):
        abort(404)
    if pid in SAMPLES:
        return send_from_directory(DATA / "pages" / pid, name, max_age=3600)
    n = int(name[1:-4])
    key = f"pages/{pid}/{name}"
    st = get_store()
    if not st.exists(key):                       # pages are drawn on first view, then kept
        pdf, idx = page_source(pid, n)
        try:
            st.put(key, render_png(pdf, idx), "image/png")
        except (IndexError, RuntimeError):
            abort(404)
    return blob_response(key)


def page_source(pid, n):
    """(pdf path, 0-based page index) for page number n of a project."""
    if pid in SAMPLES:
        return ROOT / "samples" / SAMPLES[pid] / "marked_up.pdf", n - 1
    p = load_project(pid)
    if not 1 <= n <= len(p["pages"]):
        abort(404)
    src = p["pages"][n - 1]["src"]
    pdf = blob_file(f"pdf/{pid}/{src['pdf']}")
    if not pdf:
        abort(404)
    return pdf, src["page"]


@app.get("/tile/<pid>/<int:n>")
def tile(pid, n):
    """A sharp crop of one page at the requested zoom, capped at the PDF's own resolution."""
    check_id(pid)
    pdf, idx = page_source(pid, n)
    try:
        x, y, w, h, z = (float(request.args[k]) for k in ("x", "y", "w", "h", "z"))
    except (KeyError, ValueError):
        abort(400)
    if not all(math.isfinite(v) for v in (x, y, w, h, z)) or w <= 0 or h <= 0 or z <= 0 or max(w, h) > 20000 or abs(x) > 1e6 or abs(y) > 1e6 or z > 100:
        abort(400)
    key = f"{n}_{round(x)}_{round(y)}_{round(w)}_{round(h)}_{round(z * 2) / 2}"
    skey, st = f"tiles/{pid}/{key}.png", get_store()
    if not st.exists(skey):
        try:
            png, _, _ = render_crop(pdf, idx, x, y, w, h, z)
        except (ValueError, IndexError, RuntimeError, OverflowError):
            abort(400)
        if st.kind == "supabase" and len(png) < 3_500_000:
            # small tiles go straight back (the browser caches them) so zooming around does not fill the storage quota
            resp = Response(png, mimetype="image/png")
            resp.headers["Cache-Control"] = "private, max-age=86400"
            return resp
        st.put(skey, png, "image/png")
    return blob_response(skey, 86400)


# ---------------------------------------------------------------- projects
@app.get("/api/projects")
def projects():
    out = []
    for pid in SAMPLES:
        meta = json.load(open(ROOT / "samples" / SAMPLES[pid] / "meta.json"))
        out.append({"id": pid, "name": meta["name"], "sample": True, "note": "sample"})
    for _, p, _ts in get_store().doc_list("project"):
        out.append({"id": p["id"], "name": p["name"], "sample": False, "note": f"{len(p['pages'])} sheets", "owner": p.get("owner", "")})
    return jsonify(out)


def build_project(pid, entries):
    """entries: [(original file name, stored pdf name)]. Reads each stored PDF's page sizes and scale; pages are drawn lazily."""
    pages = []
    for fi, (orig, stored) in enumerate(entries):
        path = blob_file(f"pdf/{pid}/{stored}")
        try:
            doc = fitz.open(path)
        except Exception:
            raise ValueError(f"{orig} could not be read as a PDF.")
        for i, page in enumerate(doc):
            w, h = page.rect.width, page.rect.height
            idx = len(pages) + 1
            pages.append({"n": clean_label(Path(orig).stem, 18), "t": f"page {i + 1}", "w": w, "h": h, "paper": paper_of(w, h),
                          "img": f"/pages/{pid}/p{idx}.png", "scale": _scale_json(page.get_text(), w, h, field=False), "c": 0,
                          "max_z": native_z(page), "src": {"pdf": stored, "page": i}})
        doc.close()
    proj = {"id": pid, "name": clean_label(Path(entries[0][0]).stem), "engineer": "", "rev": "", "status": "Uploaded",
            "sample": False, "owner": g.user or "", "created": int(time.time()),
            "profile": "westgarth", "groups": [], "pages": pages, "lines": [],
            "skipped": [], "ai": "not run",
            "message": "Pages are ready. Press Read markups to pull the legend quantities off the marked-up sheets."}
    get_store().doc_put("project", pid, proj)
    return proj


def stored_name(fi, name):
    base = (secure_filename(name) or f"plan{fi}.pdf")[-80:]
    return f"{fi}-{base}"


@app.post("/api/uploads/init")
def upload_init():
    """Hosted mode: hand the browser one-time links so big PDFs go straight to storage (the server never carries them)."""
    names = (request.get_json(force=True, silent=True) or {}).get("names")
    if not isinstance(names, list) or not 1 <= len(names) <= 20 or not all(isinstance(n, str) and n.lower().endswith(".pdf") and len(n) <= 200 for n in names):
        return jsonify(error="Choose at least one PDF file."), 400
    pid = "p-" + uuid.uuid4().hex[:10]
    st, files = get_store(), []
    for fi, n in enumerate(names):
        up = st.signed_upload(f"pdf/{pid}/{stored_name(fi, n)}")
        if up is None:
            return jsonify(direct=False)
        files.append(up)
    return jsonify(direct=True, id=pid, files=files)


@app.post("/api/uploads/finalize")
def upload_finalize():
    body = request.get_json(force=True, silent=True) or {}
    pid, names = str(body.get("id", "")), body.get("names")
    check_id(pid)
    if not isinstance(names, list) or not 1 <= len(names) <= 20 or not all(isinstance(n, str) and n.lower().endswith(".pdf") and len(n) <= 200 for n in names):
        abort(400)
    st = get_store()
    if st.doc_get("project", pid) is not None:
        abort(409)
    entries = [(n, stored_name(fi, n)) for fi, n in enumerate(names)]
    if not all(st.exists(f"pdf/{pid}/{stored}") for _, stored in entries):
        return jsonify(error="The upload did not finish. Try again."), 400
    try:
        build_project(pid, entries)
    except ValueError as exc:
        st.delete_prefix(f"pdf/{pid}")
        return jsonify(error=str(exc)), 400
    return jsonify(id=pid)


@app.post("/api/projects")
def upload_project():
    """Local mode: PDFs arrive with the request."""
    files = [f for f in request.files.getlist("files") if f.filename.lower().endswith(".pdf") and len(f.filename) <= 200][:20]
    if not files:
        return jsonify(error="Choose at least one PDF file."), 400
    pid, st, entries = "p-" + uuid.uuid4().hex[:10], get_store(), []
    for fi, f in enumerate(files):
        stored = stored_name(fi, f.filename)
        st.put(f"pdf/{pid}/{stored}", f.read(), "application/pdf")
        entries.append((f.filename, stored))
    try:
        build_project(pid, entries)
    except ValueError as exc:
        st.delete_prefix(f"pdf/{pid}")
        return jsonify(error=str(exc)), 400
    return jsonify(id=pid)


def _uploaded(pid):
    check_id(pid)
    if pid in SAMPLES:
        abort(400, description="The sample projects already have their lines.")
    p = get_store().doc_get("project", pid)
    if p is None:
        abort(404)
    try:
        n = int((request.get_json(force=True, silent=True) or {}).get("page"))
    except (TypeError, ValueError):
        abort(400)
    if not 1 <= n <= len(p["pages"]):
        abort(404)
    return p, n


@app.post("/api/project/<pid>/read")
def read_markups(pid):
    """The app's own logic, one sheet per request: embedded legend text and annotations first, then colour measuring."""
    p, n = _uploaded(pid)
    pdf, idx = page_source(pid, n)
    emb = logic_reader.read_embedded(pdf, idx)
    measures, note = [], ""
    if not emb["entries"]:                                  # flattened page: measure the coloured markups from the sheet's scale
        try:
            ratio = float((request.get_json(force=True, silent=True) or {}).get("ratio"))        # the scale as set on screen
        except (TypeError, ValueError):
            ratio = (p["pages"][n - 1].get("scale") or {}).get("ratio")
        if ratio is not None and not (math.isfinite(ratio) and 1 <= ratio <= 10000):
            ratio = None
        measures, note = logic_reader.measure_colours(pdf, idx, ratio)
        if not ratio:
            note = f"Sheet {n}: set its scale first (Enter scale), then press Read markups again."
    st = get_store()
    doc = st.doc_get("entries", pid) or {"pages": {}}
    doc["pages"][str(n)] = {"source": emb["source"] or ("colour" if measures else ""), "entries": emb["entries"],
                            "measures": measures, "note": note, "read": int(time.time())}
    st.doc_put("entries", pid, doc)
    return jsonify(page=n, source=doc["pages"][str(n)]["source"], found=len(emb["entries"]), measured=len(measures), note=note)


@app.post("/api/project/<pid>/ai")
def ai_read_markups(pid):
    """Optional second opinion: the AI reads the legend. It only cross-checks the logic result (or fills in when logic found no legend).

    Token saving: a sheet with no coloured markup and no embedded legend is skipped without a call (send force to read it anyway);
    where the legend was found is remembered per sheet, so reading it again looks at that area only (send fresh to start over)."""
    p, n = _uploaded(pid)
    body = request.get_json(force=True, silent=True) or {}
    pdf, idx = page_source(pid, n)
    if not body.get("force") and not logic_reader.has_markup(pdf, idx) and not logic_reader.read_embedded(pdf, idx)["entries"]:
        return jsonify(page=n, skipped=True, found=0, reason="no coloured markup on this sheet")
    st = get_store()
    doc = st.doc_get("ai", pid) or {"pages": {}}
    box = body.get("box") or (None if body.get("fresh") else (doc["pages"].get(str(n)) or {}).get("legend_box"))
    try:
        res = reader.read_page_entries(pdf, idx, box=box) if box else reader.read_page_entries(pdf, idx)
    except reader.ReaderError as exc:
        return jsonify(error=str(exc)), 502
    doc["pages"][str(n)] = {**res, "read": int(time.time())}
    st.doc_put("ai", pid, doc)
    return jsonify(page=n, sheet=res["sheet"], found=len(res["entries"]), skipped=False, area_only=bool(box and res.get("legend_box") == box))


@app.post("/api/project/<pid>/scale")
def detect_scale(pid):
    """Read the scale a sheet states. Printed text is read for free; a flattened sheet (picture only) goes to the AI, which only
    transcribes the words it sees. The ratio is then worked out by the same rules as above and a person still confirms it."""
    p, n = _uploaded(pid)
    pdf, idx = page_source(pid, n)
    page = p["pages"][n - 1]
    doc = fitz.open(pdf)
    try:
        text = doc[idx].get_text()
    finally:
        doc.close()
    by = "text"
    if not find_scale(text):
        try:
            text, by = reader.read_scale_text(pdf, idx), "ai"
        except reader.ReaderError as exc:
            return jsonify(error=str(exc)), 502
    sc = _scale_json(text, page["w"], page["h"], field=False)
    if by == "ai" and sc["ratio"]:
        sc["src"] = "read from the sheet by AI: " + sc["src"]
    return jsonify(page=n, by=by, found=bool(sc["ratio"]), scale=sc)


@app.put("/api/project/<pid>/label")
def name_markup(pid):
    """Give a colour-measured group a real name (a person identifies what each colour is)."""
    check_id(pid)
    body = request.get_json(force=True, silent=True) or {}
    key, label = str(body.get("key", "")), clean_label(body.get("label"), 60)
    if not re.match(r"^\d{1,3}c\d{1,6}$", key):
        abort(400)
    st = get_store()
    doc = st.doc_get("labels", pid) or {}
    doc[key] = label
    st.doc_put("labels", pid, doc)
    return jsonify(ok=True)


@app.delete("/api/project/<pid>/read")
def clear_markups(pid):
    check_id(pid)
    for coll in ("entries", "ai", "labels"):
        get_store().doc_delete(coll, pid)
    return jsonify(ok=True)


@app.get("/api/project/<pid>")
def project(pid):
    p = load_project(pid)
    if request.args.get("demo_ai") and p["lines"]:
        p = demo_ai(p)
    p["state"] = None if request.args.get("demo_ai") else get_store().doc_get("state", pid)
    dups = find_duplicates(p["lines"])                   # look-alike lines are only flagged for a person; nothing is removed
    for l in p["lines"]:
        if l["id"] in dups:
            l["dup"] = dups[l["id"]]
    return jsonify(p)


@app.put("/api/project/<pid>/state")
def put_state(pid):
    check_id(pid)
    load_project(pid)
    if request.content_length and request.content_length > 2_000_000:
        return jsonify(error="That much saved work is too large to store."), 413
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify(error="bad state"), 400
    base = data.pop("base", None)           # the `saved` stamp this browser last loaded or wrote
    force = bool(data.pop("force", False))
    cur = get_store().doc_get("state", pid)
    if not force and base is not None and cur and abs(float(cur.get("saved", 0)) - float(base)) > 1e-6:
        # someone else saved since this browser loaded: refuse rather than silently replace their work
        return jsonify(error="conflict", saved=cur.get("saved"), by=cur.get("by", "")), 409
    data["saved"] = time.time()
    data["by"] = g.user or ""
    get_store().doc_put("state", pid, data)
    return jsonify(ok=True, saved=data["saved"])


@app.delete("/api/project/<pid>/state")
def reset_state(pid):
    check_id(pid)
    get_store().doc_delete("state", pid)
    return jsonify(ok=True)


# ---------------------------------------------------------------- export and client templates
def lines_from_request():
    body = request.get_json(force=True, silent=True) or {}
    rows = body.get("lines")
    if not isinstance(rows, list):
        abort(400)
    return parse_lines(rows)


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def export_bytes(p, lines, pricing):
    path = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}.xlsx"
    write_structural_takeoff(lines, str(path), [], PROFILES[p["profile"]], pricing=pricing)
    data = path.read_bytes()
    path.unlink()
    return data


@app.post("/api/project/<pid>/export")
def export(pid):
    p = load_project(pid)
    lines = lines_from_request()
    if not lines:
        return jsonify(error="There are no takeoff lines to export yet."), 400
    body = request.get_json(force=True, silent=True) or {}
    return send_file(io.BytesIO(export_bytes(p, lines, body.get("pricing"))), as_attachment=True,
                     download_name=f"{secure_filename(p['name']) or pid}-takeoff.xlsx", mimetype=XLSX)


def markup_key(pid):
    return f"markup/{pid}/marked-up.pdf"


@app.post("/api/project/<pid>/markup-pdf")
def markup_pdf_export(pid):
    """Draw the measured shapes onto the sheets and store the PDF. The file is big (it is the whole drawing set), so the
    browser gets a link to fetch it from, the same way page images are served, instead of the bytes in this response."""
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    shapes = markup_pdf.clean_shapes(body.get("shapes"))
    if not shapes:
        return jsonify(error="There are no measured shapes to put on the drawing yet."), 400
    sources = [page_source(pid, n) for n in range(1, len(p["pages"]) + 1)]
    data = markup_pdf.build(sources, shapes, f"{p['name']}: measured in QuantiDawn Estimator, {time.strftime('%d %b %Y')}")
    get_store().put(markup_key(pid), data, "application/pdf")
    return jsonify(url=f"/api/project/{pid}/markup-pdf?v={int(time.time())}", name=f"{secure_filename(p['name']) or pid}-marked-up.pdf")


@app.get("/api/project/<pid>/markup-pdf")
def markup_pdf_file(pid):
    p = load_project(pid)
    name = f"{secure_filename(p['name']) or pid}-marked-up.pdf"
    st = get_store()
    url = st.url(markup_key(pid), 600)
    if url:
        return redirect(url + "&download=" + name, 302)
    f = st.local_path(markup_key(pid))
    if not f:
        abort(404)
    return send_file(f, as_attachment=True, download_name=name, mimetype="application/pdf")


@app.get("/pricing/example.csv")
def example_prices():
    return send_file(ROOT / "samples" / "pricing" / "EXAMPLE_price_list_not_real_rates.csv", as_attachment=True,
                     download_name="example_price_list_not_real_rates.csv")


@app.post("/api/project/<pid>/prices")
def upload_prices(pid):
    """Match this project's lines to a client's price list. Nothing is stored as a rate until a person accepts it."""
    check_id(pid)
    load_project(pid)
    f = request.files.get("prices")
    ext = Path(f.filename).suffix.lower() if f else ""
    if ext not in (".csv", ".xlsx"):
        return jsonify(error="Upload a .csv or .xlsx price list."), 400
    path = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex[:12]}{ext}"
    f.save(path)
    try:
        entries = load_price_list(path)
    except Exception as exc:
        return jsonify(error=str(exc)), 400
    finally:
        path.unlink(missing_ok=True)
    try:
        rows = json.loads(request.form.get("lines", "[]"))
    except ValueError:
        rows = []
    res = price_lines(parse_lines(rows), entries)
    return jsonify(name=f.filename, entries=len(entries),
                   matches=[{"id": r["id"], "status": m.status, "rate": m.rate, "entry": m.entry,
                             "score": m.score, "note": m.note, "lhpu": m.labour_hours, "lrate": m.labour_rate}
                            for r, m in zip(rows, res)])


def template_path(pid, tid):
    check_id(pid)
    if not re.match(r"^[a-f0-9]{12}$", tid):
        abort(404)
    f = blob_file(f"templates/{pid}/{tid}.xlsx")
    if not f:
        abort(404)
    return f


@app.post("/api/project/<pid>/template")
def upload_template(pid):
    check_id(pid)
    load_project(pid)
    f = request.files.get("template")
    if not f or not f.filename.lower().endswith(".xlsx"):
        return jsonify(error="Upload an .xlsx file."), 400
    tid = uuid.uuid4().hex[:12]
    key = f"templates/{pid}/{tid}.xlsx"
    get_store().put(key, f.read(), XLSX)
    try:
        tpl = read_template(str(blob_file(key)))
    except Exception as exc:
        get_store().delete_prefix(key)
        return jsonify(error=f"That file could not be read as a workbook: {exc}"), 400
    try:
        rows = json.loads(request.form.get("lines", "[]"))
    except ValueError:
        rows = []
    lines = parse_lines(rows)
    items = [i for i in tpl.line_items() if i.input_cells]
    matches, un_lines, un_items = match_lines(items, lines)
    return jsonify(
        id=tid, name=f.filename,
        sheets=[{"name": s.name, "kind": s.kind, "items": len(s.items),
                 "typed": sum(1 for i in s.items if i.input_cells), "columns": sorted(s.columns)} for s in tpl.sheets],
        matches=[{"line": m.line.description[:60], "mark": m.line.mark, "sheet": m.item.sheet, "row": m.item.row,
                  "by": m.why, "score": m.score} for m in matches],
        unmatched_lines=len(un_lines), unfilled_rows=len(un_items))


def fill_bytes(src, lines):
    out = Path(tempfile.gettempdir()) / f"{uuid.uuid4().hex}.xlsx"
    rep = fill_template(str(src), str(out), lines)
    data = out.read_bytes()
    out.unlink()
    return data, rep


@app.post("/api/project/<pid>/template/<tid>/fill")
def fill(pid, tid):
    src = template_path(pid, tid)
    data, rep = fill_bytes(src, lines_from_request())
    resp = send_file(io.BytesIO(data), as_attachment=True, download_name="client-template-filled.xlsx", mimetype=XLSX)
    resp.headers["X-Fill-Report"] = json.dumps({k: rep[k] for k in ("matched", "cells_written", "rates_written", "labour_written", "unmatched_lines", "unfilled_rows")}
                                               | {"skipped": len(rep["skipped"])})
    resp.headers["Access-Control-Expose-Headers"] = "X-Fill-Report"
    return resp


# ---------------------------------------------------------------- team login
def current_user():
    return session.get("user") if auth.auth_required() else "local"


def safe_next(n):
    return n if n and n.startswith("/") and not n.startswith("//") and "\\" not in n else "/"


@app.before_request
def gate():
    g.user = current_user()
    if not auth.auth_required() or request.path.startswith(PUBLIC_PREFIXES):
        return None
    if not session.get("user"):
        if request.path.startswith(("/api/", "/tile/", "/pages/", "/pricing/")):
            return jsonify(error="Sign in required"), 401
        return redirect("/login?next=" + safe_next(request.full_path.rstrip("?")))
    if auth.supabase_auth() and time.time() - session.get("chk", 0) > 600:        # still on the team? (removed people lose access within minutes)
        if not auth.still_member(session["user"], session.get("uid", "")):
            session.clear()
            return jsonify(error="Sign in required"), 401
        session["chk"] = time.time()
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        sent = request.headers.get("X-CSRF-Token", "")
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            return jsonify(error="Your session check failed. Reload the page and try again."), 403
    return None


@app.errorhandler(Exception)
def api_error(exc):
    """The browser app expects JSON from /api/: never hand it an HTML error page or a stack trace."""
    from werkzeug.exceptions import HTTPException
    if isinstance(exc, HTTPException):
        code, msg = exc.code or 500, exc.description
        if code == 413:
            msg = "That file is too large to upload here (the limit is about 4 MB for this kind of file)."
    else:
        app.logger.exception("unhandled error")
        code, msg = 500, "Something went wrong on the server. Try again."
    if request.path.startswith(("/api/", "/tile/", "/pages/")):
        return jsonify(error=msg), code
    return (exc, code) if isinstance(exc, HTTPException) else (Response("Something went wrong.", mimetype="text/plain"), 500)


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        f"img-src 'self' data: blob:{STORAGE_ORIGIN}; font-src 'self'; connect-src 'self'{STORAGE_ORIGIN}; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'")
    if SECURE:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.path.startswith(("/api/", "/d/", "/login", "/forgot", "/reset", "/auth/")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def page(name, **vals):
    text = (WEB / name).read_text()
    text = text.replace("<!--GOOGLE-->", GOOGLE_BTN if auth.supabase_auth() else "")
    for k, v in vals.items():
        text = text.replace("{{" + k + "}}", html.escape(str(v), quote=True))
    return Response(text, mimetype="text/html")


GOOGLE_BTN = ('<div class="or"><span>or</span></div>'
              '<a class="gbtn" href="/login/google"><svg class="gl" viewBox="0 0 48 48" aria-hidden="true"><path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/><path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/><path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/><path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/></svg><span>Continue with Google</span></a>')


@app.get("/login/google")
def login_google():
    if not auth.supabase_auth():
        abort(404)
    session.setdefault("lcsrf", secrets.token_hex(16))
    return redirect(os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/authorize?provider=google&redirect_to="
                    + quote(base_url() + "/auth/callback", safe=""))


@app.get("/auth/callback")
def auth_callback():
    """Supabase sends the browser back here with the sign-in token in the URL fragment (never seen by a server),
    so a few lines of script pass it on."""
    if not auth.supabase_auth():
        abort(404)
    session.setdefault("lcsrf", secrets.token_hex(16))
    html_ = ("<!doctype html><meta charset=utf-8><title>Signing in</title><body style=\"font:15px system-ui;padding:40px\">"
             "<p id=m>Signing you in...</p><script>(function(){var h=new URLSearchParams(location.hash.slice(1)),"
             "q=new URLSearchParams(location.search),t=h.get('access_token');if(h.get('type')==='recovery'&&t){location.replace('/reset'+location.hash);return}history.replaceState(null,'','/auth/callback');"
             "function bad(){document.getElementById('m').innerHTML='Google sign-in did not work. <a href=\"/login\">Back to sign in</a>'}"
             "if(!t){bad();return}fetch('/auth/session',{method:'POST',headers:{'Content-Type':'application/json','X-Login-Token':'"
             + session["lcsrf"] + "'},body:JSON.stringify({access_token:t})}).then(function(r){return r.json().then(function(j){"
             "if(r.ok)location.replace('/');else document.getElementById('m').textContent=j.error||'Not allowed.'})}).catch(bad)})()</script>")
    return Response(html_, mimetype="text/html")


@app.post("/auth/session")
def auth_session():
    if not auth.supabase_auth() or not login_token_ok(request.headers.get("X-Login-Token")):
        return jsonify(error="Sign-in check failed. Start again from the sign-in page."), 403
    key = f"{request.remote_addr}|google"
    if THROTTLE.locked_for(key):
        return jsonify(error="Too many attempts. Try again later."), 429
    who = auth.verify_access_token((request.get_json(force=True, silent=True) or {}).get("access_token"))
    if not who:
        THROTTLE.fail(key)
        return jsonify(error="That Google account is not on the estimator team. Ask an administrator."), 403
    THROTTLE.ok(key)
    session.clear()
    session.permanent = True
    session["user"], session["csrf"], session["uid"], session["chk"] = who[0], secrets.token_hex(16), who[1], time.time()
    return jsonify(ok=True)


@app.get("/forgot")
def forgot_page():
    session.setdefault("lcsrf", secrets.token_hex(16))
    return page("forgot.html", CSRF=session["lcsrf"], MSG="", ERROR="")


@app.post("/forgot")
def forgot_submit():
    session.setdefault("lcsrf", secrets.token_hex(16))
    if not login_token_ok(request.form.get("csrf")):
        return redirect("/forgot")
    if not auth.supabase_auth():
        return page("forgot.html", CSRF=session["lcsrf"], MSG="", ERROR="Passwords here are managed by an administrator. Ask them to reset yours.")
    key = f"{request.remote_addr}|forgot"
    if THROTTLE.locked_for(key):
        return page("forgot.html", CSRF=session["lcsrf"], MSG="", ERROR="Too many requests. Try again in a few minutes."), 429
    THROTTLE.fail(key)                                  # at most 5 requests per 10 minutes per address
    auth.request_reset(request.form.get("email", ""), base_url() + "/auth/callback")
    return page("forgot.html", CSRF=session["lcsrf"], ERROR="",
                MSG="If that email belongs to the team, a reset link is on its way. Check your inbox and spam folder.")


@app.get("/reset")
def reset_page():
    if not auth.supabase_auth():
        abort(404)
    session.setdefault("lcsrf", secrets.token_hex(16))
    return page("reset.html", TOKEN=session["lcsrf"])


@app.post("/reset/submit")
def reset_submit():
    if not auth.supabase_auth() or not login_token_ok(request.headers.get("X-Login-Token")):
        return jsonify(error="The check failed. Open the link from your email again."), 403
    key = f"{request.remote_addr}|reset"
    if THROTTLE.locked_for(key):
        return jsonify(error="Too many attempts. Try again later."), 429
    body = request.get_json(force=True, silent=True) or {}
    ok, msg = auth.set_password(str(body.get("access_token", "")), str(body.get("password", "")))
    if not ok:
        THROTTLE.fail(key)
        return jsonify(error=msg), 400
    THROTTLE.ok(key)
    return jsonify(ok=True)


@app.get("/login")
def login_page():
    if not auth.auth_required():
        return redirect("/")
    session.setdefault("lcsrf", secrets.token_hex(16))     # keep it: a second load of this page must not invalidate the open form
    return page("login.html", NEXT=safe_next(request.args.get("next", "/")), CSRF=session["lcsrf"], ERROR="")


@app.post("/login")
def login_submit():
    name = (request.form.get("username") or "").strip().lower()
    key = f"{request.remote_addr}|{name}"
    nxt = safe_next(request.form.get("next", "/"))
    if not login_token_ok(request.form.get("csrf")):
        return redirect("/login")
    wait = THROTTLE.locked_for(key)
    if wait:
        return page("login.html", NEXT=nxt, CSRF=session["lcsrf"], ERROR=f"Too many attempts. Try again in {wait // 60 + 1} minutes."), 429
    uid = auth.verify(name, request.form.get("password", ""))
    if uid:
        THROTTLE.ok(key)
        session.clear()
        session.permanent = True
        session["user"], session["csrf"] = name, secrets.token_hex(16)
        session["uid"], session["chk"] = (uid if isinstance(uid, str) else ""), time.time()
        return redirect(nxt)
    THROTTLE.fail(key)
    return page("login.html", NEXT=nxt, CSRF=session["lcsrf"], ERROR="That user name or password is not right."), 401


@app.post("/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(WEB / "brand", "favicon.ico", max_age=86400)


@app.get("/brand/<name>")
def brand_file(name):
    """QuantiDawn logos and icons (copied from quantidawn.com)."""
    if not re.match(r"^[a-z0-9-]+\.(png|ico)$", name):
        abort(404)
    return send_from_directory(WEB / "brand", name, max_age=86400)


@app.get("/manifest.webmanifest")
def manifest():
    resp = jsonify(id="/", name="QuantiDawn Estimator", short_name="Estimator", start_url="/", scope="/", display="standalone",
                   description="Construction takeoffs your team can check.", background_color="#f6f6f7", theme_color="#c2410c",
                   icons=[{"src": "/brand/favicon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                          {"src": "/brand/favicon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"}])
    resp.mimetype = "application/manifest+json"
    return resp


@app.get("/sw.js")
def service_worker():
    """Served from the root so it can control the whole app; never cached, so updates arrive at once."""
    resp = send_from_directory(WEB, "sw.js", max_age=0, mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


@app.get("/offline")
def offline_page():
    return Response((WEB / "offline.html").read_text(), mimetype="text/html")


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def apple_icon():
    return send_from_directory(WEB / "brand", "apple-touch-icon.png", max_age=86400)


@app.get("/healthz")
def healthz():
    return "ok"


# ---------------------------------------------------------------- client download links (no client account)
def base_url():
    return (os.environ.get("QD_BASE_URL") or request.host_url).rstrip("/")


def fmt_when(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%-d %b %Y, %H:%M UTC")


@app.post("/api/project/<pid>/share")
def create_share(pid):
    p = load_project(pid)
    body = request.get_json(force=True, silent=True) or {}
    lines = lines_from_request()
    safe = secure_filename(p["name"]) or pid
    if body.get("source") == "template":
        data, _ = fill_bytes(template_path(pid, str(body.get("template_id", ""))), lines)
        filename = f"{safe}-client-template.xlsx"
    else:
        if not lines:
            return jsonify(error="There are no takeoff lines to share yet."), 400
        data, filename = export_bytes(p, lines, body.get("pricing")), f"{safe}-takeoff.xlsx"
    try:
        hours, limit = int(body.get("hours", 168)), max(0, int(body.get("max_downloads", 0)))
    except (TypeError, ValueError):
        return jsonify(error="Check the expiry and download limit."), 400
    token, meta = shares.create(pid, p["name"], data, filename, hours, limit, str(body.get("note", "")), g.user or "")
    return jsonify(url=f"{base_url()}/d/{token}", id=meta["id"], expires=meta["expires"], file=filename)


@app.get("/api/project/<pid>/shares")
def list_shares(pid):
    check_id(pid)
    return jsonify([{k: m[k] for k in ("id", "file", "created", "expires", "count", "max", "state", "note", "by")}
                    for m in shares.list_for_project(pid)])


@app.delete("/api/share/<sid>")
def revoke_share(sid):
    if not shares.revoke(sid):
        abort(404)
    return jsonify(ok=True)


def share_gone(meta):
    st = shares.state(meta) if meta else "missing"
    msg = {"missing": "This link is not valid.", "revoked": "This link has been withdrawn.",
           "expired": "This link has expired.", "used up": "This link has already been used the allowed number of times."}[st]
    return page("share.html", STATE="gone", TITLE="Link unavailable", MSG=msg + " Ask the sender for a new one.",
                FILE="", SIZE="", EXPIRES="", URL=""), (404 if st == "missing" else 410)


@app.get("/d/<token>")
def share_page(token):
    meta = shares.get_by_token(token)
    if not meta or shares.state(meta) != "active":
        return share_gone(meta)
    size = f"{meta['size'] / 1024:,.0f} KB" if meta["size"] < 1024 * 1024 else f"{meta['size'] / 1048576:,.1f} MB"
    return page("share.html", STATE="ok", TITLE="Your file is ready", MSG="", FILE=meta["file"], SIZE=size,
                EXPIRES=fmt_when(meta["expires"]), URL=f"/d/{token}/file")


@app.get("/d/<token>/file")
def share_file(token):
    meta = shares.get_by_token(token)
    if not meta or shares.state(meta) != "active":
        return share_gone(meta)
    if not shares.record_download(meta):
        return share_gone({**meta, "count": meta["max"]})
    data = shares.file_bytes(meta)
    if data is None:
        return share_gone(None)
    resp = send_file(io.BytesIO(data), as_attachment=True, download_name=meta["file"], mimetype=XLSX)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


if __name__ == "__main__":
    app.run(host=os.environ.get("QD_HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5050")), debug=False)
