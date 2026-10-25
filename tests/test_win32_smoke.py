"""Real Win32 calls (skipped off Windows). These run on the CI windows-latest runner and catch
ctypes signature/struct mistakes that the fake-backend tests can't."""
from __future__ import annotations

import ctypes
import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_input_struct_size_matches_win32():
    from retrozashi.desktop.win32 import INPUT

    # sizeof(INPUT) is 40 on 64-bit Windows, 28 on 32-bit; SendInput rejects anything else.
    assert ctypes.sizeof(INPUT) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)


def test_dpi_awareness_and_cursor_round_trip():
    from retrozashi.desktop import win32

    win32.enable_dpi_awareness()
    x, y = win32.get_cursor_pos()
    win32.set_cursor_pos(x, y)
    assert win32.get_cursor_pos() == (x, y)


def test_window_helpers_handle_missing_windows():
    from retrozashi.desktop import win32

    assert win32.window_rect(0) is None
    assert isinstance(win32.list_windows({os.getpid()}), list)


def test_monitors_and_capture():
    from retrozashi.desktop.screen import API_MAX_LONG_EDGE, ScreenCapture

    monitors = ScreenCapture.list_monitors()
    assert len(monitors) >= 2  # [virtual desktop, primary, ...]
    frame = ScreenCapture(1).capture()
    assert frame.png.startswith(b"\x89PNG") and max(frame.width, frame.height) <= API_MAX_LONG_EDGE


def test_uia_backend_imports():
    from retrozashi.desktop.uia import PywinautoBackend, uia_available

    assert uia_available()
    PywinautoBackend()
