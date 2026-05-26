"""Tests for usb_scan: parse `usbip list -l` + passive learn."""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio


_TOPOLOGY = """
hosts:
  - id: hub-a
    role: microcontroller-fleet
    addr: 127.0.0.10
    transport: fake
    ssh_user: pi
    ssh_key_path: /tmp/k
    capabilities: [usbip-server]

devices:
  - id: pyportal
    host_id: hub-a
    kind: microcontroller
    pool: public
    status: available
    hub_port_path: "1-1.1.3"
    usb_ids:
      - { vid: "239a", pid: "8053", role: runtime }
"""


@pytest_asyncio.fixture
async def app(tmp_path: Path):
    db_file = str(tmp_path / "scan.db")
    topo = tmp_path / "t.yaml"
    topo.write_text(_TOPOLOGY)
    os.environ["HIL_DB_PATH"] = db_file
    from hil_controller.main import create_app
    a = create_app(db_path=db_file, topology_file=str(topo))
    async with a.router.lifespan_context(a):
        a.state._test_db = db_file
        yield a


# -- parse_usbip_list ----------------------------------------------------


def test_parse_usbip_list_basic():
    from hil_controller.adapters.usb_scan import parse_usbip_list

    text = """\
 - busid 1-1.1.3 (239a:8053)
   Adafruit Industries LLC : unknown product (239a:8053)

 - busid 1-1.1.4 (239a:80df)
   Adafruit Industries LLC : QT Py ESP32-S2 (239a:80df)
"""
    rows = parse_usbip_list(text)
    assert len(rows) == 2
    assert rows[0]["busid"] == "1-1.1.3"
    assert rows[0]["vid"] == "239a"
    assert rows[0]["pid"] == "8053"
    assert rows[1]["busid"] == "1-1.1.4"
    assert rows[1]["pid"] == "80df"
    # Description captured
    assert "QT Py" in rows[1]["description"]


def test_parse_usbip_list_empty():
    from hil_controller.adapters.usb_scan import parse_usbip_list
    assert parse_usbip_list("") == []
    assert parse_usbip_list("usbip: no exportable devices found") == []


def test_parse_usbip_list_normalises_case():
    from hil_controller.adapters.usb_scan import parse_usbip_list
    text = " - busid 1-2 (239A:8053)\n   Vendor : Product (239A:8053)\n"
    rows = parse_usbip_list(text)
    assert rows[0]["vid"] == "239a"
    assert rows[0]["pid"] == "8053"


# -- learn_once ----------------------------------------------------------


@pytest.mark.asyncio
async def test_learn_once_adds_unseen_id(app):
    from hil_controller.adapters.usb_scan import learn_once

    # Scan returns a NEW vid/pid on the device's hub_port_path
    fake_scan = lambda: [
        {"busid": "1-1.1.3", "vid": "239a", "pid": "0035",
         "description": "UF2 Bootloader"},
    ]
    added = await learn_once(
        app.state._test_db,
        device_id="pyportal",
        hub_port_path="1-1.1.3",
        job_id="job-123",
        scan_fn=fake_scan,
    )
    assert added == 1

    async with aiosqlite.connect(app.state._test_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT vid, pid, source, learned_from_job FROM device_usb_ids "
            "WHERE device_id='pyportal' AND pid='0035'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    assert row["source"] == "passive"
    assert row["learned_from_job"] == "job-123"


@pytest.mark.asyncio
async def test_learn_once_ignores_other_busids(app):
    from hil_controller.adapters.usb_scan import learn_once

    fake_scan = lambda: [
        {"busid": "1-1.99.99", "vid": "dead", "pid": "beef",
         "description": "off-port"},
    ]
    added = await learn_once(
        app.state._test_db,
        device_id="pyportal",
        hub_port_path="1-1.1.3",
        job_id="job-1",
        scan_fn=fake_scan,
    )
    assert added == 0


@pytest.mark.asyncio
async def test_learn_once_refreshes_existing_last_seen(app):
    from hil_controller.adapters.usb_scan import learn_once

    # Seeded device already has (239a, 8053); a scan that matches it
    # should NOT add a new row, but should bump last_seen_at.
    async with aiosqlite.connect(app.state._test_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT last_seen_at FROM device_usb_ids "
            "WHERE device_id='pyportal' AND pid='8053'"
        ) as cur:
            before = (await cur.fetchone())["last_seen_at"]

    import time
    time.sleep(0.01)
    added = await learn_once(
        app.state._test_db,
        device_id="pyportal",
        hub_port_path="1-1.1.3",
        job_id="job-x",
        scan_fn=lambda: [
            {"busid": "1-1.1.3", "vid": "239a", "pid": "8053",
             "description": "WipperSnapper"},
        ],
    )
    assert added == 0

    async with aiosqlite.connect(app.state._test_db) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT last_seen_at FROM device_usb_ids "
            "WHERE device_id='pyportal' AND pid='8053'"
        ) as cur:
            after = (await cur.fetchone())["last_seen_at"]
    assert after > before


@pytest.mark.asyncio
async def test_learn_once_handles_scan_failure_gracefully(app):
    from hil_controller.adapters.usb_scan import learn_once

    def broken():
        raise RuntimeError("ssh down")

    # Should not raise — return 0 added.
    added = await learn_once(
        app.state._test_db,
        device_id="pyportal",
        hub_port_path="1-1.1.3",
        job_id="job-1",
        scan_fn=broken,
    )
    assert added == 0
