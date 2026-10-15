"""Teammates see a chosen name, never the sign-in email."""
from core import auth
from core.store import get_store

u = "name.test@example.com"
try:
    assert auth.display_name(u, {}) == "name.test"          # no name chosen: the part before the @, not the address
    assert auth.display_name("anna", {}) == "anna"          # local usernames are already names
    assert auth.display_name("", {}) == ""
    print("PASS fallback never shows the email")

    assert auth.set_display_name(u.upper(), "  Anna   Reyes ") == "Anna Reyes"
    assert auth.display_names()[u] == "Anna Reyes"
    assert auth.display_name(u) == "Anna Reyes"
    print("PASS chosen name stored and used (case-insensitive sign-in)")

    try:
        auth.set_display_name(u, "me@x.com")
        raise SystemExit("email accepted as a name")
    except ValueError:
        pass
    assert auth.set_display_name(u, "") == "" and u not in auth.display_names()
    print("PASS an email is refused as a name; empty clears it")

    assert get_store().doc_get("team", auth._team_id(u)) is None
    auth.set_display_name(u, "Anna")
    assert get_store().doc_get("team", auth._team_id(u)) is None
    print("PASS setting a name never grants team access")
finally:
    auth.set_display_name(u, "")

from server import app
r = app.test_client().post("/api/me/name", json={"name": "x"})
assert r.status_code in (400, 401, 403), r.status_code
print("PASS name endpoint needs a signed-in user")
print("ALL PASSED")
