"""Executes computer_toolset_20260801 member calls against the real desktop.

Each `tool_use` block from Claude has `name` = member ("left_click", "key", ...) and
`input` = that member's parameters. `execute()` returns a tool_result content list.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ..core.safety import KillSwitch
from .keymap import MODIFIER_VKS, parse_combo
from .screen import CaptureTargetLost, ScreenCapture


class InputBackend(Protocol):
    def set_cursor_pos(self, x: int, y: int) -> None: ...
    def get_cursor_pos(self) -> tuple[int, int]: ...
    def mouse_button(self, button: str, down: bool) -> None: ...
    def mouse_wheel(self, clicks: int, horizontal: bool = False) -> None: ...
    def key_down(self, vk: int) -> None: ...
    def key_up(self, vk: int) -> None: ...
    def type_unicode(self, text: str) -> None: ...


class Win32Backend:
    """Default backend: straight through to desktop/win32.py."""

    def __getattr__(self, name: str) -> Any:
        from . import win32
        return getattr(win32, name)


class ActionError(Exception):
    pass


@dataclass
class ActionResult:
    content: list[dict[str, Any]]
    summary: str  # one-line human description for the status panel


def _text(msg: str) -> list[dict[str, Any]]:
    return [{"type": "text", "text": msg}]


def _image(png: bytes) -> list[dict[str, Any]]:
    return [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(png).decode()}}]


CLICKS = {
    "left_click": ("left", 1), "right_click": ("right", 1), "middle_click": ("middle", 1),
    "double_click": ("left", 2), "triple_click": ("left", 3),
}


class ActionExecutor:
    def __init__(self, screen: ScreenCapture, kill: KillSwitch, backend: InputBackend | None = None,
                 action_delay: float = 0.15, settle_timeout: float = 0.0) -> None:
        self.screen = screen
        self.kill = kill
        self.io: InputBackend = backend or Win32Backend()
        self.action_delay = action_delay
        self.settle_timeout = settle_timeout
        self._dirty = False  # an input action happened since the last screenshot
        self._held_keys: set[int] = set()
        self._held_buttons: set[str] = set()
        kill.on_trip(self.release_all)

    # ---- public -------------------------------------------------------------
    def execute(self, member: str, inp: dict[str, Any]) -> ActionResult:
        self.kill.check()
        handler = getattr(self, f"_do_{member}", None)
        if handler is None:
            raise ActionError(f"Unsupported computer action: {member}")
        result = handler(inp)
        if member not in ("screenshot", "zoom", "cursor_position", "wait"):
            self._dirty = True
            self.kill.wait(self.action_delay)  # lets you follow each step
        return result

    def release_all(self) -> None:
        """Called on kill-switch trip: never leave a key or button stuck down."""
        for vk in list(self._held_keys):
            try:
                self.io.key_up(vk)
            except Exception:
                pass
        for btn in list(self._held_buttons):
            try:
                self.io.mouse_button(btn, down=False)
            except Exception:
                pass
        self._held_keys.clear()
        self._held_buttons.clear()

    # ---- helpers ------------------------------------------------------------
    def _move(self, coord: list[int] | None) -> tuple[int, int] | None:
        if coord is None:
            return None
        frame = self.screen.last
        # Coordinates are only meaningful against the screenshot Claude actually saw.
        if frame is None:
            raise ActionError(f"No screenshot of the current capture target ({self.screen.describe_target()}) "
                              "yet; take a screenshot first.")
        try:
            if not self.screen.frame_still_valid(frame):
                raise ActionError("The target window moved or resized since the last screenshot; take a new one.")
        except CaptureTargetLost as e:
            raise ActionError(str(e)) from e
        x, y = frame.to_screen(coord[0], coord[1])
        self.kill.check()
        self.io.set_cursor_pos(x, y)
        return x, y

    def _press(self, vks: list[int]) -> None:
        for vk in vks:
            self.kill.check()
            self.io.key_down(vk)
            self._held_keys.add(vk)

    def _release(self, vks: list[int]) -> None:
        for vk in reversed(vks):
            self.io.key_up(vk)
            self._held_keys.discard(vk)

    def _modifiers(self, inp: dict[str, Any]) -> list[int]:
        mods = parse_combo(inp["text"]) if inp.get("text") else []
        if any(vk not in MODIFIER_VKS for vk in mods):
            raise ActionError(f"'text' on a click/scroll must be modifier keys only, got {inp['text']!r}")
        return mods

    # ---- members ------------------------------------------------------------
    def _do_screenshot(self, inp: dict[str, Any]) -> ActionResult:
        note: list[dict[str, Any]] = []
        if self._dirty and self.settle_timeout > 0:
            self._dirty = False
            try:
                if not self.screen.wait_until_stable(self.kill.wait, self.settle_timeout):
                    note = _text("Screen was still changing when captured (animation or loading); "
                                 "take another screenshot if something looks mid-transition.")
            except CaptureTargetLost:
                pass  # handled by the capture below
        try:
            frame = self.screen.capture()
        except CaptureTargetLost as e:  # window gone: fall back to the monitor and tell Claude
            frame = self.screen.capture()
            return ActionResult(_text(str(e)) + _image(frame.png), "Target window lost; monitor screenshot")
        return ActionResult(note + _image(frame.png),
                            f"Screenshot {frame.width}x{frame.height} of {self.screen.describe_target()}"
                            + (" (still changing)" if note else ""))

    def _do_zoom(self, inp: dict[str, Any]) -> ActionResult:
        return ActionResult(_image(self.screen.zoom(inp["region"])), f"Zoom {inp['region']}")

    def _click(self, member: str, inp: dict[str, Any]) -> ActionResult:
        button, count = CLICKS[member]
        pos = self._move(inp.get("coordinate"))
        mods = self._modifiers(inp)
        self._press(mods)
        try:
            for _ in range(count):
                self.kill.check()
                self.io.mouse_button(button, True)
                self.io.mouse_button(button, False)
                time.sleep(0.03)
        finally:
            self._release(mods)
        return ActionResult(_text("OK"), f"{member} at {inp.get('coordinate')} -> {pos}")

    _do_left_click = lambda self, inp: self._click("left_click", inp)      # noqa: E731
    _do_right_click = lambda self, inp: self._click("right_click", inp)    # noqa: E731
    _do_middle_click = lambda self, inp: self._click("middle_click", inp)  # noqa: E731
    _do_double_click = lambda self, inp: self._click("double_click", inp)  # noqa: E731
    _do_triple_click = lambda self, inp: self._click("triple_click", inp)  # noqa: E731

    def _do_left_click_drag(self, inp: dict[str, Any]) -> ActionResult:
        mods = self._modifiers(inp)
        self._move(inp["start_coordinate"])
        self._press(mods)
        try:
            self.io.mouse_button("left", True)
            self._held_buttons.add("left")
            self.kill.wait(0.08)
            self._move(inp["coordinate"])
            self.kill.wait(0.08)
            self.io.mouse_button("left", False)
            self._held_buttons.discard("left")
        finally:
            self._release(mods)
        return ActionResult(_text("OK"), f"Drag {inp['start_coordinate']} -> {inp['coordinate']}")

    def _do_mouse_move(self, inp: dict[str, Any]) -> ActionResult:
        self._move(inp["coordinate"])
        return ActionResult(_text("OK"), f"Move to {inp['coordinate']}")

    def _do_left_mouse_down(self, inp: dict[str, Any]) -> ActionResult:
        self.io.mouse_button("left", True)
        self._held_buttons.add("left")
        return ActionResult(_text("OK"), "Left button down")

    def _do_left_mouse_up(self, inp: dict[str, Any]) -> ActionResult:
        self.io.mouse_button("left", False)
        self._held_buttons.discard("left")
        return ActionResult(_text("OK"), "Left button up")

    def _do_cursor_position(self, inp: dict[str, Any]) -> ActionResult:
        frame = self.screen.last or self.screen.capture()
        x, y = frame.to_image(*self.io.get_cursor_pos())
        return ActionResult(_text(f"X={x}, Y={y}"), "Read cursor position")

    def _do_scroll(self, inp: dict[str, Any]) -> ActionResult:
        self._move(inp.get("coordinate"))
        direction, amount = inp["scroll_direction"], int(inp["scroll_amount"])
        clicks = amount if direction in ("up", "right") else -amount
        mods = self._modifiers(inp)
        self._press(mods)
        try:
            self.io.mouse_wheel(clicks, horizontal=direction in ("left", "right"))
        finally:
            self._release(mods)
        return ActionResult(_text("OK"), f"Scroll {direction} x{amount}")

    def _do_type(self, inp: dict[str, Any]) -> ActionResult:
        text: str = inp["text"]
        for i in range(0, len(text), 32):  # chunked so the kill switch can interrupt long strings
            self.kill.check()
            self.io.type_unicode(text[i:i + 32])
        preview = text if len(text) <= 40 else text[:37] + "..."
        return ActionResult(_text("OK"), f"Type {preview!r}")

    def _do_key(self, inp: dict[str, Any]) -> ActionResult:
        vks = parse_combo(inp["text"])
        for _ in range(int(inp.get("repeat", 1))):
            self._press(vks)
            self._release(vks)
            self.kill.wait(0.02)
        return ActionResult(_text("OK"), f"Key {inp['text']}" + (f" x{inp['repeat']}" if inp.get("repeat") else ""))

    def _do_hold_key(self, inp: dict[str, Any]) -> ActionResult:
        vks = parse_combo(inp["text"])
        self._press(vks)
        try:
            self.kill.wait(float(inp["duration"]))
        finally:
            self._release(vks)
        return ActionResult(_text("OK"), f"Hold {inp['text']} {inp['duration']}s")

    def _do_wait(self, inp: dict[str, Any]) -> ActionResult:
        self.kill.wait(float(inp["duration"]))
        return ActionResult(_text("OK"), f"Wait {inp['duration']}s")
