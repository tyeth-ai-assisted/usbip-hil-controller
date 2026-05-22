"""Async SQLite connection pool and schema initialiser."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

import aiosqlite

_SCHEMA = Path(__file__).parent / "schema.sql"


async def init_db(db_path: str) -> None:
    """Create tables if they don't exist."""
    async with aiosqlite.connect(db_path) as db:
        sql = _SCHEMA.read_text()
        await db.executescript(sql)
        await db.commit()


@asynccontextmanager
async def get_db(db_path: str) -> AsyncGenerator[aiosqlite.Connection, None]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def insert_job(
    db: aiosqlite.Connection,
    *,
    job_id: str,
    request_json: dict[str, Any],
    secrets_profile: str,
    exclusive_host: bool,
    submitted_by: str = "",
    repo: str = "",
) -> None:
    await db.execute(
        """
        INSERT INTO jobs (id, submitted_by, repo, request_json, secrets_profile,
                          exclusive_host, state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)
        """,
        (
            job_id,
            submitted_by,
            repo,
            json.dumps(request_json),
            secrets_profile,
            int(exclusive_host),
            now_iso(),
        ),
    )
    await db.commit()


async def get_job(db: aiosqlite.Connection, job_id: str) -> dict[str, Any] | None:
    async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cur:
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(row)


async def update_job_state(
    db: aiosqlite.Connection,
    job_id: str,
    state: str,
    *,
    result: str | None = None,
    assigned_host: str | None = None,
    assigned_device: str | None = None,
    summary: str | None = None,
) -> None:
    fields = ["state = ?"]
    values: list[Any] = [state]

    if state in ("running", "assigned", "preparing", "flashing") and not assigned_host:
        pass
    if state in ("assigned", "preparing", "flashing", "running"):
        fields.append("started_at = COALESCE(started_at, ?)")
        values.append(now_iso())
    if state in ("finished", "error", "timeout", "cancelled"):
        fields.append("finished_at = ?")
        values.append(now_iso())
    if result is not None:
        fields.append("result = ?")
        values.append(result)
    if assigned_host is not None:
        fields.append("assigned_host = ?")
        values.append(assigned_host)
    if assigned_device is not None:
        fields.append("assigned_device = ?")
        values.append(assigned_device)
    if summary is not None:
        fields.append("summary = ?")
        values.append(summary)

    values.append(job_id)
    await db.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
    await db.commit()


async def append_event(
    db: aiosqlite.Connection,
    job_id: str,
    kind: str,
    payload: dict[str, Any],
) -> int:
    import sqlite3

    for _ in range(10):
        async with db.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM events WHERE job_id = ?", (job_id,)
        ) as cur:
            row = await cur.fetchone()
            seq = row[0] if row else 0

        try:
            await db.execute(
                "INSERT INTO events (job_id, seq, at, kind, payload_json) VALUES (?, ?, ?, ?, ?)",
                (job_id, seq, now_iso(), kind, json.dumps(payload)),
            )
            await db.commit()
            return seq
        except (aiosqlite.IntegrityError, sqlite3.IntegrityError):
            # Concurrent writer took this seq — re-read MAX and retry.
            continue

    raise RuntimeError(f"Failed to append event for job {job_id} after 10 retries")


async def get_events_since(
    db: aiosqlite.Connection, job_id: str, since: int
) -> list[dict[str, Any]]:
    async with db.execute(
        "SELECT seq, at, kind, payload_json FROM events WHERE job_id = ? AND seq > ? ORDER BY seq",
        (job_id, since),
    ) as cur:
        rows = await cur.fetchall()
        return [
            {"seq": r["seq"], "at": r["at"], "kind": r["kind"], "payload": json.loads(r["payload_json"])}
            for r in rows
        ]
