"""Parse `usbip list -l` output + passively learn unseen VID/PIDs.

Passive learn runs during an exclusive_device lease: the worker polls the
hub host every few seconds via `usbip list -l` and upserts any VID/PID
appearing on the device's bus-id that we haven't seen before. New rows are
tagged `source='passive'` and `learned_from_job=<job_id>`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import aiosqlite

log = logging.getLogger(__name__)

_BUSID_RE = re.compile(r"^\s*-\s*busid\s+(\S+)\s+\(([0-9a-fA-F]+):([0-9a-fA-F]+)\)")
_DESC_RE = re.compile(r"^\s*(.+?)\s+\([0-9a-fA-F]+:[0-9a-fA-F]+\)\s*$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_usbip_list(text: str) -> list[dict[str, Any]]:
    """Parse `usbip list -l` output into [{busid, vid, pid, description}, ...]."""
    out: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in (text or "").splitlines():
        m = _BUSID_RE.match(line)
        if m:
            if current:
                out.append(current)
            current = {
                "busid": m.group(1),
                "vid": m.group(2).lower(),
                "pid": m.group(3).lower(),
                "description": "",
            }
            continue
        if current and current["description"] == "":
            d = _DESC_RE.match(line)
            if d:
                current["description"] = d.group(1).strip()
    if current:
        out.append(current)
    return out


ScanFn = Callable[[], list[dict[str, Any]] | Awaitable[list[dict[str, Any]]]]


async def _call_scan(scan_fn: ScanFn) -> list[dict[str, Any]]:
    try:
        result = scan_fn()
        if asyncio.iscoroutine(result):
            result = await result
        return result or []
    except Exception as exc:
        log.warning("usb scan failed: %s", exc)
        return []


async def learn_once(
    db_path: str,
    *,
    device_id: str,
    hub_port_path: str | None,
    job_id: str | None,
    scan_fn: ScanFn,
) -> int:
    """Run a single scan, upsert matching rows, return # newly added.

    Existing rows have their `last_seen_at` refreshed (and description
    filled in if missing) but `source` and `learned_from_job` are NOT
    overwritten — manual/seeder rows keep their provenance.
    """
    entries = await _call_scan(scan_fn)
    if not entries or not hub_port_path:
        return 0

    matches = [e for e in entries if e.get("busid") == hub_port_path]
    if not matches:
        return 0

    now = _now_iso()
    added = 0
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        for e in matches:
            vid = (e.get("vid") or "").lower()
            pid = (e.get("pid") or "").lower()
            if not (vid and pid):
                continue
            description = e.get("description") or None

            async with db.execute(
                "SELECT id, description FROM device_usb_ids "
                "WHERE device_id=? AND vid=? AND pid=? "
                "AND COALESCE(iserial,'')=''",
                (device_id, vid, pid),
            ) as cur:
                row = await cur.fetchone()

            if row:
                await db.execute(
                    "UPDATE device_usb_ids "
                    "SET last_seen_at=?, "
                    "    description=COALESCE(description, ?) "
                    "WHERE id=?",
                    (now, description, row["id"]),
                )
            else:
                await db.execute(
                    "INSERT INTO device_usb_ids "
                    "(device_id, vid, pid, role, description, "
                    " first_seen_at, last_seen_at, learned_from_job, source) "
                    "VALUES (?, ?, ?, 'unknown', ?, ?, ?, ?, 'passive')",
                    (device_id, vid, pid, description, now, now, job_id),
                )
                added += 1
        await db.commit()
    if added:
        log.info(
            "passive learn: +%d usb_ids on %s (job=%s)",
            added, device_id, job_id,
        )
    return added


async def make_ssh_scan_fn(transport, hub_host_id: str) -> ScanFn:
    """Build a scan_fn that runs `usbip list -l` over the given transport."""
    async def _scan() -> list[dict[str, Any]]:
        try:
            out = await transport.run("usbip list -l")
        except Exception as exc:
            log.debug("ssh usbip list -l failed on %s: %s", hub_host_id, exc)
            return []
        return parse_usbip_list(out if isinstance(out, str) else (out or {}).get("stdout", ""))
    return _scan


async def passive_learn_loop(
    db_path: str,
    *,
    device_id: str,
    hub_port_path: str | None,
    job_id: str | None,
    scan_fn: ScanFn,
    interval_s: float = 3.0,
) -> None:
    """Run learn_once on a loop until cancelled. Errors are swallowed."""
    if not hub_port_path:
        return
    try:
        while True:
            try:
                await learn_once(
                    db_path,
                    device_id=device_id,
                    hub_port_path=hub_port_path,
                    job_id=job_id,
                    scan_fn=scan_fn,
                )
            except Exception as exc:
                log.debug("passive_learn_loop iter failed: %s", exc)
            await asyncio.sleep(interval_s)
    except asyncio.CancelledError:
        pass
