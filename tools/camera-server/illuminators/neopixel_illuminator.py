"""NeoPixel illuminator via Adafruit Blinka + CircuitPython NeoPixel.

Designed for an Adafruit STEMMA-connected ring (e.g. on the 2.13" eInk
Bonnet's 3-pin STEMMA connector). Defaults to GPIO5 (D5) — override
with the ``pin`` argument to match a different wiring.

CircuitPython's ``neopixel`` library on Pi uses ``rpi_ws281x`` under the
hood, which needs DMA/PWM access. The systemd unit runs as root by
default on hosts where this driver is enabled; without root the driver
raises ``IlluminatorUnavailable`` and the server falls back to Null.
"""
from __future__ import annotations

from .base import Illuminator, IlluminatorUnavailable

try:
    import board  # type: ignore[import-not-found]
    import neopixel  # type: ignore[import-not-found]
    _IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001
    board = None  # type: ignore[assignment]
    neopixel = None  # type: ignore[assignment]
    _IMPORT_ERROR = exc


class NeoPixelIlluminator(Illuminator):
    kind = "neopixel"

    def __init__(
        self,
        *,
        pin: str = "D5",
        count: int = 32,
        color: tuple[int, int, int] = (255, 255, 255),
    ) -> None:
        if neopixel is None or board is None:
            raise IlluminatorUnavailable(
                f"adafruit_blinka/neopixel not importable: {_IMPORT_ERROR}"
            )
        pin_obj = getattr(board, pin, None)
        if pin_obj is None:
            raise IlluminatorUnavailable(f"board has no pin {pin!r}")
        try:
            # auto_write=False so we can batch fill+show; brightness controls
            # the overall PWM amplitude across all pixels.
            self._strip = neopixel.NeoPixel(
                pin_obj, count, brightness=0.0, auto_write=False
            )
        except Exception as exc:
            raise IlluminatorUnavailable(f"NeoPixel init failed: {exc}") from exc
        self._count = count
        self._color = color
        self._brightness = 0
        # Pre-fill colour; brightness is what we'll vary.
        self._strip.fill(color)
        self._strip.show()

    def set_brightness(self, value: int) -> None:
        v = max(0, min(255, int(value)))
        self._brightness = v
        self._strip.brightness = v / 255.0
        # Re-fill in case anything stomped the colour state, then show.
        self._strip.fill(self._color)
        self._strip.show()

    def get_brightness(self) -> int:
        return self._brightness

    def close(self) -> None:
        try:
            self._strip.brightness = 0.0
            self._strip.fill((0, 0, 0))
            self._strip.show()
            self._strip.deinit()
        except Exception:
            pass
