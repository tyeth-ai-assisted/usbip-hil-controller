"""M1 tests: in-process scheduler and fake worker."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hil_controller.adapters.base import DeviceAdapter
from hil_controller.queue.events import EventBus
from hil_controller.queue.scheduler import Scheduler
from hil_controller.queue.worker import JobWorker, WorkerResult


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def fake_adapter():
    adapter = AsyncMock(spec=DeviceAdapter)
    adapter.acquire = AsyncMock(return_value=None)
    adapter.reset = AsyncMock(return_value=None)
    adapter.flash = AsyncMock(return_value=None)
    adapter.open_serial = AsyncMock(return_value=None)
    adapter.release = AsyncMock(return_value=None)
    return adapter


@pytest.mark.asyncio
async def test_event_bus_publish_and_subscribe(event_bus):
    received = []

    async def handler(event):
        received.append(event)

    event_bus.subscribe("job-123", handler)
    await event_bus.publish("job-123", {"kind": "state", "payload": {"state": "running"}})
    await asyncio.sleep(0)
    assert len(received) == 1
    event_bus.unsubscribe("job-123", handler)


@pytest.mark.asyncio
async def test_event_bus_no_handler_does_not_raise(event_bus):
    await event_bus.publish("nonexistent-job", {"kind": "log", "payload": {"msg": "hi"}})


@pytest.mark.asyncio
async def test_worker_runs_fake_adapter_to_finish(event_bus, fake_adapter):
    states = []

    async def on_event(ev):
        if ev.get("kind") == "state":
            states.append(ev["payload"]["state"])

    event_bus.subscribe("j1", on_event)

    worker = JobWorker(
        job_id="j1",
        adapter=fake_adapter,
        event_bus=event_bus,
        script="git-clone-and-run",
        params={"entry": "python", "args": ["-m", "pytest"]},
        payload={"kind": "fake"},
        timeouts={"total_s": 30},
    )
    result = await worker.run()

    assert result.state == "finished"
    assert result.result in ("pass", "fail", "error")
    assert "running" in states or "finished" in states
    event_bus.unsubscribe("j1", on_event)
