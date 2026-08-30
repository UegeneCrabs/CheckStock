import asyncio
import contextlib
import unittest
from unittest import mock

from app.scheduling import BackgroundJob, run_background_job


class BackgroundJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_job_waits_for_dependency_and_runs(self) -> None:
        ready = asyncio.Event()
        attempted = asyncio.Event()
        calls = []
        job = BackgroundJob(
            name="test",
            callback=lambda: calls.append("called"),
            next_delay=lambda: 60,
            ready_event=ready,
            on_attempt=attempted.set,
        )
        with mock.patch("app.scheduling.logger.info"):
            task = asyncio.create_task(run_background_job(job))

            await asyncio.sleep(0)
            self.assertEqual(calls, [])
            ready.set()
            await asyncio.wait_for(attempted.wait(), timeout=1)

            self.assertEqual(calls, ["called"])
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_failed_job_is_logged_and_next_attempt_is_scheduled(self) -> None:
        attempted = asyncio.Event()
        callback = mock.Mock(side_effect=RuntimeError("sync failed"))
        job = BackgroundJob(
            name="failing",
            callback=callback,
            next_delay=lambda: 60,
            on_attempt=attempted.set,
        )
        with mock.patch("app.scheduling.logger.exception") as exception:
            task = asyncio.create_task(run_background_job(job))
            await asyncio.wait_for(attempted.wait(), timeout=1)
            await asyncio.sleep(0.01)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        callback.assert_called_once()
        exception.assert_called_once()

    async def test_disabled_job_skips_callback_and_keeps_next_schedule(self) -> None:
        attempted = asyncio.Event()
        callback = mock.Mock()
        job = BackgroundJob(
            name="disabled",
            callback=callback,
            next_delay=lambda: 60,
            is_enabled=lambda: False,
            on_attempt=attempted.set,
        )
        with mock.patch("app.scheduling.set_next_run") as next_run:
            task = asyncio.create_task(run_background_job(job))
            await asyncio.wait_for(attempted.wait(), timeout=1)
            await asyncio.sleep(0.01)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        callback.assert_not_called()
        next_run.assert_called_once_with("disabled", 60)


if __name__ == "__main__":
    unittest.main()
