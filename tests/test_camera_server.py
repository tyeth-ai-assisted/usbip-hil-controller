"""HTTP-server tests for tools/camera-server.

Uses a FakeBackend so the tests don't need real camera hardware.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

CAMERA_SERVER_DIR = Path(__file__).resolve().parents[1] / "tools" / "camera-server"
sys.path.insert(0, str(CAMERA_SERVER_DIR))

from backends.base import Backend, FrameConfig  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

from server import SnapshotHandler  # noqa: E402


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, cfg: FrameConfig, *, jpeg_payload: bytes = b"\xff\xd8\xff\xd9"):
        super().__init__(cfg)
        self._payload = jpeg_payload
        self._counter = 0

    def supports_autofocus(self) -> bool:
        return True

    def _open(self) -> None:
        pass

    def _grab_jpeg(self) -> bytes:
        self._counter += 1
        # Vary the payload so /stream sees changing timestamps + bytes.
        return self._payload + self._counter.to_bytes(2, "big")

    def _close(self) -> None:
        pass


@pytest.fixture
def running_server():
    backend = FakeBackend(FrameConfig(width=320, height=240, fps=30))
    backend.start()
    server = ThreadingHTTPServer(("127.0.0.1", 0), SnapshotHandler)
    server.backend = backend
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        backend.stop()


def test_health_reports_backend_metadata(running_server):
    port = running_server
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
        assert r.status == 200
        body = json.loads(r.read())
    assert body["ok"] is True
    assert body["backend"] == "fake"
    assert body["autofocus"] is True
    assert body["width"] == 320
    assert body["height"] == 240


def test_snapshot_returns_jpeg(running_server):
    port = running_server
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
        assert r.status == 200
        assert r.headers["Content-Type"] == "image/jpeg"
        body = r.read()
    assert body.startswith(b"\xff\xd8\xff\xd9")  # FakeBackend payload prefix


def test_stream_emits_multiple_distinct_frames(running_server):
    port = running_server
    parts: list[bytes] = []
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/stream", timeout=3) as r:
        assert "multipart/x-mixed-replace" in r.headers["Content-Type"]
        deadline = time.monotonic() + 2.0
        buf = b""
        while time.monotonic() < deadline and len(parts) < 3:
            chunk = r.read(4096)
            if not chunk:
                break
            buf += chunk
            # crude split: each frame ends with the JPEG EOI \xff\xd9 + \r\n
            while b"\xff\xd9" in buf:
                idx = buf.index(b"\xff\xd9") + 2
                parts.append(buf[:idx])
                buf = buf[idx:]
    assert len(parts) >= 2, "stream should emit at least two frames in 2s"
    # Successive frames should differ (FakeBackend counter advances).
    assert parts[0] != parts[1]


def test_unknown_path_returns_404(running_server):
    port = running_server
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=2)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        pytest.fail("expected 404")
