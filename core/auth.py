"""Team login: users with hashed passwords, a throttle against password guessing, and the session secret.

Users live in data/users.json (created with manage.py). Once at least one user exists, or QD_REQUIRE_AUTH=1,
every page and API call needs a login. With no users and no QD_REQUIRE_AUTH the app runs open, for local use only.
"""
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from core.project import DATA

USERS_FILE = DATA / "users.json"
NAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
MIN_PASSWORD = 10
HASH_METHOD = "pbkdf2:sha256:600000"      # available on every Python build (scrypt is not on all)
_DUMMY = generate_password_hash("not-a-real-password", method=HASH_METHOD)


def _write_private(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def load_users():
    if not USERS_FILE.exists():
        return {}
    return json.loads(USERS_FILE.read_text())


def add_user(name, password, role="estimator"):
    name = name.strip().lower()
    if not NAME_RE.match(name):
        raise ValueError("User names are 3 to 32 characters: letters, numbers, dot, dash or underscore.")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"Use a password of at least {MIN_PASSWORD} characters.")
    users = load_users()
    users[name] = {"hash": generate_password_hash(password, method=HASH_METHOD), "role": role, "created": int(time.time())}
    _write_private(USERS_FILE, json.dumps(users, indent=1))


def remove_user(name):
    users = load_users()
    if users.pop(name.strip().lower(), None) is None:
        raise KeyError(name)
    _write_private(USERS_FILE, json.dumps(users, indent=1))


def supabase_auth():
    """Hosted mode: sign in with a Supabase Auth account, but only if the email is on this app's team list."""
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_ANON_KEY"))


def _team_id(email):
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:40]


def team_add(email, role="estimator"):
    from core.store import get_store
    email = email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("That does not look like an email address.")
    get_store().doc_put("team", _team_id(email), {"email": email, "role": role, "created": int(time.time())})


def team_remove(email):
    from core.store import get_store
    get_store().doc_delete("team", _team_id(email))


def team_list():
    from core.store import get_store
    return sorted(d["email"] for _, d, _ in get_store().doc_list("team"))


def _is_member(user_id, email):
    """On this app's team list, or a QuantiDawn website admin (public.admins). The project is shared with the
    website, so merely having a Supabase account is never enough."""
    from core.store import get_store
    st = get_store()
    if st.doc_get("team", _team_id(email)) is not None:
        return True
    if st.kind == "supabase" and user_id and re.match(r"^[0-9a-f-]{36}$", str(user_id)):
        try:
            return bool(st._r("GET", f"/rest/v1/admins?user_id=eq.{user_id}&select=user_id").json())
        except Exception:
            return False
    return False


def _auth_call(method, path, **kw):
    import requests
    try:
        return requests.request(method, os.environ["SUPABASE_URL"].rstrip("/") + path, timeout=15, **kw)
    except requests.RequestException:
        return None


def _verify_supabase(email, password):
    """Email + password against Supabase Auth, then the membership check. Supabase is always asked first so a
    non-member and a wrong password look the same."""
    email = (email or "").strip().lower()
    r = _auth_call("POST", "/auth/v1/token?grant_type=password", json={"email": email, "password": password or ""},
                   headers={"apikey": os.environ["SUPABASE_ANON_KEY"]})
    if r is None or r.status_code != 200:
        return False
    user = r.json().get("user") or {}
    return _is_member(user.get("id"), email)


def verify_access_token(token):
    """Google sign-in: the browser hands over the Supabase access token it received; ask Supabase who it belongs to.
    Returns the email if that person is a member, else None."""
    if not token or len(token) > 4000:
        return None
    r = _auth_call("GET", "/auth/v1/user", headers={"apikey": os.environ["SUPABASE_ANON_KEY"], "Authorization": f"Bearer {token}"})
    if r is None or r.status_code != 200:
        return None
    u = r.json()
    email = (u.get("email") or "").strip().lower()
    if not email or not u.get("email_confirmed_at"):
        return None
    return email if _is_member(u.get("id"), email) else None


def verify(name, password):
    """True only for a known user with the right password. Takes similar time either way."""
    if supabase_auth():
        return _verify_supabase(name, password)
    u = load_users().get((name or "").strip().lower())
    ok = check_password_hash(u["hash"] if u else _DUMMY, password or "")
    return bool(u) and ok


def auth_required():
    return os.environ.get("QD_REQUIRE_AUTH") == "1" or supabase_auth() or bool(load_users())


class Throttle:
    """Lock a user+address pair after repeated wrong passwords."""
    def __init__(self, max_fails=5, lock_seconds=600):
        self.max_fails, self.lock_seconds, self.state = max_fails, lock_seconds, {}

    def locked_for(self, key):
        fails, until = self.state.get(key, (0, 0))
        return max(0, int(until - time.time())) if fails >= self.max_fails else 0

    def fail(self, key):
        fails, until = self.state.get(key, (0, 0))
        fails += 1
        self.state[key] = (fails, time.time() + self.lock_seconds if fails >= self.max_fails else 0)

    def ok(self, key):
        self.state.pop(key, None)


def secret_key():
    env = os.environ.get("QD_SECRET_KEY")
    if env:
        return env
    if os.environ.get("VERCEL"):
        raise RuntimeError("Set QD_SECRET_KEY: sessions would break between server instances without it.")
    f = DATA / "secret_key"
    if not f.exists():
        _write_private(f, secrets.token_hex(32))
    return f.read_text().strip()
