"""Create the first administrator locally, even if ordinary users registered first."""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys

from . import main


def run() -> None:
    parser = argparse.ArgumentParser(description="Create Paperlight's first administrator")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    if any(user['role'] == 'admin' for user in main.ACCOUNTS.list_users()):
        raise SystemExit("An administrator already exists; use the administrator interface to manage users.")
    if sys.stdin.isatty():
        password = getpass.getpass("Initial administrator password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match; no account was created.")
    else:
        # Retain support for secrets supplied over stdin, never command arguments.
        password = sys.stdin.readline().rstrip("\r\n")
    try:
        admin = main.ACCOUNTS.create_user(args.username, password, "admin", initial_admin=True)
    except sqlite3.IntegrityError as exc:
        raise SystemExit("Username already exists; choose another --username. Existing accounts are not promoted.") from exc
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    main._migrate_legacy_documents(admin["id"])
    print(f"Created administrator {admin['username']}; existing server documents are assigned to this account.")


if __name__ == "__main__":
    run()
