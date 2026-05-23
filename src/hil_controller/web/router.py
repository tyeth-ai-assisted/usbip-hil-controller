"""Jinja2 / HTMX web interface for HIL controller admin."""

from __future__ import annotations

import html
import json
import logging
import shlex
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, File, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from hil_controller.auth.principal import Principal
from hil_controller.db.connection import get_db

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
templates.env.filters["tojson"] = json.dumps

router = APIRouter(prefix="/ui", tags=["web"])


def _tr(request: Request, name: str, ctx: dict | None = None, **kwargs):
    """Shorthand for Starlette 1.0+ TemplateResponse(request, name, context)."""
    return templates.TemplateResponse(request, name, ctx or {}, **kwargs)


def _redirect(path: str) -> Response:
    """HX-Redirect triggers a full client navigation in HTMX, avoiding tbody nesting bugs."""
    return Response(status_code=200, headers={"HX-Redirect": path})


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


async def _check_web_token(request: Request, token: str) -> Principal | None:
    if not token:
        return None
    from fastapi.security import HTTPAuthorizationCredentials

    from hil_controller.auth.tokens import require_auth

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    try:
        return await require_auth(request, creds)
    except Exception:
        return None


def _login_redirect() -> RedirectResponse:
    return RedirectResponse("/ui/login", status_code=status.HTTP_303_SEE_OTHER)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _hosts(db_path: str) -> list[dict]:
    async with get_db(db_path) as db:
        async with db.execute(
            """
            SELECT h.*, COUNT(d.id) AS device_count
            FROM hosts h LEFT JOIN devices d ON d.host_id = h.id
            GROUP BY h.id ORDER BY h.id
            """
        ) as cur:
            rows = await cur.fetchall()
    return [
        {**dict(r), "capabilities": json.loads(r["capabilities_json"])}
        for r in rows
    ]


async def _devices(db_path: str) -> list[dict]:
    async with get_db(db_path) as db:
        async with db.execute("SELECT * FROM devices ORDER BY id") as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["capabilities"] = json.loads(d.pop("capabilities_json"))
        usb = json.loads(d.pop("usb_json") or "null") or {}
        d["usb_vid"] = usb.get("vid", "")
        d["usb_pid"] = usb.get("pid", "")
        result.append(d)
    return result


def _parse_streams(row: dict) -> list[dict]:
    raw = row.get("streams_json")
    if raw:
        try:
            return json.loads(raw)
        except Exception:
            pass
    # fall back to legacy single interface/observability fields
    if row.get("interface"):
        return [{"url": row["interface"], "type": row.get("observability", "other")}]
    return []


