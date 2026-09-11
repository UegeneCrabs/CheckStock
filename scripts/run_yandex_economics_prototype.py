"""Run a local YM prototype against an explicitly selected, isolated database."""

import argparse
import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--sync", action="store_true", help="Run only YM read-only API loaders")
    args = parser.parse_args()
    database = args.db.resolve()
    default_database = Path(__file__).resolve().parents[1] / "data" / "checkstock.db"
    if database == default_database.resolve():
        parser.error("Use a separate prototype database, not data/checkstock.db")
    os.environ.update(
        {
            "CHECKSTOCK_DB_PATH": str(database),
            "CHECKSTOCK_DATABASE_URL": "",
            "CHECKSTOCK_DISABLE_BACKGROUND_SYNC": "true",
            "CHECKSTOCK_FUNNEL_ORDERS_SYNC_ENABLED": "false",
            "CHECKSTOCK_UNIT_ECONOMICS_1C_PRICE_SYNC_ENABLED": "false",
            "CHECKSTOCK_FTP_EXPORT_ENABLED": "false",
            "CHECKSTOCK_ADMIN_SEED_PATH": str(database.parent / "prototype-admin-seed.json"),
        }
    )
    import uvicorn

    from app import background
    from app.main import create_app
    from app.scheduling import BackgroundJob, run_background_job
    from app.yandex import economics_sync

    application = create_app()
    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app):
            jobs = []
            if args.sync:
                jobs = [
                    replace(job, is_enabled=None, run_callback=None)
                    for job in background._yandex_unit_economics_jobs()
                ]
                jobs.append(
                    BackgroundJob(
                        economics_sync.JOB, economics_sync.sync_all, lambda: 3600, startup_delay_seconds=30
                    )
                )
            tasks = [asyncio.create_task(run_background_job(job)) for job in jobs]
            try:
                yield
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    application.router.lifespan_context = lifespan
    uvicorn.run(application, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
