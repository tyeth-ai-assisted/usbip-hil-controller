#!/usr/bin/env python3
"""Minimal JPEG snapshot HTTP server for Raspberry Pi CSI camera.

Serves a fresh single-frame JPEG on every GET request by invoking
rpicam-still. Suitable for low-traffic HIL bench use on a Pi Zero 2W.

Usage:
    python3 snapshot_server.py [--port 8080]

Endpoints:
    GET /           → image/jpeg snapshot
    GET /health     → 200 OK text
"""
import argparse
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

RPICAM_CMD = [
    "rpicam-still",
    "--nopreview",
    "--autofocus-mode", "auto",
    "--autofocus-range", "full",
    "--autofocus-on-capture",
    "--output", "-",
    "--encoding", "jpg",
    "--quality", "85",
    "--timeout", "5000",
]


class SnapshotHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            result = subprocess.run(RPICAM_CMD, capture_output=True, timeout=15)
        except subprocess.TimeoutExpired:
            self.send_error(503, "Camera timed out")
            return

        if result.returncode != 0:
            self.send_error(503, f"rpicam-still failed: {result.stderr.decode()[:200]}")
            return

        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(result.stdout)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(result.stdout)

    def log_message(self, fmt, *args):
        if self.path != "/health":
            print(f"{self.address_string()} - {fmt % args}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--bind", default="0.0.0.0")
    args = ap.parse_args()

    server = HTTPServer((args.bind, args.port), SnapshotHandler)
    print(f"HIL snapshot server listening on {args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