async def _aux_list(db_path: str, kind_filter: str | None = None) -> list[dict]:
    async with get_db(db_path) as db:
        if kind_filter:
            async with db.execute(
                "SELECT * FROM auxes WHERE kind = ? ORDER BY id", (kind_filter,)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with db.execute("SELECT * FROM auxes ORDER BY id") as cur:
                rows = await cur.fetchall()
        result = []
        for r in rows:
            a = dict(r)
            a["capabilities"] = json.loads(a.pop("capabilities_json"))
            a["streams"] = _parse_streams(a)
            async with db.execute(
                "SELECT * FROM connections WHERE aux_id = ?", (a["id"],)
            ) as ccur:
                a["connections"] = [dict(c) for c in await ccur.fetchall()]
            result.append(a)
    return result


async def _aux_by_id(db_path: str, aux_id: str) -> dict | None:
    async with get_db(db_path) as db:
        async with db.execute("SELECT * FROM auxes WHERE id = ?", (aux_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        a = dict(row)
        a["capabilities"] = json.loads(a.pop("capabilities_json"))
        a["streams"] = _parse_streams(a)
        async with db.execute(
            "SELECT * FROM connections WHERE aux_id = ?", (aux_id,)
        ) as ccur:
            a["connections"] = [dict(c) for c in await ccur.fetchall()]
    return a


def _parse_caps(raw: str) -> list[str]:
    return [c.strip() for c in raw.split(",") if c.strip()]


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, error: str = "") -> HTMLResponse:
    return _tr(request, "login.html", {"error": error})


@router.post("/login", include_in_schema=False, response_model=None)
async def do_login(
    request: Request, token: Annotated[str, Form()] = ""
) -> Response:
    p = await _check_web_token(request, token)
    if p is None:
        return _tr(request, "login.html", {"error": "Invalid token"}, status_code=401)
    resp = RedirectResponse("/ui/", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie("hil_token", token, httponly=True, samesite="strict", path="/ui")
    return resp


@router.get("/logout", include_in_schema=False)
async def do_logout() -> RedirectResponse:
    resp = RedirectResponse("/ui/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie("hil_token", path="/ui")
    return resp


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


@router.get("/empty", response_class=HTMLResponse, include_in_schema=False)
async def empty() -> HTMLResponse:
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path

    hosts = await _hosts(db_path)
    devices = await _devices(db_path)
    hw = await _aux_list(db_path)
    cameras = await _aux_list(db_path, kind_filter="camera")

    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 10"
        ) as cur:
            recent_jobs = [dict(r) for r in await cur.fetchall()]
        async with db.execute(
            "SELECT COUNT(*) FROM jobs WHERE state NOT IN ('finished','error','timeout','cancelled')"
        ) as cur:
            row = await cur.fetchone()
            active_jobs = row[0] if row else 0

    from hil_controller.config import get_settings

    scripts_dir = get_settings().scripts_dir
    script_count = 0
    if scripts_dir and Path(scripts_dir).exists():
        script_count = len(list(Path(scripts_dir).glob("*.json")))

    return _tr(
        request,
        "dashboard.html",
        {
            "token": hil_token,
            "active": "dashboard",
            "hosts": hosts,
            "devices": devices,
            "hardware": hw,
            "cameras": cameras,
            "recent_jobs": recent_jobs,
            "active_jobs": active_jobs,
            "script_count": script_count,
        },
    )


# ---------------------------------------------------------------------------
# Hosts CRUD
# ---------------------------------------------------------------------------


@router.get("/hosts", response_class=HTMLResponse, include_in_schema=False)
async def hosts_page(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    hosts = await _hosts(db_path)
    return _tr(request, "hosts.html", {"token": hil_token, "active": "hosts", "hosts": hosts})


@router.get("/hosts/form", response_class=HTMLResponse, include_in_schema=False)
async def new_host_form(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    return _tr(request, "hosts_form.html", {"host": None})


@router.get("/hosts/{host_id}/form", response_class=HTMLResponse, include_in_schema=False)
async def edit_host_form(
    request: Request, host_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        async with db.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)) as cur:
            row = await cur.fetchone()
    if row is None:
        return HTMLResponse("Host not found", status_code=404)
    h = dict(row)
    h["capabilities"] = json.loads(h.pop("capabilities_json"))
    return _tr(request, "hosts_form.html", {"host": h})


@router.post("/hosts", response_class=HTMLResponse, include_in_schema=False)
async def create_host(
    request: Request,
    hil_token: str = Cookie(default=""),
    id: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = "",
    addr: Annotated[str, Form()] = "",
    transport: Annotated[str, Form()] = "ssh",
    ssh_user: Annotated[str, Form()] = "pi",
    ssh_key_path: Annotated[str, Form()] = "",
    max_concurrent_jobs: Annotated[str, Form()] = "",
    capabilities: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "available",
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    if not id:
        return _tr(request, "hosts_form.html", {"host": None, "error": "ID is required"})
    db_path: str = request.app.state.db_path
    max_jobs = int(max_concurrent_jobs) if max_concurrent_jobs.strip() else None
    async with get_db(db_path) as db:
        try:
            await db.execute(
                """INSERT INTO hosts
                   (id, role, addr, transport, ssh_user, ssh_key_path,
                    max_concurrent_jobs, capabilities_json, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (id, role, addr, transport, ssh_user, ssh_key_path or None,
                 max_jobs, json.dumps(_parse_caps(capabilities)), status),
            )
            await db.commit()
        except Exception as exc:
            return _tr(request, "hosts_form.html", {"host": None, "error": str(exc)})
    return _redirect("/ui/hosts")


@router.post("/hosts/{host_id}", response_class=HTMLResponse, include_in_schema=False)
async def update_host(
    request: Request,
    host_id: str,
    hil_token: str = Cookie(default=""),
    role: Annotated[str, Form()] = "",
    addr: Annotated[str, Form()] = "",
    transport: Annotated[str, Form()] = "ssh",
    ssh_user: Annotated[str, Form()] = "pi",
    ssh_key_path: Annotated[str, Form()] = "",
    max_concurrent_jobs: Annotated[str, Form()] = "",
    capabilities: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "available",
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    max_jobs = int(max_concurrent_jobs) if max_concurrent_jobs.strip() else None
    async with get_db(db_path) as db:
        async with db.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)) as cur:
            existing = await cur.fetchone()
        if existing is None:
            return HTMLResponse("Host not found", status_code=404)
        try:
            await db.execute(
                """UPDATE hosts SET role=?, addr=?, transport=?, ssh_user=?, ssh_key_path=?,
                   max_concurrent_jobs=?, capabilities_json=?, status=? WHERE id=?""",
                (role, addr, transport, ssh_user, ssh_key_path or None,
                 max_jobs, json.dumps(_parse_caps(capabilities)), status, host_id),
            )
            await db.commit()
        except Exception as exc:
            h = dict(existing)
            h["capabilities"] = json.loads(h.pop("capabilities_json"))
            return _tr(request, "hosts_form.html", {"host": h, "error": str(exc)})
    return _redirect("/ui/hosts")


@router.delete("/hosts/{host_id}", response_class=HTMLResponse, include_in_schema=False)
async def delete_host(
    request: Request, host_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
        await db.commit()
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Devices CRUD
# ---------------------------------------------------------------------------


@router.get("/devices", response_class=HTMLResponse, include_in_schema=False)
async def devices_page(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    devices = await _devices(db_path)
    return _tr(request, "devices.html", {"token": hil_token, "active": "devices", "devices": devices})


@router.get("/devices/form", response_class=HTMLResponse, include_in_schema=False)
async def new_device_form(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    hosts = await _hosts(db_path)
    return _tr(request, "devices_form.html", {"device": None, "hosts": hosts})


@router.get("/devices/{device_id}/form", response_class=HTMLResponse, include_in_schema=False)
async def edit_device_form(
    request: Request, device_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        async with db.execute("SELECT * FROM devices WHERE id = ?", (device_id,)) as cur:
            row = await cur.fetchone()
    if row is None:
        return HTMLResponse("Device not found", status_code=404)
    d = dict(row)
    d["capabilities"] = json.loads(d.pop("capabilities_json"))
    usb = json.loads(d.pop("usb_json") or "null") or {}
    d["usb_vid"] = usb.get("vid", "")
    d["usb_pid"] = usb.get("pid", "")
    hosts = await _hosts(db_path)
    return _tr(request, "devices_form.html", {"device": d, "hosts": hosts})


@router.post("/devices", response_class=HTMLResponse, include_in_schema=False)
async def create_device(
    request: Request,
    hil_token: str = Cookie(default=""),
    id: Annotated[str, Form()] = "",
    host_id: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "microcontroller",
    model: Annotated[str, Form()] = "",
    pool: Annotated[str, Form()] = "public",
    capabilities: Annotated[str, Form()] = "",
    serial_port: Annotated[str, Form()] = "",
    flasher: Annotated[str, Form()] = "",
    usb_vid: Annotated[str, Form()] = "",
    usb_pid: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "available",
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    if not id or not host_id:
        hosts = await _hosts(db_path)
        return _tr(
            request, "devices_form.html",
            {"device": None, "hosts": hosts, "error": "ID and Host are required"},
        )
    usb_json = json.dumps({"vid": usb_vid, "pid": usb_pid}) if (usb_vid or usb_pid) else None
    async with get_db(db_path) as db:
        try:
            await db.execute(
                """INSERT INTO devices
                   (id, host_id, kind, model, capabilities_json, usb_json,
                    pool, status, serial_port, flasher)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (id, host_id, kind, model, json.dumps(_parse_caps(capabilities)),
                 usb_json, pool, status, serial_port or None, flasher or None),
            )
            await db.commit()
        except Exception as exc:
            hosts = await _hosts(db_path)
            return _tr(request, "devices_form.html",
                       {"device": None, "hosts": hosts, "error": str(exc)})
    return _redirect("/ui/devices")


@router.post("/devices/{device_id}", response_class=HTMLResponse, include_in_schema=False)
async def update_device(
    request: Request,
    device_id: str,
    hil_token: str = Cookie(default=""),
    host_id: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "microcontroller",
    model: Annotated[str, Form()] = "",
    pool: Annotated[str, Form()] = "public",
    capabilities: Annotated[str, Form()] = "",
    serial_port: Annotated[str, Form()] = "",
    flasher: Annotated[str, Form()] = "",
    usb_vid: Annotated[str, Form()] = "",
    usb_pid: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = "available",
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    usb_json = json.dumps({"vid": usb_vid, "pid": usb_pid}) if (usb_vid or usb_pid) else None
    async with get_db(db_path) as db:
        async with db.execute("SELECT id FROM devices WHERE id = ?", (device_id,)) as cur:
            if await cur.fetchone() is None:
                return HTMLResponse("Device not found", status_code=404)
        await db.execute(
            """UPDATE devices SET host_id=?, kind=?, model=?, capabilities_json=?,
               usb_json=?, pool=?, status=?, serial_port=?, flasher=? WHERE id=?""",
            (host_id, kind, model, json.dumps(_parse_caps(capabilities)),
             usb_json, pool, status, serial_port or None, flasher or None, device_id),
        )
        await db.commit()
    return _redirect("/ui/devices")


@router.delete("/devices/{device_id}", response_class=HTMLResponse, include_in_schema=False)
async def delete_device(
    request: Request, device_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        await db.commit()
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Hardware / Aux CRUD
# ---------------------------------------------------------------------------


@router.get("/hardware", response_class=HTMLResponse, include_in_schema=False)
async def hardware_page(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    hardware = [a for a in await _aux_list(db_path) if a["kind"] != "camera"]
    return _tr(request, "hardware.html",
               {"token": hil_token, "active": "hardware", "hardware": hardware})


@router.get("/hardware/form", response_class=HTMLResponse, include_in_schema=False)
async def new_hardware_form(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    devices = await _devices(db_path)
    return _tr(request, "hardware_form.html", {"aux": None, "devices": devices})


@router.get("/hardware/{aux_id}/form", response_class=HTMLResponse, include_in_schema=False)
async def edit_hardware_form(
    request: Request, aux_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    aux = await _aux_by_id(db_path, aux_id)
    if aux is None:
        return HTMLResponse("Aux not found", status_code=404)
    devices = await _devices(db_path)
    return _tr(request, "hardware_form.html", {"aux": aux, "devices": devices})


@router.post("/hardware", response_class=HTMLResponse, include_in_schema=False)
async def create_hardware(
    request: Request,
    hil_token: str = Cookie(default=""),
    id: Annotated[str, Form()] = "",
    kind: Annotated[str, Form()] = "display",
    model: Annotated[str, Form()] = "",
    interface: Annotated[str, Form()] = "",
    observability: Annotated[str, Form()] = "none",
    capabilities: Annotated[str, Form()] = "",
    pool: Annotated[str, Form()] = "public",
    status: Annotated[str, Form()] = "available",
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    if not id:
        devices = await _devices(db_path)
        return _tr(request, "hardware_form.html",
                   {"aux": None, "devices": devices, "error": "ID is required"})
    async with get_db(db_path) as db:
        try:
            await db.execute(
                """INSERT INTO auxes
                   (id, kind, model, capabilities_json, interface, observability, pool, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (id, kind, model, json.dumps(_parse_caps(capabilities)),
                 interface, observability, pool, status),
            )
            await db.commit()
        except Exception as exc:
            devices = await _devices(db_path)
            return _tr(request, "hardware_form.html",
                       {"aux": None, "devices": devices, "error": str(exc)})
    return _redirect("/ui/hardware")


@router.post("/hardware/{aux_id}", response_class=HTMLResponse, include_in_schema=False)
async def update_hardware(
    request: Request,
    aux_id: str,
    hil_token: str = Cookie(default=""),
    kind: Annotated[str, Form()] = "display",
    model: Annotated[str, Form()] = "",
    interface: Annotated[str, Form()] = "",
    observability: Annotated[str, Form()] = "none",
    capabilities: Annotated[str, Form()] = "",
    pool: Annotated[str, Form()] = "public",
    status: Annotated[str, Form()] = "available",
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        async with db.execute("SELECT id FROM auxes WHERE id = ?", (aux_id,)) as cur:
            if await cur.fetchone() is None:
                return HTMLResponse("Aux not found", status_code=404)
        await db.execute(
            """UPDATE auxes SET kind=?, model=?, capabilities_json=?,
               interface=?, observability=?, pool=?, status=? WHERE id=?""",
            (kind, model, json.dumps(_parse_caps(capabilities)),
             interface, observability, pool, status, aux_id),
        )
        await db.commit()
    return _redirect("/ui/hardware")


@router.delete("/hardware/{aux_id}", response_class=HTMLResponse, include_in_schema=False)
async def delete_hardware(
    request: Request, aux_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM connections WHERE aux_id = ?", (aux_id,))
        await db.execute("DELETE FROM auxes WHERE id = ?", (aux_id,))
        await db.commit()
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Cameras (aux with kind=camera)
# ---------------------------------------------------------------------------


@router.get("/cameras", response_class=HTMLResponse, include_in_schema=False)
async def cameras_page(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    cameras = await _aux_list(db_path, kind_filter="camera")
    return _tr(request, "cameras.html",
               {"token": hil_token, "active": "cameras", "cameras": cameras})


@router.get("/cameras/form", response_class=HTMLResponse, include_in_schema=False)
async def new_camera_form(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    devices = await _devices(db_path)
    return _tr(request, "cameras_form.html", {"camera": None, "devices": devices})


@router.get("/cameras/{cam_id}/form", response_class=HTMLResponse, include_in_schema=False)
async def edit_camera_form(
    request: Request, cam_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    cam = await _aux_by_id(db_path, cam_id)
    if cam is None:
        return HTMLResponse("Camera not found", status_code=404)
    devices = await _devices(db_path)
    return _tr(request, "cameras_form.html", {"camera": cam, "devices": devices})


@router.post("/cameras", response_class=HTMLResponse, include_in_schema=False)
async def create_camera(
    request: Request,
    hil_token: str = Cookie(default=""),
    id: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    stream_url: Annotated[list[str], Form()] = [],
    stream_type: Annotated[list[str], Form()] = [],
    pool: Annotated[str, Form()] = "public",
    status: Annotated[str, Form()] = "available",
) -> Response:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    streams = [{"url": u.strip(), "type": t} for u, t in zip(stream_url, stream_type) if u.strip()]
    if not id or not streams:
        devices = await _devices(db_path)
        return _tr(request, "cameras_form.html",
                   {"camera": None, "devices": devices, "error": "ID and at least one stream URL are required"})
    primary = streams[0]
    streams_json = json.dumps(streams)
    async with get_db(db_path) as db:
        try:
            await db.execute(
                """INSERT INTO auxes
                   (id, kind, model, capabilities_json, interface, observability, pool, status, streams_json)
                   VALUES (?, 'camera', ?, '[]', ?, ?, ?, ?, ?)""",
                (id, model, primary["url"], primary["type"], pool, status, streams_json),
            )
            await db.commit()
        except Exception as exc:
            devices = await _devices(db_path)
            return _tr(request, "cameras_form.html",
                       {"camera": None, "devices": devices, "error": str(exc)})
    return _redirect("/ui/cameras")


@router.post("/cameras/{cam_id}", response_class=HTMLResponse, include_in_schema=False)
async def update_camera(
    request: Request,
    cam_id: str,
    hil_token: str = Cookie(default=""),
    model: Annotated[str, Form()] = "",
    stream_url: Annotated[list[str], Form()] = [],
    stream_type: Annotated[list[str], Form()] = [],
    pool: Annotated[str, Form()] = "public",
    status: Annotated[str, Form()] = "available",
) -> Response:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    streams = [{"url": u.strip(), "type": t} for u, t in zip(stream_url, stream_type) if u.strip()]
    primary = streams[0] if streams else {"url": "", "type": "other"}
    streams_json = json.dumps(streams) if streams else None
    async with get_db(db_path) as db:
        async with db.execute("SELECT id FROM auxes WHERE id = ?", (cam_id,)) as cur:
            if await cur.fetchone() is None:
                return HTMLResponse("Camera not found", status_code=404)
        await db.execute(
            """UPDATE auxes SET model=?, interface=?, observability=?, pool=?, status=?,
               streams_json=?, kind='camera' WHERE id=?""",
            (model, primary["url"], primary["type"], pool, status, streams_json, cam_id),
        )
        await db.commit()
    return _redirect("/ui/cameras")


@router.delete("/cameras/{cam_id}", response_class=HTMLResponse, include_in_schema=False)
async def delete_camera(
    request: Request, cam_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM connections WHERE aux_id = ?", (cam_id,))
        await db.execute("DELETE FROM auxes WHERE id = ?", (cam_id,))
        await db.commit()
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


@router.post("/connections", response_class=HTMLResponse, include_in_schema=False)
async def create_connection(
    request: Request,
    hil_token: str = Cookie(default=""),
    aux_id: Annotated[str, Form()] = "",
    device_id: Annotated[str, Form()] = "",
    mux_id: Annotated[str, Form()] = "",
    mux_channel: Annotated[str, Form()] = "",
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    if not aux_id or not device_id:
        return HTMLResponse("aux_id and device_id are required", status_code=422)
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        await db.execute(
            "INSERT INTO connections (aux_id, device_id, mux_id, mux_channel) VALUES (?, ?, ?, ?)",
            (aux_id, device_id, mux_id or None, mux_channel or None),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM connections WHERE aux_id = ?", (aux_id,)
        ) as cur:
            conns = [dict(c) for c in await cur.fetchall()]
    return _tr(request, "conn_list.html", {"connections": conns, "aux_id": aux_id})


@router.delete("/connections/{conn_id}", response_class=HTMLResponse, include_in_schema=False)
async def delete_connection(
    request: Request, conn_id: int, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        await db.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
        await db.commit()
    return HTMLResponse("")


# ---------------------------------------------------------------------------
# Scripts browser
# ---------------------------------------------------------------------------


@router.get("/scripts", response_class=HTMLResponse, include_in_schema=False)
async def scripts_page(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    from hil_controller.config import get_settings

    scripts_dir = get_settings().scripts_dir
    scripts = []
    if scripts_dir:
        p = Path(scripts_dir)
        for jf in sorted(p.glob("*.json")):
            try:
                data = json.loads(jf.read_text())
                scripts.append(
                    {
                        "filename": jf.name,
                        "name": data.get("name", jf.stem),
                        "description": data.get("description", ""),
                        "proto_version": data.get("protoVersion", ""),
                        "step_count": len(data.get("steps", [])),
                    }
                )
            except Exception:
                scripts.append({"filename": jf.name, "name": jf.stem,
                                 "description": "", "proto_version": "", "step_count": 0})
    return _tr(
        request, "scripts.html",
        {"token": hil_token, "active": "scripts", "scripts": scripts, "scripts_dir": scripts_dir},
    )


@router.get("/scripts/{filename}", response_class=HTMLResponse, include_in_schema=False)
async def script_detail(
    request: Request, filename: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    from hil_controller.config import get_settings

    scripts_dir = get_settings().scripts_dir
    if not scripts_dir:
        return HTMLResponse("Scripts directory not configured", status_code=404)
    safe_name = Path(filename).name
    fpath = Path(scripts_dir) / safe_name
    if not fpath.exists() or fpath.suffix != ".json":
        return HTMLResponse("Script not found", status_code=404)
    try:
        data = json.loads(fpath.read_text())
    except Exception as exc:
        return HTMLResponse(f"Parse error: {exc}", status_code=500)
    name = data.get("name", safe_name)
    desc = data.get("description", "")
    body = json.dumps(data, indent=2)
    return HTMLResponse(
        f'<div class="script-card" style="cursor:default;">'
        f"<h3>{name}</h3>"
        f"<p>{desc}</p></div>"
        f"<pre><code>{body}</code></pre>"
    )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


def _duration(started: str | None, finished: str | None) -> str:
    if not started or not finished:
        return ""
    try:
        fmt = "%Y-%m-%dT%H:%M:%S"
        s = datetime.fromisoformat(started)
        f = datetime.fromisoformat(finished)
        secs = int((f - s).total_seconds())
        return f"{secs // 60}m {secs % 60}s"
    except Exception:
        return ""


async def _job_rows(db_path: str, limit: int = 100) -> list[dict]:
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        j = dict(r)
        req = json.loads(j.get("request_json") or "{}")
        src = (req.get("payload") or {}).get("source") or {}
        j["repo_url"] = src.get("repo", "")
        j["ref"] = src.get("ref", "")
        j["duration"] = _duration(j.get("started_at"), j.get("finished_at"))
        result.append(j)
    return result


def _render_events(events: list[dict]) -> str:
    lines = []
    colours = {"stdout": "#c9d1d9", "stderr": "#f97583", "protomq": "#79c0ff", "state": "#d2a8ff"}
    for ev in events:
        kind = ev.get("kind", "")
        payload = ev.get("payload", {})
        at = ev.get("at", "")[:19]
        if kind == "log":
            stream = payload.get("stream", "stdout")
            msg = html.escape(payload.get("msg", ""))
            colour = colours.get(stream, "#c9d1d9")
            lines.append(
                f'<div style="color:{colour};font-family:monospace;font-size:0.75rem;white-space:pre-wrap;">'
                f'<span style="color:#6c757d;user-select:none;">[{at}] </span>{msg}</div>'
            )
        elif kind == "state":
            st = payload.get("state", "")
            colour = colours["state"]
            lines.append(
                f'<div style="color:{colour};font-family:monospace;font-size:0.75rem;">'
                f'<span style="color:#6c757d;user-select:none;">[{at}] </span>'
                f'<b>── state: {html.escape(st)} ──</b></div>'
            )
    return "\n".join(lines) if lines else '<span style="color:#6c757d;font-size:0.8rem;">No output yet.</span>'


_JOB_DEFAULTS = {
    "no_hw_args": '-m "not hardware" -v --tb=short',
    "hw_args": '-m "display or hardware" -v --tb=short',
}


def _fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n:.1f} PB"


def _disk_info(path: str = "") -> dict:
    try:
        p = path or "/"
        u = shutil.disk_usage(p)
        pct = int(100 * u.used / u.total) if u.total else 0
        return {"total_fmt": _fmt_bytes(u.total), "free_fmt": _fmt_bytes(u.free),
                "used_fmt": _fmt_bytes(u.used), "pct_used": pct, "free": u.free}
    except Exception:
        return {"total_fmt": "?", "free_fmt": "?", "used_fmt": "?", "pct_used": 0, "free": 0}


def _jobs_dir() -> str:
    from hil_controller.config import get_settings
    cfg = get_settings()
    if cfg.jobs_dir:
        return cfg.jobs_dir
    db = cfg.db_path
    return str(Path(db).parent / "jobs") if db else "/tmp/hil-jobs"


async def _asset_rows(db_path: str) -> list[dict]:
    async with get_db(db_path) as db:
        async with db.execute("SELECT * FROM assets ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
    result = []
    for r in rows:
        a = dict(r)
        a["size_fmt"] = _fmt_bytes(a.get("size_bytes") or 0)
        result.append(a)
    return result


async def _call_jobs_api(request: Request, job_request: dict, token: str) -> dict:
    """Submit a job via the internal /v1/jobs API and return the response dict."""
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=request.app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/v1/jobs",
            json=job_request,
            headers={"Authorization": f"Bearer {token}"},
        )
    if r.status_code not in (200, 202):
        raise ValueError(r.json().get("detail", f"HTTP {r.status_code}"))
    return r.json()


def _build_job_request(
    *,
    repo: str,
    ref: str,
    pat: str,
    submodules: bool,
    setup: str,
    hw_mode: str,
    test_args: str,
    protomq_script: str,
    device_id: str,
    requires_aux: str,
    secrets_profile: str,
    mqtt_host: str,
    mqtt_port: str,
    io_username: str,
    io_key: str,
    timeout_total: int,
    timeout_run: int,
    timeout_deploy: int,
) -> dict:
    parsed_args = shlex.split(test_args) if test_args.strip() else []
    full_args = ["-m", "pytest"] + parsed_args

    extra_env: dict = {}
    if hw_mode == "no_hardware":
        extra_env["BLINKA_OS_AGNOSTIC"] = "1"

    target: dict = {"pool": "wippersnapper-python"}
    if device_id:
        target["device"] = {"id": device_id}
    else:
        target["device"] = {"kind": "sbc", "capabilities": ["python-snapper"]}
    if requires_aux:
        target["requires"] = [{"id": requires_aux}]

    _mqtt_port = int(mqtt_port) if mqtt_port.strip().isdigit() else 1884
    params: dict = {
        "entry": "python",
        "args": full_args,
        "secrets_format": "dotenv",
        "extra_env": extra_env,
    }
    if protomq_script and mqtt_host:
        params["protomq"] = {
            "broker_host": mqtt_host,
            "mqtt_port": _mqtt_port,
            "api_port": 5173,
            "script": protomq_script,
        }

    source: dict = {
        "repo": repo,
        "ref": ref,
        "shallow": True,
        "submodules": submodules,
        "setup": shlex.split(setup) if setup.strip() else [],
    }
    if pat:
        source["pat"] = pat

    secrets: dict = {"MQTT_HOST": mqtt_host, "MQTT_PORT": str(_mqtt_port)}
    if io_username:
        secrets["IO_USERNAME"] = io_username
    if io_key:
        secrets["IO_KEY"] = io_key

    return {
        "target": target,
        "script": "pytest-suite",
        "payload": {"kind": "git-source", "source": source},
        "params": params,
        "secrets": secrets,
        "secrets_profile": secrets_profile,
        "timeouts": {
            "total_s": timeout_total,
            "deploy_s": timeout_deploy,
            "run_s": timeout_run,
            "flash_s": 120,
        },
    }


@router.get("/jobs", response_class=HTMLResponse, include_in_schema=False)
async def jobs_page(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    jobs = await _job_rows(db_path)
    return _tr(request, "jobs.html", {"token": hil_token, "active": "jobs", "jobs": jobs,
                                       "disk": _disk_info(_jobs_dir())})


@router.get("/jobs/list", response_class=HTMLResponse, include_in_schema=False)
async def jobs_list_partial(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return HTMLResponse("", status_code=401)
    db_path: str = request.app.state.db_path
    jobs = await _job_rows(db_path)
    return _tr(request, "jobs_body.html", {"jobs": jobs})


@router.get("/jobs/new", response_class=HTMLResponse, include_in_schema=False)
async def new_job_page(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    sbc_devices = [d for d in await _devices(db_path) if d["kind"] == "sbc"]

    from hil_controller.config import get_settings
    scripts_dir = get_settings().scripts_dir
    scripts = sorted(Path(scripts_dir).glob("*.json")) if scripts_dir and Path(scripts_dir).exists() else []

    return _tr(request, "job_new.html", {
        "token": hil_token,
        "active": "jobs",
        "sbc_devices": sbc_devices,
        "scripts": scripts,
        "defaults": _JOB_DEFAULTS,
        "disk": _disk_info(_jobs_dir()),
        "form": None,
        "error": None,
    })


@router.post("/jobs", include_in_schema=False, response_model=None)
async def submit_job_form(
    request: Request,
    hil_token: str = Cookie(default=""),
    repo: Annotated[str, Form()] = "",
    ref: Annotated[str, Form()] = "main",
    pat: Annotated[str, Form()] = "",
    submodules: Annotated[str, Form()] = "",
    setup: Annotated[str, Form()] = "pip install -e .[test]",
    hw_mode: Annotated[str, Form()] = "no_hardware",
    test_args: Annotated[str, Form()] = '-m "not hardware" -v --tb=short',
    protomq_script: Annotated[str, Form()] = "",
    device_id: Annotated[str, Form()] = "",
    requires_aux: Annotated[str, Form()] = "",
    secrets_profile: Annotated[str, Form()] = "bench-protomq",
    mqtt_host: Annotated[str, Form()] = "127.0.0.1",
    mqtt_port: Annotated[str, Form()] = "1884",
    io_username: Annotated[str, Form()] = "",
    io_key: Annotated[str, Form()] = "",
    timeout_total: Annotated[str, Form()] = "600",
    timeout_run: Annotated[str, Form()] = "300",
    timeout_deploy: Annotated[str, Form()] = "180",
) -> Response:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()

    db_path: str = request.app.state.db_path
    sbc_devices = [d for d in await _devices(db_path) if d["kind"] == "sbc"]
    from hil_controller.config import get_settings
    scripts_dir = get_settings().scripts_dir
    scripts = sorted(Path(scripts_dir).glob("*.json")) if scripts_dir and Path(scripts_dir).exists() else []

    form_vals = {
        "repo": repo, "ref": ref, "pat": pat, "setup": setup,
        "submodules": bool(submodules), "hw_mode": hw_mode, "test_args": test_args,
        "protomq_script": protomq_script, "device_id": device_id, "requires_aux": requires_aux,
        "secrets_profile": secrets_profile, "mqtt_host": mqtt_host, "mqtt_port": mqtt_port,
        "io_username": io_username, "io_key": io_key,
        "timeout_total": timeout_total, "timeout_run": timeout_run, "timeout_deploy": timeout_deploy,
    }

    if not repo:
        return _tr(request, "job_new.html", {
            "token": hil_token, "active": "jobs",
            "sbc_devices": sbc_devices, "scripts": scripts,
            "defaults": _JOB_DEFAULTS, "disk": _disk_info(_jobs_dir()),
            "form": form_vals, "error": "Repository URL is required",
        })

    try:
        job_req = _build_job_request(
            repo=repo, ref=ref, pat=pat,
            submodules=bool(submodules), setup=setup,
            hw_mode=hw_mode, test_args=test_args,
            protomq_script=protomq_script, device_id=device_id,
            requires_aux=requires_aux, secrets_profile=secrets_profile,
            mqtt_host=mqtt_host, mqtt_port=mqtt_port,
            io_username=io_username, io_key=io_key,
            timeout_total=int(timeout_total or 600),
            timeout_run=int(timeout_run or 300),
            timeout_deploy=int(timeout_deploy or 180),
        )
        resp = await _call_jobs_api(request, job_req, hil_token)
        job_id = resp["id"]
    except Exception as exc:
        return _tr(request, "job_new.html", {
            "token": hil_token, "active": "jobs",
            "sbc_devices": sbc_devices, "scripts": scripts,
            "defaults": _JOB_DEFAULTS, "disk": _disk_info(_jobs_dir()),
            "form": form_vals, "error": str(exc),
        })

    return RedirectResponse(f"/ui/jobs/{job_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/jobs/{job_id}", response_class=HTMLResponse, include_in_schema=False)
async def job_detail(
    request: Request, job_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        from hil_controller.db.connection import get_job, get_events_since
        row = await get_job(db, job_id)
        if row is None:
            return HTMLResponse("Job not found", status_code=404)
        events = await get_events_since(db, job_id, -1)

    j = dict(row)
    req = json.loads(j.get("request_json") or "{}")
    src = (req.get("payload") or {}).get("source") or {}
    j["repo_url"] = src.get("repo", "")
    j["ref"] = src.get("ref", "")
    j["duration"] = _duration(j.get("started_at"), j.get("finished_at"))

    return _tr(request, "job_detail.html", {
        "token": hil_token,
        "active": "jobs",
        "job": j,
        "log_html": _render_events(events),
    })


@router.get("/jobs/{job_id}/log", response_class=HTMLResponse, include_in_schema=False)
async def job_log_partial(
    request: Request, job_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return HTMLResponse("", status_code=401)
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        from hil_controller.db.connection import get_job, get_events_since
        row = await get_job(db, job_id)
        if row is None:
            return HTMLResponse("Job not found", status_code=404)
        events = await get_events_since(db, job_id, -1)
    return HTMLResponse(_render_events(events))


@router.post("/jobs/{job_id}/rerun", include_in_schema=False, response_model=None)
async def rerun_job(
    request: Request, job_id: str, hil_token: str = Cookie(default="")
) -> Response:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        from hil_controller.db.connection import get_job
        row = await get_job(db, job_id)
    if row is None:
        return HTMLResponse("Job not found", status_code=404)
    original_req = json.loads(row["request_json"])
    try:
        resp = await _call_jobs_api(request, original_req, hil_token)
        new_id = resp["id"]
    except Exception as exc:
        return HTMLResponse(f'<div class="alert alert-error">{html.escape(str(exc))}</div>')
    return Response(status_code=200, headers={"HX-Redirect": f"/ui/jobs/{new_id}"})


@router.post("/jobs/{job_id}/cancel", include_in_schema=False, response_model=None)
async def cancel_job_web(
    request: Request, job_id: str, hil_token: str = Cookie(default="")
) -> Response:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(
        transport=ASGITransport(app=request.app), base_url="http://test"
    ) as c:
        await c.post(
            f"/v1/jobs/{job_id}/cancel",
            headers={"Authorization": f"Bearer {hil_token}"},
        )
    return Response(status_code=200, headers={"HX-Redirect": f"/ui/jobs/{job_id}"})


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


@router.get("/assets", response_class=HTMLResponse, include_in_schema=False)
async def assets_page(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    assets = await _asset_rows(db_path)
    jdir = _jobs_dir()
    total_bytes = sum(a.get("size_bytes") or 0 for a in assets if not a.get("purged_at"))
    eligible = sum(1 for a in assets if not a.get("purged_at") and a.get("purge_at"))
    return _tr(request, "assets.html", {
        "token": hil_token,
        "active": "assets",
        "assets": assets,
        "total_size": _fmt_bytes(total_bytes),
        "purge_eligible": eligible,
        "disk": _disk_info(jdir),
    })


@router.delete("/assets/{asset_id}", response_class=HTMLResponse, include_in_schema=False)
async def purge_asset(
    request: Request, asset_id: str, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    async with get_db(db_path) as db:
        async with db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            return HTMLResponse("", status_code=404)
        path = row["path"]
        if path and Path(path).exists():
            try:
                Path(path).unlink()
            except Exception:
                pass
        await db.execute(
            "UPDATE assets SET purged_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), asset_id),
        )
        await db.commit()
    return HTMLResponse("")


@router.post("/assets/purge-eligible", response_class=HTMLResponse, include_in_schema=False)
async def purge_eligible(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    now = datetime.now(timezone.utc).isoformat()
    async with get_db(db_path) as db:
        async with db.execute(
            "SELECT id, path FROM assets WHERE purge_at IS NOT NULL AND purge_at <= ? AND purged_at IS NULL",
            (now,),
        ) as cur:
            rows = await cur.fetchall()
        for r in rows:
            if r["path"] and Path(r["path"]).exists():
                try:
                    Path(r["path"]).unlink()
                except Exception:
                    pass
            await db.execute("UPDATE assets SET purged_at = ? WHERE id = ?", (now, r["id"]))
        await db.commit()
    assets = await _asset_rows(db_path)
    return _tr(request, "assets_body.html", {"assets": assets})


# ---------------------------------------------------------------------------
# Arduino job
# ---------------------------------------------------------------------------


@router.get("/jobs/new-arduino", response_class=HTMLResponse, include_in_schema=False)
async def new_arduino_job_page(
    request: Request, hil_token: str = Cookie(default="")
) -> HTMLResponse:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()
    db_path: str = request.app.state.db_path
    mcu_devices = [d for d in await _devices(db_path) if d["kind"] == "microcontroller"]
    assets = await _asset_rows(db_path)
    recent_assets = [a for a in assets if a["kind"] == "firmware" and not a.get("purged_at")][:10]
    return _tr(request, "job_new_arduino.html", {
        "token": hil_token,
        "active": "jobs",
        "mcu_devices": mcu_devices,
        "recent_assets": recent_assets,
        "disk": _disk_info(_jobs_dir()),
        "form": None,
        "error": None,
    })


@router.post("/jobs/arduino", include_in_schema=False, response_model=None)
async def submit_arduino_job(
    request: Request,
    hil_token: str = Cookie(default=""),
    firmware_source: Annotated[str, Form()] = "url",
    firmware_url: Annotated[str, Form()] = "",
    reuse_asset_id: Annotated[str, Form()] = "",
    flasher: Annotated[str, Form()] = "esptool",
    flash_args: Annotated[str, Form()] = "",
    purge_days: Annotated[str, Form()] = "30",
    device_id: Annotated[str, Form()] = "",
    pool: Annotated[str, Form()] = "public",
    timeout_flash: Annotated[str, Form()] = "120",
    timeout_total: Annotated[str, Form()] = "300",
    firmware_file: UploadFile | None = File(default=None),
) -> Response:
    if not (await _check_web_token(request, hil_token)):
        return _login_redirect()

    db_path: str = request.app.state.db_path
    mcu_devices = [d for d in await _devices(db_path) if d["kind"] == "microcontroller"]
    assets = await _asset_rows(db_path)
    recent_assets = [a for a in assets if a["kind"] == "firmware" and not a.get("purged_at")][:10]
    jdir = _jobs_dir()

    form_vals = {
        "firmware_source": firmware_source, "firmware_url": firmware_url,
        "reuse_asset_id": reuse_asset_id, "flasher": flasher, "flash_args": flash_args,
        "purge_days": purge_days, "device_id": device_id, "pool": pool,
        "timeout_flash": timeout_flash, "timeout_total": timeout_total,
    }

    def _err(msg: str) -> HTMLResponse:
        return _tr(request, "job_new_arduino.html", {
            "token": hil_token, "active": "jobs",
            "mcu_devices": mcu_devices, "recent_assets": recent_assets,
            "disk": _disk_info(jdir), "form": form_vals, "error": msg,
        })

    asset_id: str | None = None
    resolved_url: str = ""
    resolved_path: str = ""

    if firmware_source == "upload":
        if reuse_asset_id:
            # reuse existing asset
            async with get_db(db_path) as db:
                async with db.execute("SELECT * FROM assets WHERE id = ?", (reuse_asset_id,)) as cur:
                    existing = await cur.fetchone()
            if not existing:
                return _err("Selected asset not found")
            asset_id = existing["id"]
            resolved_path = existing["path"]
            resolved_url = existing["url"] or ""
        elif firmware_file and firmware_file.filename:
            # save uploaded file
            aid = str(uuid.uuid4())
            save_dir = Path(jdir) / "firmware" / aid
            save_dir.mkdir(parents=True, exist_ok=True)
            dest = save_dir / firmware_file.filename
            content = await firmware_file.read()
            dest.write_bytes(content)
            size = len(content)
            days = int(purge_days or 0)
            purge_at = None
            if days:
                from datetime import timedelta
                purge_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
            async with get_db(db_path) as db:
                await db.execute(
                    """INSERT INTO assets (id, filename, path, size_bytes, kind, job_id, created_at, purge_at)
                       VALUES (?, ?, ?, ?, 'firmware', NULL, ?, ?)""",
                    (aid, firmware_file.filename, str(dest), size,
                     datetime.now(timezone.utc).isoformat(), purge_at),
                )
                await db.commit()
            asset_id = aid
            resolved_path = str(dest)
        else:
            return _err("Select a file to upload or choose a previously uploaded firmware")
    else:
        if not firmware_url:
            return _err("Firmware URL is required")
        # store as URL-only asset (no local file)
        aid = str(uuid.uuid4())
        fname = Path(firmware_url.split("?")[0]).name or "firmware.bin"
        async with get_db(db_path) as db:
            await db.execute(
                """INSERT INTO assets (id, filename, url, size_bytes, kind, job_id, created_at)
                   VALUES (?, ?, ?, 0, 'firmware', NULL, ?)""",
                (aid, fname, firmware_url, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
        asset_id = aid
        resolved_url = firmware_url

    target: dict = {"pool": pool}
    if device_id:
        target["device"] = {"id": device_id}
    else:
        target["device"] = {"kind": "microcontroller"}

    extra_flash = shlex.split(flash_args) if flash_args.strip() else []
    job_req = {
        "target": target,
        "script": "firmware-flash",
        "payload": {
            "kind": "firmware-binary",
            "source": {
                "asset_id": asset_id,
                "url": resolved_url,
                "path": resolved_path,
                "flasher": flasher,
            },
        },
        "params": {"flasher": flasher, "flash_args": extra_flash},
        "timeouts": {
            "total_s": int(timeout_total or 300),
            "flash_s": int(timeout_flash or 120),
            "run_s": 60,
            "deploy_s": 60,
        },
    }

    try:
        resp = await _call_jobs_api(request, job_req, hil_token)
        job_id = resp["id"]
        # link asset to job
        async with get_db(db_path) as db:
            await db.execute("UPDATE assets SET job_id = ? WHERE id = ?", (job_id, asset_id))
            await db.commit()
    except Exception as exc:
        return _err(str(exc))

    return RedirectResponse(f"/ui/jobs/{job_id}", status_code=status.HTTP_303_SEE_OTHER)
