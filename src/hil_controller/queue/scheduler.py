"""In-process asyncio scheduler: assigns queued jobs to available hosts."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

log = logging.getLogger(__name__)


class Scheduler:
    """Picks queued jobs from the DB and dispatches workers."""

    def __init__(
        self,
        db_path: str,
        event_bus: Any,
        host_registry: Any | None = None,
    ) -> None:
        self.db_path = db_path
        self.event_bus = event_bus
        self.host_registry = host_registry
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._active: dict[str, asyncio.Task] = {}
        self._running = False
        self._dispatch_task: asyncio.Task | None = None

    async def enqueue(self, job_id: str) -> None:
        await self._queue.put(job_id)

    async def start(self) -> None:
        self._running = True
        self._dispatch_task = asyncio.create_task(
            self._dispatch_loop(), name="scheduler-dispatch"
        )

    async def stop(self) -> None:
        self._running = False
        if self._dispatch_task and not self._dispatch_task.done():
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        for task in list(self._active.values()):
            task.cancel()
        if self._active:
            await asyncio.gather(*self._active.values(), return_exceptions=True)

    async def _dispatch_loop(self) -> None:
        while self._running:
            try:
                job_id = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            asyncio.create_task(self._run_job(job_id), name=f"worker-{job_id}")

    async def _run_job(self, job_id: str) -> None:
        from hil_controller.db.connection import get_db, get_job, update_job_state
        from hil_controller.queue.worker import JobWorker

        adapter = await self._resolve_adapter(job_id)

        async with get_db(self.db_path) as db:
            row = await get_job(db, job_id)
        if row is None:
            return

        import json

        request = json.loads(row["request_json"])

        worker = JobWorker(
            job_id=job_id,
            adapter=adapter,
            event_bus=self.event_bus,
            script=request.get("script", "git-clone-and-run"),
            params=request.get("params", {}),
            payload=request.get("payload", {}),
            timeouts=request.get("timeouts", {"total_s": 1800}),
            db_path=self.db_path,
        )
        self._active[job_id] = asyncio.current_task()  # type: ignore[assignment]
        try:
            await worker.run()
        finally:
            self._active.pop(job_id, None)
            self.event_bus.cleanup(job_id)

    async def _resolve_adapter(self, job_id: str):  # noqa: ANN201
        """Return the appropriate adapter for this job.

        Falls back to a FakeAdapter when no real host registry is configured,
        which is useful for unit tests and local dev.
        """
        if self.host_registry is not None:
            return await self.host_registry.get_adapter(job_id)
        return _FakeAdapter()


class _FakeAdapter:
    async def acquire(self) -> None:
        await asyncio.sleep(0)

    async def reset(self) -> None:
        await asyncio.sleep(0)

    async def flash(self, artifact: dict) -> None:
        await asyncio.sleep(0)

    async def open_serial(self):
        return iter([])

    async def release(self) -> None:
        await asyncio.sleep(0)
