import argparse
import gzip
import hashlib
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import date
from pathlib import Path

from sqlalchemy.engine import make_url

from app.config import settings
from app.core.constants import DATABASE_BACKUP, BackupIssue
from app.dto.database_backup import BackupArtifact, BackupError


def hashes(path: Path, day: date) -> BackupArtifact:
    md5, sha256 = hashlib.md5(), hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(DATABASE_BACKUP.hash_chunk_bytes):
            md5.update(chunk)
            sha256.update(chunk)
    return BackupArtifact(
        day=day, path=path, size=path.stat().st_size, md5=md5.hexdigest(), sha256=sha256.hexdigest()
    )


def sqlite_backup(target: Path, temporary: Path) -> None:
    source = settings.database_path.resolve()
    if not source.is_file():
        raise BackupError(BackupIssue.SOURCE)
    uncompressed = target.with_suffix(".sqlite.tmp")
    try:
        for leftover in (uncompressed, Path(str(uncompressed) + "-wal"), Path(str(uncompressed) + "-shm")):
            if leftover.is_symlink():
                raise BackupError(BackupIssue.INTEGRITY)
            leftover.unlink(missing_ok=True)
        with uncompressed.open("xb"):
            uncompressed.chmod(0o600)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(uncompressed)) as writer:
                reader.backup(writer, pages=2048)
                if writer.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise BackupError(BackupIssue.DUMP)
                writer.commit()
        with uncompressed.open("rb") as reader, temporary.open("wb") as output:
            with gzip.GzipFile(fileobj=output, mode="wb", compresslevel=1) as writer:
                shutil.copyfileobj(reader, writer, DATABASE_BACKUP.hash_chunk_bytes)
    finally:
        uncompressed.unlink(missing_ok=True)


def postgres_backup(temporary: Path) -> None:
    url = make_url(settings.database_url or "")
    environment = os.environ.copy()
    for name, value in (
        ("PGHOST", url.host),
        ("PGPORT", str(url.port or 5432)),
        ("PGDATABASE", url.database),
        ("PGUSER", url.username),
        ("PGPASSWORD", url.password),
    ):
        if value is not None:
            environment[name] = value
    for name in ("sslmode", "sslrootcert", "sslcert", "sslkey"):
        if isinstance(ssl_value := url.query.get(name), str):
            environment["PG" + name.upper()] = ssl_value
    subprocess.run(
        [settings.database_backup.pg_dump, "--no-password", "--format=custom", "--file", str(temporary)],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=settings.database_backup.process_timeout_seconds,
    )
    subprocess.run(
        [settings.database_backup.pg_restore, "--list", str(temporary)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=settings.database_backup.process_timeout_seconds,
    )


def create(target: Path, day: date) -> BackupArtifact:
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not target.exists():
        temporary = target.with_suffix(target.suffix + ".tmp")
        try:
            if temporary.is_symlink():
                raise BackupError(BackupIssue.INTEGRITY)
            temporary.unlink(missing_ok=True)
            with temporary.open("xb"):
                temporary.chmod(0o600)
            if settings.database_url:
                postgres_backup(temporary)
            else:
                sqlite_backup(target, temporary)
            if not temporary.stat().st_size:
                raise BackupError(BackupIssue.DUMP)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return hashes(target, day)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    try:
        print(create(args.output, args.day).model_dump_json())
    except Exception:
        print(BackupIssue.DUMP.value)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
