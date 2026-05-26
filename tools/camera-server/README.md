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
git clone https://github.com/Gundry-Consultancy/usbip-hil-controller.git ~/usbip-hil-controller

# IMX519 only: graft the AF block into libcamera's tuning file. Upstream
# omits it even though the IPA library has the AF algorithm compiled in.
sudo python3 ~/usbip-hil-controller/tools/camera-server/tuning/imx519-af-patch.py

sudo cp ~/usbip-hil-controller/tools/camera-server/hil-camera.service /etc/systemd/system/
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

## IMX519 autofocus footnote

Pi OS ships an `imx519.json` tuning file with no `rpi.af` algorithm block — but the libcamera IPA library does have the AF algorithm code compiled in. Symptom: `set_controls({"AfMode": Continuous})` emits `WARN IPARPI ipa_base.cpp: Could not set AF_MODE - no AF algorithm` and the lens never moves. `tuning/imx519-af-patch.py` adds an AF block (lens map `[0.0, 0, 32.0, 1023]`, matching the Arducam IMX519 16MP module) and continuous AF starts working immediately on next libcamera open. The patch is idempotent; the original tuning is saved as `imx519.json.preaf`.
