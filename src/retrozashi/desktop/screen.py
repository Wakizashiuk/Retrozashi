"""Screenshot capture with multi-monitor targeting and coordinate scaling.

Claude sees one monitor (or one rect), downscaled to fit the API's image limits. Every
coordinate Claude returns is in that downscaled image's pixel space, so `Frame.to_screen`
maps it back to physical virtual-desktop pixels for SendInput/SetCursorPos.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass

# API limits for models that support computer_toolset_20260801.
API_MAX_LONG_EDGE = 2576
API_MAX_VISUAL_TOKENS = 4784  # ceil(w/28) * ceil(h/28)
# Cost/accuracy sweet spot: 4K monitors get sent at 1080p-class resolution.
DEFAULT_MAX_LONG_EDGE = 1920


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int


def visual_tokens(w: int, h: int) -> int:
    return math.ceil(w / 28) * math.ceil(h / 28)


def compute_scale(width: int, height: int, max_long_edge: int = DEFAULT_MAX_LONG_EDGE) -> float:
    """Largest scale <= 1 so the image fits both the long-edge and visual-token limits."""
    long_edge = min(max_long_edge, API_MAX_LONG_EDGE)
    scale = min(1.0, long_edge / max(width, height))
    # Token budget is roughly area/784; solve, then nudge down for ceil() rounding.
    while visual_tokens(int(width * scale), int(height * scale)) > API_MAX_VISUAL_TOKENS:
        scale = min(scale * 0.98, math.sqrt(API_MAX_VISUAL_TOKENS * 784 / (width * height)))
    return scale


@dataclass(frozen=True)
class Frame:
    png: bytes
    width: int          # size of the image sent to Claude
    height: int
    source: Rect        # physical rect it was captured from
    scale: float        # image px = physical px * scale

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        """Claude image coords -> physical virtual-desktop coords, clamped inside the source rect."""
        px = self.source.left + round(x / self.scale)
        py = self.source.top + round(y / self.scale)
        px = max(self.source.left, min(px, self.source.left + self.source.width - 1))
        py = max(self.source.top, min(py, self.source.top + self.source.height - 1))
        return px, py

    def to_image(self, px: int, py: int) -> tuple[int, int]:
        """Physical coords -> Claude image coords (used for cursor_position)."""
        return round((px - self.source.left) * self.scale), round((py - self.source.top) * self.scale)


def _encode(raw_rgb: bytes, size: tuple[int, int], out_size: tuple[int, int]) -> bytes:
    from PIL import Image

    img = Image.frombytes("RGB", size, raw_rgb)
    if out_size != size:
        img = img.resize(out_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=3)
    return buf.getvalue()


class ScreenCapture:
    def __init__(self, monitor_index: int = 1, max_long_edge: int = DEFAULT_MAX_LONG_EDGE) -> None:
        self.monitor_index = monitor_index
        self.max_long_edge = max_long_edge
        self.last: Frame | None = None

    @staticmethod
    def list_monitors() -> list[Rect]:
        """Index 0 = the whole virtual desktop, 1..N = individual monitors (mss convention)."""
        import mss

        with mss.mss() as sct:
            return [Rect(m["left"], m["top"], m["width"], m["height"]) for m in sct.monitors]

    def target_rect(self) -> Rect:
        monitors = self.list_monitors()
        if not 0 <= self.monitor_index < len(monitors):
            raise ValueError(f"Monitor {self.monitor_index} not found ({len(monitors) - 1} connected)")
        return monitors[self.monitor_index]

    def _grab(self, rect: Rect, max_long_edge: int) -> Frame:
        import mss  # created per call: mss handles are not thread-safe on Windows

        with mss.mss() as sct:
            shot = sct.grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
        scale = compute_scale(shot.width, shot.height, max_long_edge)
        out = (max(1, int(shot.width * scale)), max(1, int(shot.height * scale)))
        png = _encode(shot.rgb, (shot.width, shot.height), out)
        return Frame(png, out[0], out[1], rect, scale)

    def capture(self) -> Frame:
        self.last = self._grab(self.target_rect(), self.max_long_edge)
        return self.last

    def zoom(self, region: list[int]) -> bytes:
        """`region` is [x0, y0, x1, y1] in the last screenshot's coordinates. Returns a higher-detail
        crop. It does NOT replace `last`: coordinates stay in full-screenshot space after a zoom."""
        frame = self.last or self.capture()
        x0, y0 = frame.to_screen(region[0], region[1])
        x1, y1 = frame.to_screen(region[2], region[3])
        rect = Rect(min(x0, x1), min(y0, y1), max(1, abs(x1 - x0)), max(1, abs(y1 - y0)))
        return self._grab(rect, API_MAX_LONG_EDGE).png
