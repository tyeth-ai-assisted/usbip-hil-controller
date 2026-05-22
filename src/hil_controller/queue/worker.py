"""Per-job async worker: drives the state machine, emits events."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from hil_controller.adapters.base import DeviceAdapter
from hil_controller.queue.events import EventBus

log = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"finished", "error", "timeout", "cancelled"})


@dataclass
class WorkerResult:
    state: str
    result: str  # pass | fail | error | timeout | cancelled


class JobWorker:
    def __init__(
        self,
        *,
        job_id: str,
        adapter: DeviceAdapter,
        event_bus: EventBus,
        script: str,
        params: dict[str, Any],
        payload: dict[str, Any],
        timeouts: dict[str, Any],
        db_path: str | None = None,
    ) -> None:
        self.job_id = job_id
        self.adapter = adapter
        self.event_bus = event_bus
        self.script = script
        self.params = params
        self.payload = payload
        self.timeouts = timeouts
        self.db_path = db_path
        self._cancelled = False

    async def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        await self.event_bus.publish(self.job_id, {"kind": kind, "payload": payload})
        if self.db_path:
            from hil_controller.db.connection import append_event, get_db, update_job_state

            async with get_db(self.db_path) as db:
                await append_event(db, self.job_id, kind, payload)
                if kind == "state":
                    kw: dict[str, Any] = {}
                    if "result" in payload:
                        kw["result"] = payload["result"]
                    await update_job_state(db, self.job_id, payload["state"], **kw)

    async def cancel(self) -> None:
        self._cancelled = True

    async def run(self) -> WorkerResult:
        total = self.timeouts.get("total_s", 1800)
        try:
            return await asyncio.wait_for(self._run(), timeout=total)
        except asyncio.TimeoutError:
            await self._emit("state", {"state": "timeout"})
            return WorkerResult(state="timeout", result="timeout")

    async def _run(self) -> WorkerResult:
        try:
            await self._emit("state", {"state": "preparing"})
            await self.adapter.acquire()

            if self._cancelled:
                await self._emit("state", {"state": "cancelled"})
                return WorkerResult(state="cancelled", result="cancelled")

            # For git-source payloads, flash = deploy (clone + setup)
            if self.payload.get("kind") == "git-source":
                await self._emit("state", {"state": "flashing"})
                await self._deploy_git_source()
            elif self.payload.get("kind") not in (None, "fake", "none"):
                await self._emit("state", {"state": "flashing"})
                await self.adapter.flash(self.payload)

            if self._cancelled:
                await self._emit("state", {"state": "cancelled"})
                return WorkerResult(state="cancelled", result="cancelled")

            await self._emit("state", {"state": "running"})
            result = await self._run_script()

        except asyncio.CancelledError:
            await self._emit("state", {"state": "cancelled"})
            return WorkerResult(state="cancelled", result="cancelled")
        except Exception as exc:
            log.exception("Worker error for job %s", self.job_id)
            await self._emit("state", {"state": "error"})
            await self._emit("log", {"msg": str(exc), "stream": "stderr"})
            return WorkerResult(state="error", result="error")
        finally:
            try:
                await self.adapter.release()
            except Exception:
                pass

        final_result = "pass" if result == 0 else "fail"
        await self._emit("state", {"state": "finished", "result": final_result})
        return WorkerResult(state="finished", result=final_result)

    async def _deploy_git_source(self) -> None:
        """Delegate to the GitDeployAdapter attached to self.adapter if available."""
        if hasattr(self.adapter, "deploy"):
            await self.adapter.deploy()  # type: ignore[attr-defined]

    async def _run_script(self) -> int:
        """Run the test script; return exit code."""
        if hasattr(self.adapter, "run"):
            outcome = await self.adapter.run()  # type: ignore[attr-defined]
            return 0 if outcome == "pass" else 1
        # Fake adapter — simulate pass
        await asyncio.sleep(0)
        return 0
