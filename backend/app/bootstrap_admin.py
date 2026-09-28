"""One-time local admin setup; read the initial password from standard input."""

from __future__ import annotations

import argparse
import sys

from . import main


def run() -> None:
    parser = argparse.ArgumentParser(description="Create Paperlight's first administrator")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    password = sys.stdin.readline().rstrip("\r\n")
    if main.ACCOUNTS.user_count():
        raise SystemExit("Accounts already exist; use the administrator interface to manage users.")
    admin = main.ACCOUNTS.create_user(args.username, password, "admin")
    main._migrate_legacy_documents(admin["id"])
    print(f"Created administrator {admin['username']}; existing server documents are assigned to this account.")


if __name__ == "__main__":
    run()
