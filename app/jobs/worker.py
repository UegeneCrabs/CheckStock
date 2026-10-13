"""Run automatic synchronization independently of the HTTP server."""

import argparse
import asyncio
import json
import logging
import os
import signal
from datetime import UTC, datetime
from pathlib import Path

from fastapi.concurrency import run_in_threadpool

from app.config import settings
from app.core.logging_config import configure_logging
from app.jobs.background import _initialize_application, scheduled_jobs
from app.jobs.scheduling import ERROR_RETRY_SECONDS, run_background_job, wait_for_stop
from app.repositories.core import get_connection

logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_MAX_AGE_SECONDS = 60


def heartbeat_path() -> Path:
    return settings.database_path.parent / "scheduler-heartbeat.json"


def _check_database() -> None:
    with get_connection() as connection:
        connection.execute("SELECT 1").fetchone()


def _write_heartbeat(path: Path) -> None:
    _check_database()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps({"pid": os.getpid(), "updated_at": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    temporary.replace(path)


def healthcheck(path: Path) -> bool:
    try:
        heartbeat = json.loads(path.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(heartbeat["updated_at"])
        age = (datetime.now(UTC) - updated).total_seconds()
        if not -5 <= age <= HEARTBEAT_MAX_AGE_SECONDS:
            raise ValueError("Scheduler heartbeat is stale")
        _check_database()
        return True
    except Exception as error:
        logger.warning("scheduler_unhealthy error_type=%s", type(error).__name__)
        return False


async def _heartbeat(stop: asyncio.Event, path: Path) -> None:
    while not stop.is_set():
        try:
            await asyncio.to_thread(_write_heartbeat, path)
        except Exception:
            logger.exception("scheduler_heartbeat_failed")
        await wait_for_stop(stop, HEARTBEAT_INTERVAL_SECONDS)


def _remove_heartbeat(path: Path) -> None:
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


async def run(stop: asyncio.Event, path: Path) -> None:
    while not stop.is_set():
        try:
            await run_in_threadpool(_initialize_application)
            break
        except Exception:
            logger.exception("scheduler_initialization_failed")
            await wait_for_stop(stop, ERROR_RETRY_SECONDS)
    if stop.is_set():
        return
    jobs = scheduled_jobs(asyncio.Event())
    tasks = [
        asyncio.create_task(run_background_job(job, stop), name=f"checkstock:{job.name}") for job in jobs
    ]
    tasks.append(asyncio.create_task(_heartbeat(stop, path), name="checkstock:scheduler_heartbeat"))
    stopping = asyncio.create_task(stop.wait())
    logger.info("scheduler_started jobs=%s", len(jobs))
    try:
        done, _ = await asyncio.wait([stopping, *tasks], return_when=asyncio.FIRST_COMPLETED)
        if not stop.is_set():
            for task in done:
                task.result()
            raise RuntimeError("A scheduler task stopped unexpectedly")
    finally:
        stop.set()
        stopping.cancel()
        await asyncio.gather(stopping, *tasks, return_exceptions=True)
        _remove_heartbeat(path)
        logger.info("scheduler_stopped")


async def _main(path: Path) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = {}

    def request_stop(signum, frame):
        loop.call_soon_threadsafe(stop.set)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, request_stop)
    try:
        await run(stop, path)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--heartbeat-path", type=Path, default=heartbeat_path())
    args = parser.parse_args()
    configure_logging()
    if args.healthcheck:
        return 0 if healthcheck(args.heartbeat_path) else 1
    asyncio.run(_main(args.heartbeat_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
