"""Create or replace an RBE user without putting passwords in source control."""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import os
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
USERS_PATH = Path(os.getenv("RBE_USERS_PATH", SITE / "secrets" / "users.json"))


def password_hash(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    return "600000${}${}".format(
        base64.urlsafe_b64encode(salt).decode().rstrip("="),
        base64.urlsafe_b64encode(digest).decode().rstrip("="),
    )


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[2] not in {"admin", "viewer"}:
        raise SystemExit("Usage: python scripts/create_user.py LOGIN admin|viewer")
    username, role = sys.argv[1].strip(), sys.argv[2]
    if not username or any(char.isspace() for char in username):
        raise SystemExit("Login must not contain spaces")
    password = getpass.getpass("Password: ")
    repeated = getpass.getpass("Repeat password: ")
    if len(password) < 12:
        raise SystemExit("Password must be at least 12 characters")
    if password != repeated:
        raise SystemExit("Passwords do not match")
    try:
        users = json.loads(USERS_PATH.read_text(encoding="utf-8")) if USERS_PATH.exists() else {}
    except (OSError, ValueError) as error:
        raise SystemExit(f"Cannot read users file: {error}") from error
    users[username] = {"role": role, "password_hash": password_hash(password)}
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = USERS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(USERS_PATH)
    print(f"User {username} saved with role {role}")


if __name__ == "__main__":
    main()
