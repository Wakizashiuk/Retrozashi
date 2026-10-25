"""Screenshot capture with multi-monitor / single-window targeting and coordinate scaling.

Claude sees one monitor, or just one emulator window, downscaled to fit the API's image
limits. Window targeting gives sharper detail for the same token cost, hides everything else
on screen, and (via clamping) keeps every click inside that window. Every
coordinate Claude returns is in that downscaled image's pixel space, so `Frame.to_screen`
maps it back to physical virtual-desktop pixels for SendInput/SetCursorPos.
"""
from __future__ import annotations

import io
import math
import time
from dataclasses import dataclass
from typing import Callable

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


@dataclass(frozen=True)
class WindowTarget:
    hwnd: int
    title: str
    emulator: str


class CaptureTargetLost(Exception):
    """The targeted window closed or was minimized."""


def clip(rect: Rect, bounds: Rect) -> Rect:
    """Intersect a window rect with the virtual desktop (windows can hang off-screen)."""
    left, top = max(rect.left, bounds.left), max(rect.top, bounds.top)
    right = min(rect.left + rect.width, bounds.left + bounds.width)
    bottom = min(rect.top + rect.height, bounds.top + bounds.height)
    if right <= left or bottom <= top:
        raise CaptureTargetLost("Target window is entirely off-screen")
    return Rect(left, top, right - left, bottom - top)


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


def frame_difference(a: bytes, b: bytes) -> float:
    """Mean absolute difference of two equal-size grayscale buffers, 0.0 (identical) .. 1.0."""
    if len(a) != len(b) or not a:
        return 1.0
    return sum(abs(x - y) for x, y in zip(a, b)) / (255 * len(a))


# A blinking caret or a hover highlight stays under this; a dialog opening or a page change doesn't.
SETTLE_THRESHOLD = 0.003


def _encode(raw_rgb: bytes, size: tuple[int, int], out_size: tuple[int, int]) -> bytes:
    from PIL import Image

    img = Image.frombytes("RGB", size, raw_rgb)
    if out_size != size:
        img = img.resize(out_size, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False, compress_level=3)
    return buf.getvalue()


def _win32_window_rect(hwnd: int) -> Rect | None:
    from .win32 import window_rect

    r = window_rect(hwnd)
    return Rect(*r) if r else None


class ScreenCapture:
    def __init__(self, monitor_index: int = 1, max_long_edge: int = DEFAULT_MAX_LONG_EDGE,
                 window_rect_fn: Callable[[int], Rect | None] = _win32_window_rect) -> None:
        self.monitor_index = monitor_index
        self.max_long_edge = max_long_edge
        self.window: WindowTarget | None = None
        self._window_rect = window_rect_fn
        self.last: Frame | None = None

    def set_monitor(self, index: int) -> None:
        self.monitor_index = index
        self.window = None
        self.last = None  # old coordinates mean nothing now: force a fresh screenshot

    def set_window(self, target: WindowTarget | None) -> None:
        self.window = target
        self.last = None

    def describe_target(self) -> str:
        return f"window '{self.window.title}'" if self.window else f"monitor {self.monitor_index}"

    def _current_window_rect(self) -> Rect:
        rect = self._window_rect(self.window.hwnd)
        if rect is None:
            lost = self.window
            self.set_window(None)
            raise CaptureTargetLost(f"Window '{lost.title}' was closed or minimized; "
                                    f"capture is back on monitor {self.monitor_index}.")
        return rect

    def frame_still_valid(self, frame: Frame) -> bool:
        """False if the targeted window moved/resized since `frame` was taken."""
        if self.window is None:
            return True
        return clip(self._current_window_rect(), self.list_monitors()[0]) == frame.source

    @staticmethod
    def list_monitors() -> list[Rect]:
        """Index 0 = the whole virtual desktop, 1..N = individual monitors (mss convention)."""
        import mss

        with mss.mss() as sct:
            return [Rect(m["left"], m["top"], m["width"], m["height"]) for m in sct.monitors]

    def target_rect(self) -> Rect:
        monitors = self.list_monitors()
        if self.window is not None:
            return clip(self._current_window_rect(), monitors[0])
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

    def _thumbnail(self, rect: Rect) -> bytes:
        """1/16-scale grayscale grab: cheap enough to poll several times a second."""
        import mss
        from PIL import Image

        with mss.mss() as sct:
            shot = sct.grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
        img = Image.frombytes("RGB", (shot.width, shot.height), shot.rgb).convert("L")
        return img.resize((max(1, shot.width // 16), max(1, shot.height // 16))).tobytes()

    def wait_until_stable(self, sleep: Callable[[float], None], timeout: float = 1.5,
                          interval: float = 0.12) -> bool:
        """Poll until two consecutive thumbnails match, so the screenshot shows the result of the
        last action rather than a half-drawn dialog. False if still changing at `timeout`
        (a running game or video never settles; that's fine, we capture anyway)."""
        rect = self.target_rect()
        deadline = time.monotonic() + timeout
        prev = self._thumbnail(rect)
        while time.monotonic() < deadline:
            sleep(interval)
            cur = self._thumbnail(rect)
            if frame_difference(prev, cur) <= SETTLE_THRESHOLD:
                return True
            prev = cur
        return False

    def zoom(self, region: list[int]) -> bytes:
        """`region` is [x0, y0, x1, y1] in the last screenshot's coordinates. Returns a higher-detail
        crop. It does NOT replace `last`: coordinates stay in full-screenshot space after a zoom."""
        frame = self.last or self.capture()
        x0, y0 = frame.to_screen(region[0], region[1])
        x1, y1 = frame.to_screen(region[2], region[3])
        rect = Rect(min(x0, x1), min(y0, y1), max(1, abs(x1 - x0)), max(1, abs(y1 - y0)))
        return self._grab(rect, API_MAX_LONG_EDGE).png
