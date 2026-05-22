"""POST /v1/jobs, GET /v1/jobs/{id}, /wait, /cancel."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from hil_controller.auth.tokens import require_auth
from hil_controller.db.connection import (
    append_event,
    get_db,
    get_events_since,
    get_job,
    insert_job,
    update_job_state,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/jobs", tags=["jobs"])

Auth = Annotated[str, Depends(require_auth)]


# --------------------------------------------------------------------------- #
# Request / response models                                                    #
# --------------------------------------------------------------------------- #


class DeviceSelector(BaseModel):
    kind: str | None = None
    model: str | None = None
    id: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class AuxSelector(BaseModel):
    kind: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class Target(BaseModel):
    device: DeviceSelector
    requires: list[AuxSelector] = Field(default_factory=list)
    pool: str = "public"


class PayloadSource(BaseModel):
    repo: str | None = None
    ref: str | None = None
    submodules: bool = False
    shallow: bool = True
    setup: list[str] = Field(default_factory=list)
    kind: str | None = None
    source: str | None = None
    tag: str | None = None
    asset: str | None = None
    sha256: str | None = None


class Payload(BaseModel):
    kind: str
    source: dict[str, Any] | None = None


class ExclusiveFlag(BaseModel):
    host: bool = False


class Timeouts(BaseModel):
    total_s: int = 1800
    flash_s: int = 120
    run_s: int = 300
    deploy_s: int = 300


class JobRequest(BaseModel):
    target: Target
    script: str
    params: dict[str, Any] = Field(default_factory=dict)
    payload: Payload | None = None
    secrets_profile: str = "bench-protomq"
    exclusive: ExclusiveFlag = Field(default_factory=ExclusiveFlag)
    timeouts: Timeouts = Field(default_factory=Timeouts)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobSubmitResponse(BaseModel):
    id: str
    wait_url: str
    since: int = 0


class JobSnapshot(BaseModel):
    id: str
    state: str
    result: str | None
    assigned_host: str | None
    assigned_device: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    summary: str | None


class WaitResponse(BaseModel):
    events: list[dict[str, Any]]
    next_since: int
    state: str
    result: str | None = None


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=JobSubmitResponse)
async def submit_job(request: Request, body: JobRequest, _auth: Auth) -> JobSubmitResponse:
    job_id = str(uuid.uuid4())
    db_path: str = request.app.state.db_path
    scheduler = request.app.state.scheduler

    async with get_db(db_path) as db:
        await insert_job(
            db,
            job_id=job_id,
            request_json=body.model_dump(),
            secrets_profile=body.secrets_profile,
            exclusive_host=body.exclusive.host,
            submitted_by=_auth,
            repo=body.metadata.get("repo", ""),
        )
        await append_event(db, job_id, "state", {"state": "queued"})

    base = str(request.base_url).rstrip("/")
    await scheduler.enqueue(job_id)

    return JobSubmitResponse(
        id=job_id,
        wait_url=f"{base}/v1/jobs/{job_id}/wait",
        since=0,
    )


@router.get("/{job_id}", response_model=JobSnapshot)
async def get_job_snapshot(request: Request, job_id: str, _auth: Auth) -> JobSnapshot:
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        row = await get_job(db, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobSnapshot(
        id=row["id"],
        state=row["state"],
        result=row["result"],
        assigned_host=row["assigned_host"],
        assigned_device=row["assigned_device"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        summary=row["summary"],
    )


@router.get("/{job_id}/wait", response_model=WaitResponse)
async def long_poll_wait(
    request: Request,
    job_id: str,
    _auth: Auth,
    since: int = Query(default=0, ge=0),
    timeout: int = Query(default=300, ge=1, le=600),
) -> WaitResponse:
    db_path: str = request.app.state.db_path
    event_bus = request.app.state.event_bus

    # First check job exists
    async with get_db(db_path) as db:
        row = await get_job(db, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check for already-available events
    async with get_db(db_path) as db:
        events = await get_events_since(db, job_id, since)

    if not events:
        await event_bus.wait_for_events(job_id, timeout=float(timeout))
        async with get_db(db_path) as db:
            events = await get_events_since(db, job_id, since)
            row = await get_job(db, job_id)

    next_since = events[-1]["seq"] if events else since
    return WaitResponse(
        events=events,
        next_since=next_since,
        state=row["state"],  # type: ignore[index]
        result=row["result"],  # type: ignore[index]
    )


@router.post("/{job_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_job(request: Request, job_id: str, _auth: Auth) -> dict[str, str]:
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        row = await get_job(db, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if row["state"] in ("finished", "error", "timeout", "cancelled"):
        raise HTTPException(status_code=409, detail=f"Job already in terminal state: {row['state']}")

    async with get_db(db_path) as db:
        await update_job_state(db, job_id, "cancelled", result="cancelled")
        await append_event(db, job_id, "state", {"state": "cancelled"})

    event_bus = request.app.state.event_bus
    await event_bus.publish(job_id, {"kind": "state", "payload": {"state": "cancelled"}})

    return {"status": "cancelled", "id": job_id}
