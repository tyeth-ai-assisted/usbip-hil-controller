"""Picamera2 backend (libcamera-based; Pi CSI and many libcamera boards).

Continuous AF runs on the camera's own pipeline so snapshots stay sharp
without per-request AF cycles.
"""
from __future__ import annotations

import io

from .base import Backend, BackendUnavailable, FrameConfig

try:
    from picamera2 import Picamera2  # type: ignore[import-not-found]
    from libcamera import controls  # type: ignore[import-not-found]
    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001
    Picamera2 = None  # type: ignore[assignment]
    controls = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


class Picamera2Backend(Backend):
    name = "picamera2"

    def __init__(self, cfg: FrameConfig, *, camera_num: int = 0):
        super().__init__(cfg)
        self._camera_num = camera_num
        self._cam = None

    def supports_autofocus(self) -> bool:
        return True

    def _open(self) -> None:
        if Picamera2 is None:
            raise BackendUnavailable(f"picamera2 not importable: {_IMPORT_ERROR}")
        try:
            cam = Picamera2(camera_num=self._camera_num)
            config = cam.create_still_configuration(
                main={"size": (self.cfg.width, self.cfg.height), "format": "RGB888"}
            )
            cam.configure(config)
            cam.start()
            # Best-effort continuous AF with full range; ignore on sensors
            # without an AF motor (e.g. CM2, CM HQ).
            try:
                cam.set_controls(
                    {
                        "AfMode": controls.AfModeEnum.Continuous,
                        "AfRange": controls.AfRangeEnum.Full,
                        "AfSpeed": controls.AfSpeedEnum.Fast,
                    }
                )
            except Exception:
                pass
            self._cam = cam
        except BackendUnavailable:
            raise
        except Exception as exc:
            raise BackendUnavailable(f"picamera2 open failed: {exc}") from exc

    def _grab_jpeg(self) -> bytes:
        assert self._cam is not None
        buf = io.BytesIO()
        # capture_file with format="jpeg" uses the hardware ISP path when
        # available and respects JPEG quality via the global config.
        self._cam.options["quality"] = self.cfg.jpeg_quality
        self._cam.capture_file(buf, format="jpeg")
        return buf.getvalue()

    def _close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.stop()
                self._cam.close()
            finally:
                self._cam = None


BACKEND_CLASS = Picamera2Backend
