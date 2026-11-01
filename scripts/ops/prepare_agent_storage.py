import argparse
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.agents.access import read_credentials


@dataclass(frozen=True, slots=True, kw_only=True)
class StorageFile:
    name: str
    sqlite: bool = False


STORAGE_FILES = (
    StorageFile(name="chatgpt_tokens.json"),
    StorageFile(name="mcp_oauth.sqlite3", sqlite=True),
)


def copy_storage_file(source: Path, target: Path, *, sqlite: bool) -> None:
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        if sqlite:
            with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
                with closing(sqlite3.connect(temporary)) as destination:
                    original.backup(destination)
                    if destination.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise ValueError("OAuth database integrity check failed")
        else:
            read_credentials(source)
            shutil.copyfile(source, temporary)
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(source: Path, target: Path, *, apply: bool, uid: int | None, gid: int | None) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise ValueError("Source and target directories must differ")
    if (uid is None) != (gid is None):
        raise ValueError("Specify both uid and gid")
    if uid is not None and gid is not None and (uid < 0 or gid < 0):
        raise ValueError("uid and gid must be non-negative")
    if uid is not None and not hasattr(os, "chown"):
        raise ValueError("Changing ownership requires a POSIX system")
    if apply:
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.chmod(0o700)
    for item in STORAGE_FILES:
        original = source / item.name
        destination = target / item.name
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise ValueError("Target must be a regular file: " + item.name)
        if destination.exists():
            if apply and item.sqlite:
                with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as existing:
                    if existing.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise ValueError("Existing OAuth database integrity check failed")
            elif apply:
                read_credentials(destination)
            action = "kept existing target"
        elif original.is_file():
            action = "copied" if apply else "would copy"
            if apply:
                copy_storage_file(original, destination, sqlite=item.sqlite)
        elif original.exists():
            raise ValueError("Source must be a regular file: " + item.name)
        else:
            action = "no legacy file; application will create it"
        if apply and destination.exists():
            destination.chmod(0o600)
            if uid is not None and gid is not None:
                os.chown(destination, uid, gid)
            if item.sqlite:
                for suffix in ("-wal", "-shm", "-journal"):
                    auxiliary = destination.with_name(destination.name + suffix)
                    if auxiliary.is_symlink() or (auxiliary.exists() and not auxiliary.is_file()):
                        raise ValueError("SQLite auxiliary target must be a regular file")
                    if auxiliary.exists():
                        auxiliary.chmod(0o600)
                        if uid is not None and gid is not None:
                            os.chown(auxiliary, uid, gid)
        print(f"{item.name}: {action}")
    if apply and uid is not None and gid is not None:
        os.chown(target, uid, gid)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare persistent API/MCP storage; preserves existing targets."
    )
    parser.add_argument("--source-directory", type=Path, default=Path("/app/secrets"))
    parser.add_argument("--target-directory", type=Path, default=Path("/app/agent-credentials"))
    parser.add_argument("--uid", type=int)
    parser.add_argument("--gid", type=int)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.source_directory, args.target_directory, apply=args.apply, uid=args.uid, gid=args.gid)
    except (OSError, ValueError, sqlite3.Error) as error:
        raise SystemExit(
            f"Agent storage preparation failed ({type(error).__name__}); deployment must stop"
        ) from error


if __name__ == "__main__":
    main()
