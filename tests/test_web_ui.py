"""Tests for the Jinja2/HTMX web UI."""

import pytest

TOKEN = "test-token-for-ci"
COOKIE = {"hil_token": TOKEN}


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_page_renders(client):
    r = await client.get("/ui/login")
    assert r.status_code == 200
    assert "Bearer Token" in r.text


@pytest.mark.asyncio
async def test_login_invalid_token_returns_401(client):
    r = await client.post("/ui/login", data={"token": "bad-token"})
    assert r.status_code == 401
    assert "Invalid token" in r.text


@pytest.mark.asyncio
async def test_login_valid_token_sets_cookie_and_redirects(client):
    r = await client.post("/ui/login", data={"token": TOKEN}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ui/"
    assert "hil_token" in r.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_logout_clears_cookie(client):
    r = await client.get("/ui/logout", follow_redirects=False)
    assert r.status_code == 303
    # cookie should be deleted (max-age=0 or expires in past)
    assert "hil_token" in r.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# Auth guard — unauthenticated requests redirect to /ui/login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_redirects_without_cookie(client):
    r = await client.get("/ui/", follow_redirects=False)
    assert r.status_code == 303
    assert "/ui/login" in r.headers["location"]


@pytest.mark.asyncio
async def test_hosts_redirects_without_cookie(client):
    r = await client.get("/ui/hosts", follow_redirects=False)
    assert r.status_code == 303


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_renders(client):
    r = await client.get("/ui/", cookies=COOKIE)
    assert r.status_code == 200
    assert "Dashboard" in r.text
    assert "Hosts" in r.text


# ---------------------------------------------------------------------------
# Hosts CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hosts_page_renders_empty(client):
    r = await client.get("/ui/hosts", cookies=COOKIE)
    assert r.status_code == 200
    assert "Hosts" in r.text


@pytest.mark.asyncio
async def test_new_host_form_renders(client):
    r = await client.get("/ui/hosts/form", cookies=COOKIE)
    assert r.status_code == 200
    assert "New Host" in r.text


@pytest.mark.asyncio
async def test_create_host_returns_updated_tbody(client):
    r = await client.post(
        "/ui/hosts",
        data={
            "id": "test-host-01",
            "role": "sbc-fleet",
            "addr": "10.0.0.1",
            "transport": "ssh",
            "ssh_user": "pi",
            "ssh_key_path": "/etc/hil/keys/test",
            "max_concurrent_jobs": "1",
            "capabilities": "linux, cameras",
            "status": "available",
        },
        cookies=COOKIE,
    )
    assert r.status_code == 200
    assert "test-host-01" in r.text


@pytest.mark.asyncio
async def test_create_host_missing_id_shows_error(client):
    r = await client.post(
        "/ui/hosts",
        data={"id": "", "role": "sbc-fleet", "addr": "", "transport": "ssh",
              "ssh_user": "pi", "status": "available"},
        cookies=COOKIE,
    )
    assert r.status_code == 200
    assert "required" in r.text.lower()


@pytest.mark.asyncio
async def test_edit_host_form_renders(client):
    # create first
    await client.post(
        "/ui/hosts",
        data={"id": "edit-host-01", "role": "sbc-fleet", "addr": "10.0.0.2",
              "transport": "ssh", "ssh_user": "pi", "status": "available"},
        cookies=COOKIE,
    )
    r = await client.get("/ui/hosts/edit-host-01/form", cookies=COOKIE)
    assert r.status_code == 200
    assert "edit-host-01" in r.text
    assert "Edit Host" in r.text


@pytest.mark.asyncio
async def test_update_host_returns_tbody(client):
    await client.post(
        "/ui/hosts",
        data={"id": "upd-host-01", "role": "sbc-fleet", "addr": "10.0.0.3",
              "transport": "ssh", "ssh_user": "pi", "status": "available"},
        cookies=COOKIE,
    )
    r = await client.post(
        "/ui/hosts/upd-host-01",
        data={"role": "microcontroller-fleet", "addr": "10.0.0.33", "transport": "ssh",
              "ssh_user": "admin", "status": "available"},
        cookies=COOKIE,
    )
    assert r.status_code == 200
    assert "upd-host-01" in r.text


@pytest.mark.asyncio
async def test_delete_host_returns_empty(client):
    await client.post(
        "/ui/hosts",
        data={"id": "del-host-01", "role": "sbc-fleet", "addr": "10.0.0.4",
              "transport": "ssh", "ssh_user": "pi", "status": "available"},
        cookies=COOKIE,
    )
    r = await client.delete("/ui/hosts/del-host-01", cookies=COOKIE)
    assert r.status_code == 200
    assert r.text == ""


# ---------------------------------------------------------------------------
# Devices CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devices_page_renders(client):
    r = await client.get("/ui/devices", cookies=COOKIE)
    assert r.status_code == 200
    assert "Devices" in r.text


@pytest.mark.asyncio
async def test_new_device_form_renders(client):
    r = await client.get("/ui/devices/form", cookies=COOKIE)
    assert r.status_code == 200
    assert "New Device" in r.text


@pytest.mark.asyncio
async def test_create_device(client):
    # need a host first
    await client.post(
        "/ui/hosts",
        data={"id": "dev-host-01", "role": "microcontroller-fleet", "addr": "10.0.1.1",
              "transport": "ssh", "ssh_user": "pi", "status": "available"},
        cookies=COOKIE,
    )
    r = await client.post(
        "/ui/devices",
        data={
            "id": "qtpy-test-01",
            "host_id": "dev-host-01",
            "kind": "microcontroller",
            "model": "esp32-s3",
            "pool": "public",
            "capabilities": "spi, i2c",
            "status": "available",
        },
        cookies=COOKIE,
    )
    assert r.status_code == 200
    assert "qtpy-test-01" in r.text


@pytest.mark.asyncio
async def test_delete_device(client):
    await client.post(
        "/ui/hosts",
        data={"id": "dev-host-02", "role": "microcontroller-fleet", "addr": "10.0.1.2",
              "transport": "ssh", "ssh_user": "pi", "status": "available"},
        cookies=COOKIE,
    )
    await client.post(
        "/ui/devices",
        data={"id": "del-device-01", "host_id": "dev-host-02", "kind": "microcontroller",
              "model": "rp2040", "pool": "public", "status": "available"},
        cookies=COOKIE,
    )
    r = await client.delete("/ui/devices/del-device-01", cookies=COOKIE)
    assert r.status_code == 200
    assert r.text == ""


# ---------------------------------------------------------------------------
# Hardware / Aux CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hardware_page_renders(client):
    r = await client.get("/ui/hardware", cookies=COOKIE)
    assert r.status_code == 200
    assert "Hardware" in r.text


@pytest.mark.asyncio
async def test_create_hardware(client):
    r = await client.post(
        "/ui/hardware",
        data={
            "id": "oled-test-01",
            "kind": "display",
            "model": "ssd1306",
            "interface": "i2c",
            "observability": "camera",
            "capabilities": "display:128x32",
            "pool": "public",
            "status": "available",
        },
        cookies=COOKIE,
    )
    assert r.status_code == 200
    assert "oled-test-01" in r.text


@pytest.mark.asyncio
async def test_delete_hardware(client):
    await client.post(
        "/ui/hardware",
        data={"id": "del-hw-01", "kind": "sensor", "model": "ens160",
              "interface": "i2c", "observability": "none", "pool": "public", "status": "available"},
        cookies=COOKIE,
    )
    r = await client.delete("/ui/hardware/del-hw-01", cookies=COOKIE)
    assert r.status_code == 200
    assert r.text == ""


# ---------------------------------------------------------------------------
# Cameras CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cameras_page_renders(client):
    r = await client.get("/ui/cameras", cookies=COOKIE)
    assert r.status_code == 200
    assert "Camera" in r.text


@pytest.mark.asyncio
async def test_create_camera(client):
    r = await client.post(
        "/ui/cameras",
        data={
            "id": "cam-bench-01",
            "model": "Wyze Cam v3",
            "interface": "rtsp://192.168.1.50/stream",
            "observability": "rtsp",
            "pool": "public",
            "status": "available",
        },
        cookies=COOKIE,
    )
    assert r.status_code == 200
    assert "cam-bench-01" in r.text


@pytest.mark.asyncio
async def test_delete_camera(client):
    await client.post(
        "/ui/cameras",
        data={"id": "del-cam-01", "model": "test", "interface": "rtsp://10.0.0.100",
              "observability": "rtsp", "pool": "public", "status": "available"},
        cookies=COOKIE,
    )
    r = await client.delete("/ui/cameras/del-cam-01", cookies=COOKIE)
    assert r.status_code == 200
    assert r.text == ""


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_delete_connection(client):
    # setup
    await client.post(
        "/ui/hosts",
        data={"id": "conn-host-01", "role": "microcontroller-fleet", "addr": "10.0.2.1",
              "transport": "ssh", "ssh_user": "pi", "status": "available"},
        cookies=COOKIE,
    )
    await client.post(
        "/ui/devices",
        data={"id": "conn-dev-01", "host_id": "conn-host-01", "kind": "microcontroller",
              "model": "esp32-s3", "pool": "public", "status": "available"},
        cookies=COOKIE,
    )
    await client.post(
        "/ui/hardware",
        data={"id": "conn-hw-01", "kind": "display", "model": "ssd1306", "interface": "i2c",
              "observability": "none", "pool": "public", "status": "available"},
        cookies=COOKIE,
    )
    r = await client.post(
        "/ui/connections",
        data={"aux_id": "conn-hw-01", "device_id": "conn-dev-01"},
        cookies=COOKIE,
    )
    assert r.status_code == 200
    assert "conn-dev-01" in r.text

    # find the connection id from the response
    import re
    m = re.search(r'id="conn-(\d+)"', r.text)
    assert m, "connection id not found in response"
    conn_id = m.group(1)

    r2 = await client.delete(f"/ui/connections/{conn_id}", cookies=COOKIE)
    assert r2.status_code == 200
    assert r2.text == ""


# ---------------------------------------------------------------------------
# Scripts page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scripts_page_no_dir_configured(client):
    r = await client.get("/ui/scripts", cookies=COOKIE)
    assert r.status_code == 200
    assert "HIL_SCRIPTS_DIR" in r.text


@pytest.mark.asyncio
async def test_scripts_page_with_dir(tmp_path, client):
    import json
    import os

    script = {"name": "Test Script", "description": "A test", "protoVersion": "v2", "steps": []}
    (tmp_path / "test-script.json").write_text(json.dumps(script))

    os.environ["HIL_SCRIPTS_DIR"] = str(tmp_path)
    from hil_controller import config as cfg

    cfg._settings = None  # force re-read

    try:
        r = await client.get("/ui/scripts", cookies=COOKIE)
        assert r.status_code == 200
        assert "Test Script" in r.text
    finally:
        os.environ.pop("HIL_SCRIPTS_DIR", None)
        cfg._settings = None


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_static_css_served(client):
    r = await client.get("/ui/static/app.css")
    assert r.status_code == 200
    assert "body" in r.text
