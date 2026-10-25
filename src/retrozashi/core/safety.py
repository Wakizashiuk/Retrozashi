"""The kill switch. One threading.Event that every layer checks before touching input.

Defence in depth:
  1. Global hotkey (RegisterHotKey), which fires even while a fullscreen emulator has focus.
  2. The executor calls `check()` before every single SendInput, and between batched actions.
  3. The agent loop checks while streaming, so a halt also aborts an in-flight API response.
  4. On trip, cleanup hooks release any held keys or mouse buttons so nothing stays stuck down.
"""
from __future__ import annotations

import threading
from typing import Callable

from .events import EventBus, EventKind


class AgentHalted(Exception):
    """Raised inside the agent/executor when the kill switch has been tripped."""


class KillSwitch:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._halted = threading.Event()
        self._cleanup: list[Callable[[], None]] = []
        self._hotkey = None

    @property
    def halted(self) -> bool:
        return self._halted.is_set()

    def on_trip(self, fn: Callable[[], None]) -> None:
        self._cleanup.append(fn)

    def trip(self, reason: str = "Kill switch pressed") -> None:
        if self._halted.is_set():
            return
        self._halted.set()
        for fn in self._cleanup:
            try:
                fn()
            except Exception:
                pass
        self._bus.publish(EventKind.HALTED, reason)

    def reset(self) -> None:
        self._halted.clear()

    def check(self) -> None:
        if self._halted.is_set():
            raise AgentHalted()

    def wait(self, seconds: float) -> None:
        """Interruptible sleep: returns early (and raises) if the switch trips."""
        if self._halted.wait(timeout=seconds):
            raise AgentHalted()

    def arm_hotkey(self, combo: str) -> None:
        from ..desktop.keymap import parse_hotkey
        from ..desktop.win32 import IS_WINDOWS, GlobalHotkey

        if not IS_WINDOWS:
            return
        mods, vk = parse_hotkey(combo)
        self._hotkey = GlobalHotkey(mods, vk, lambda: self.trip(f"Hotkey {combo} pressed"))
        self._hotkey.start()

    def disarm(self) -> None:
        if self._hotkey:
            self._hotkey.stop()
