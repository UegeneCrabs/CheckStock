"""Run locally on the server: python -m scripts.agent_key --help."""

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.agent_access import create_credential, read_credentials, save_credentials
from app.config import settings
from app.container import ApplicationContainer
from app.dto.identity import SectionName, UserId
from app.section_access import has_access


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage employee ChatGPT keys on the application server")
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue")
    issue.add_argument("--user-id", type=int, required=True)
    issue.add_argument("--days", type=int, default=30)
    issue.add_argument(
        "--output", type=Path, required=True, help="New private file to receive the plaintext key"
    )
    revoke = commands.add_parser("revoke")
    revoke.add_argument("key_id")
    commands.add_parser("list")
    args = parser.parse_args()
    path = settings.agent_tokens_path
    if args.command == "list":
        for record in read_credentials(path):
            print(f"{record.key_id} user={record.user_id} expires={record.expires_at.isoformat()}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    try:
        lock = lock_path.open("x")
    except FileExistsError:
        parser.error("Another key-management operation is running (chatgpt_tokens.lock exists)")
    try:
        records = read_credentials(path)
        if args.command == "revoke":
            remaining = [record for record in records if record.key_id != args.key_id]
            if len(remaining) == len(records):
                parser.error("Key ID not found")
            save_credentials(path, remaining)
            print("Key revoked")
            return
        if not 1 <= args.days <= 90:
            parser.error("--days must be between 1 and 90")
        if args.user_id <= 0:
            parser.error("--user-id must be positive")
        user = ApplicationContainer().identity.get_user(UserId(args.user_id))
        if user is None or not user.is_active or not any(has_access(user, section) for section in SectionName):
            parser.error("An active user with analytics section access is required")
        token, record = create_credential(args.user_id, datetime.now(UTC) + timedelta(days=args.days))
        # Exclusive creation avoids overwriting an existing private file.
        with args.output.open("x", encoding="utf-8") as output:
            output.write(token)
        try:
            save_credentials(path, [*records, record])
        except Exception:
            args.output.unlink()
            raise
        print(f"Created key {record.key_id}; plaintext saved to the requested private file")
    finally:
        lock.close()
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
