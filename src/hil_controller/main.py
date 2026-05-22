"""FastAPI app factory and uvicorn entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

log = logging.getLogger(__name__)


def create_app(db_path: str | None = None, topology_file: str | None = None) -> FastAPI:
    from hil_controller.config import get_settings

    settings = get_settings()
    _db_path = db_path or settings.db_path
    _topology_file = topology_file if topology_file is not None else settings.topology_file

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from hil_controller.db.connection import init_db
        from hil_controller.queue.events import EventBus
        from hil_controller.queue.scheduler import Scheduler
        from hil_controller.topology.seeder import seed_topology

        await init_db(_db_path)
        await seed_topology(_db_path, _topology_file)

        event_bus = EventBus()

        host_registry = None
        if _topology_file:
            from hil_controller.hosts.registry import RealHostRegistry

            host_registry = RealHostRegistry(topology_file=_topology_file, db_path=_db_path)
            host_registry.load()

        scheduler = Scheduler(db_path=_db_path, event_bus=event_bus, host_registry=host_registry)
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

    from hil_controller.api.aux import router as aux_router
    from hil_controller.api.devices import router as devices_router
    from hil_controller.api.health import router as health_router
    from hil_controller.api.hosts import router as hosts_router
    from hil_controller.api.jobs import router as jobs_router
    from hil_controller.api.topology import router as topology_router

    app.include_router(health_router)
    app.include_router(jobs_router)
    app.include_router(hosts_router)
    app.include_router(devices_router)
    app.include_router(aux_router)
    app.include_router(topology_router)

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
