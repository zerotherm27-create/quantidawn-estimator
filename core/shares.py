"""Expiring download links for client deliverables. Clients need no account.

The link token is shown once, at creation. Only its SHA-256 hash is stored, so a copy of the data folder does not
reveal working links. A link can expire, be limited to N downloads, or be revoked.
"""
import hashlib
import re
import secrets
import time

from core.store import get_store

COLL = "share"
MAX_HOURS = 90 * 24
SID_RE = re.compile(r"^[a-f0-9]{32}$")


def _sid(token):
    return hashlib.sha256(token.encode()).hexdigest()[:32]


def _bin(sid):
    return f"shares/{sid}.bin"


def _save(meta):
    get_store().doc_put(COLL, meta["id"], meta)


def create(project_id, project_name, data, filename, hours=168, max_downloads=0, note="", user=""):
    hours = max(1, min(int(hours), MAX_HOURS))
    token = secrets.token_urlsafe(32)
    sid = _sid(token)
    get_store().put(_bin(sid), data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    now = int(time.time())
    meta = {"id": sid, "project": project_id, "name": project_name, "file": filename, "size": len(data),
            "created": now, "expires": now + hours * 3600, "max": int(max_downloads), "count": 0, "last": None,
            "revoked": False, "note": note[:200], "by": user}
    _save(meta)
    return token, meta


def get_by_token(token):
    if not token or len(token) > 100:
        return None
    return get_store().doc_get(COLL, _sid(token))


def get_by_id(sid):
    if not SID_RE.match(sid or ""):
        return None
    return get_store().doc_get(COLL, sid)


def state(meta, now=None):
    now = now or time.time()
    if meta["revoked"]:
        return "revoked"
    if now >= meta["expires"]:
        return "expired"
    if meta["max"] and meta["count"] >= meta["max"]:
        return "used up"
    return "active"


def file_bytes(meta):
    return get_store().get(_bin(meta["id"]))


def record_download(meta):
    """Count one download atomically (so two people clicking at once cannot beat the limit). False if the limit is used up."""
    n = get_store().doc_bump(COLL, meta["id"], "count", meta["max"])
    if n is None:
        return False
    meta["count"] = n
    return True


def revoke(sid):
    meta = get_by_id(sid)
    if not meta:
        return False
    meta["revoked"] = True
    _save(meta)
    return True


def list_for_project(project_id):
    out = [{**m, "state": state(m)} for _, m, _ in get_store().doc_list(COLL, {"project": project_id})]
    return sorted(out, key=lambda m: m["created"], reverse=True)
