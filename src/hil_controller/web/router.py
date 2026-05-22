"""Jinja2 / HTMX web interface for HIL controller admin."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Form, Request, status
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
