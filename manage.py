"""Manage team logins.

    ./.venv/bin/python manage.py adduser anna        (asks for a password)
    ./.venv/bin/python manage.py passwd anna
    ./.venv/bin/python manage.py deluser anna
    ./.venv/bin/python manage.py users

Hosted (Supabase) mode: who may sign in is the team list; their passwords are Supabase Auth accounts.
    SUPABASE_URL=... SUPABASE_SERVICE_KEY=... ./.venv/bin/python manage.py team add anna@quantidawn.com
    ... manage.py team remove anna@quantidawn.com        ... manage.py team list
"""
import getpass
import sys

from core import auth


def team(argv):
    if len(argv) < 3 or argv[2] not in ("add", "remove", "list"):
        print(__doc__)
        return 1
    try:
        if argv[2] == "list":
            print("\n".join(auth.team_list()) or "(nobody yet)")
        elif len(argv) < 4:
            print("Give an email address.")
            return 1
        elif argv[2] == "add":
            auth.team_add(argv[3])
            print(f"Added {argv[3].lower()}. They sign in with their Supabase Auth password.")
        else:
            auth.team_remove(argv[3])
            print(f"Removed {argv[3].lower()}.")
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1
    return 0


def main(argv):
    if len(argv) > 1 and argv[1] == "team":
        return team(argv)
    if len(argv) < 2 or argv[1] not in ("adduser", "passwd", "deluser", "users"):
        print(__doc__)
        return 1
    cmd = argv[1]
    if cmd == "users":
        for n, u in auth.load_users().items():
            print(f"{n}\t{u['role']}")
        return 0
    if len(argv) < 3:
        print("Give a user name.")
        return 1
    name = argv[2]
    try:
        if cmd == "deluser":
            auth.remove_user(name)
            print(f"Removed {name}.")
            return 0
        if cmd == "passwd" and name.lower() not in auth.load_users():
            print("No such user.")
            return 1
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Repeat it: "):
            print("The passwords do not match.")
            return 1
        auth.add_user(name, pw)
        print(f"Saved {name}.")
    except (ValueError, KeyError) as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
