# HIL Camera Snapshot Server

A small HTTP server that keeps a camera pipeline warm and serves fresh JPEG snapshots or an MJPEG stream. Pluggable backends keep it portable across libcamera Pi CSI sensors, UVC webcams, and future stacks.

## Endpoints

| Path      | Response                                                                 |
|-----------|--------------------------------------------------------------------------|
| `GET /`        | `image/jpeg` — latest frame from the warm pipeline (sub-100ms when warm) |
| `GET /stream`  | `multipart/x-mixed-replace` MJPEG stream (record + split frames client-side) |
| `GET /health`  | JSON: backend name, AF state, resolution                                 |

## Backends

| Name        | Hardware                          | AF                                | Deps                        |
|-------------|-----------------------------------|-----------------------------------|-----------------------------|
| `picamera2` | Pi CSI sensors via libcamera      | Continuous AF (full range)        | `python3-picamera2`         |
| `v4l2`      | UVC webcams via OpenCV/V4L2       | UVC `focus_auto` if exposed       | `python3-opencv`            |

`--backend auto` (default) tries each in order and uses the first that opens. Force a specific backend with `--backend picamera2` or `--backend v4l2`.

## Install on a camera host

Pi CSI host (e.g. rpi-displays):

```bash
sudo apt install -y python3-picamera2
# clone repo to /home/pi/hil-camera-server (or symlink server.py + backends/ there)
sudo cp tools/camera-server/hil-camera.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hil-camera
```

UVC webcam host:

```bash
sudo apt install -y python3-opencv v4l-utils
# then the same systemd steps; --backend auto picks v4l2 when picamera2 isn't installed
```

## CLI

```
server.py [--port 8080] [--backend auto|picamera2|v4l2]
          [--device /dev/video0] [--camera-num 0]
          [--width 1280] [--height 720] [--fps 10] [--jpeg-quality 85]
```

## Architecture

Each backend opens the camera once at startup and runs a daemon grabber thread that writes the latest JPEG into a single-slot buffer. HTTP handlers serve from that buffer — no per-request camera open, no per-request AF cycle. AF runs continuously inside the camera's own pipeline (libcamera for Pi CSI, UVC controls for webcams), so snapshots stay sharp without explicit triggering.

The `/stream` endpoint avoids re-sending identical frames by waiting for a new timestamp before emitting the next multipart part.
