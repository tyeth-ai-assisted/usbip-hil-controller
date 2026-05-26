#!/usr/bin/env python3
"""HTTP server: single JPEG, MJPEG stream, health.

Endpoints:
    GET /         -> image/jpeg (latest frame from the warm pipeline)
    GET /stream   -> multipart/x-mixed-replace MJPEG stream
    GET /health   -> 200 OK with backend name + AF state
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Allow running as a script (python3 server.py) without installing as a package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from backends import Backend, BackendUnavailable, autodetect, load_backend
    from backends.base import FrameConfig
else:
    from .backends import Backend, BackendUnavailable, autodetect, load_backend
    from .backends.base import FrameConfig


MJPEG_BOUNDARY = "hilcamframe"


class SnapshotHandler(BaseHTTPRequestHandler):
    backend: Backend  # set on the server instance below

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("", "/"):
            self._serve_snapshot()
        elif path == "/health":
            self._serve_health()
        elif path == "/stream":
            self._serve_stream()
        else:
            self.send_error(404, "not found")

    def _serve_snapshot(self) -> None:
        try:
            jpeg = self.server.backend.read_jpeg()  # type: ignore[attr-defined]
        except TimeoutError as exc:
            self.send_error(503, str(exc))
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(jpeg)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(jpeg)

    def _serve_health(self) -> None:
        backend: Backend = self.server.backend  # type: ignore[attr-defined]
        body = json.dumps(
            {
                "ok": True,
                "backend": backend.name,
                "autofocus": backend.supports_autofocus(),
                "width": backend.cfg.width,
                "height": backend.cfg.height,
                "fps": backend.cfg.fps,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_stream(self) -> None:
        backend: Backend = self.server.backend  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header(
            "Content-Type",
            f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY}",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        last_ts = 0.0
        try:
            while True:
                # Re-read until we get a frame newer than the one we last sent
                # so /stream doesn't burn CPU repeating identical bytes.
                for _ in range(50):
                    with backend._lock:  # noqa: SLF001 — internal coupling is intentional
                        ts = backend._latest_ts  # noqa: SLF001
                        jpeg = backend._latest  # noqa: SLF001
                    if jpeg is not None and ts != last_ts:
                        break
                    time.sleep(0.02)
                else:
                    continue
                last_ts = ts
                part = (
                    f"--{MJPEG_BOUNDARY}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(jpeg)}\r\n\r\n"
                ).encode()
                self.wfile.write(part)
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            return

    def log_message(self, fmt: str, *args) -> None:
        if self.path == "/health":
            return
        print(f"{self.address_string()} - {fmt % args}", flush=True)


def build_backend(args) -> Backend:
    cfg = FrameConfig(
        width=args.width,
        height=args.height,
        fps=args.fps,
        jpeg_quality=args.jpeg_quality,
    )
    kwargs: dict = {}
    if args.device:
        kwargs["device"] = args.device
    if args.camera_num is not None:
        kwargs["camera_num"] = args.camera_num

    if args.backend == "auto":
        return autodetect(cfg, **{k: v for k, v in kwargs.items() if k != "device"})
    # picamera2 doesn't take device=; v4l2 doesn't take camera_num=. Filter.
    if args.backend == "picamera2":
        return load_backend("picamera2", cfg, camera_num=args.camera_num or 0)
    if args.backend == "v4l2":
        return load_backend("v4l2", cfg, device=args.device or "/dev/video0")
    raise BackendUnavailable(f"unknown backend: {args.backend}")


def main() -> None:
    ap = argparse.ArgumentParser(description="HIL camera snapshot server")
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument(
        "--backend",
        choices=("auto", "picamera2", "v4l2"),
        default="auto",
    )
    ap.add_argument("--device", default=None, help="V4L2 device path (v4l2 backend)")
    ap.add_argument(
        "--camera-num", type=int, default=None, help="libcamera camera index (picamera2)"
    )
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=10)
    ap.add_argument("--jpeg-quality", type=int, default=85)
    args = ap.parse_args()

    try:
        backend = build_backend(args)
    except BackendUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    backend.start()

    server = ThreadingHTTPServer((args.bind, args.port), SnapshotHandler)
    server.backend = backend  # type: ignore[attr-defined]
    print(
        f"HIL snapshot server: backend={backend.name} "
        f"af={backend.supports_autofocus()} "
        f"resolution={args.width}x{args.height}@{args.fps} "
        f"listening on {args.bind}:{args.port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        backend.stop()


if __name__ == "__main__":
    main()
