"""FastAPI app factory and uvicorn entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

log = logging.getLogger(__name__)


def create_app(db_path: str | None = None) -> FastAPI:
    from hil_controller.config import get_settings

    settings = get_settings()
    _db_path = db_path or settings.db_path

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from hil_controller.db.connection import init_db
        from hil_controller.queue.events import EventBus
        from hil_controller.queue.scheduler import Scheduler

        await init_db(_db_path)

        event_bus = EventBus()
        scheduler = Scheduler(db_path=_db_path, event_bus=event_bus)
        await scheduler.start()

        app.state.db_path = _db_path
        app.state.event_bus = event_bus
        app.state.scheduler = scheduler

        log.info("hil-controller started, db=%s", _db_path)
        yield

        await scheduler.stop()
        log.info("hil-controller stopped")

    app = FastAPI(
        title="HIL Controller",
        version="0.1.0",
        lifespan=lifespan,
    )

    from hil_controller.api.health import router as health_router
    from hil_controller.api.jobs import router as jobs_router

    app.include_router(health_router)
    app.include_router(jobs_router)

    return app


def cli() -> None:
    import uvicorn

    from hil_controller.config import get_settings

    settings = get_settings()
    uvicorn.run(
        "hil_controller.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=False,
        log_config=None,
        log_level="info",
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli()
