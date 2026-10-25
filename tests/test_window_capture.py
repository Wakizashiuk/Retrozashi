"""Window-targeted capture: rect clipping, coordinate confinement, stale-frame guards, focus tool."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from retrozashi.agent.tools import NativeTools
from retrozashi.core.events import EventBus
from retrozashi.core.safety import KillSwitch
from retrozashi.desktop import screen as screen_mod
from retrozashi.desktop.executor import ActionError, ActionExecutor
from retrozashi.desktop.screen import CaptureTargetLost, Rect, ScreenCapture, WindowTarget, clip
from retrozashi.emulators.registry import EmulatorRegistry

# Primary 2560x1440 at origin, 4K monitor to its left.
VIRTUAL = Rect(-3840, 0, 6400, 2160)
MONITORS = [VIRTUAL, Rect(0, 0, 2560, 1440), Rect(-3840, 0, 3840, 2160)]


def test_clip_to_virtual_desktop():
    assert clip(Rect(-4000, 100, 1000, 500), VIRTUAL) == Rect(-3840, 100, 840, 500)
    with pytest.raises(CaptureTargetLost):
        clip(Rect(9000, 0, 100, 100), VIRTUAL)


@pytest.fixture
def cap(monkeypatch):
    rects = {111: Rect(-3000, 200, 1600, 900)}
    monkeypatch.setattr(ScreenCapture, "list_monitors", staticmethod(lambda: MONITORS))

    def fake_grab(self, rect, max_long_edge):
        s = screen_mod.compute_scale(rect.width, rect.height, max_long_edge)
        return screen_mod.Frame(b"png", int(rect.width * s), int(rect.height * s), rect, s)

    monkeypatch.setattr(ScreenCapture, "_grab", fake_grab)
    return ScreenCapture(1, window_rect_fn=lambda h: rects.get(h)), rects


def test_window_capture_confines_clicks_to_window(cap):
    sc, _ = cap
    sc.set_window(WindowTarget(111, "PCSX2 Settings", "pcsx2"))
    frame = sc.capture()
    assert frame.source == Rect(-3000, 200, 1600, 900) and frame.scale == 1.0  # small window: full detail
    assert frame.to_screen(10, 10) == (-2990, 210)
    assert frame.to_screen(99999, 99999) == (-3000 + 1599, 200 + 899)  # can't escape the window
    assert sc.describe_target() == "window 'PCSX2 Settings'"


def make_exec(sc):
    calls = []
    backend = SimpleNamespace(set_cursor_pos=lambda x, y: calls.append((x, y)),
                              mouse_button=lambda b, d: None)
    return ActionExecutor(sc, KillSwitch(EventBus()), backend=backend, action_delay=0), calls


def test_executor_requires_fresh_screenshot_and_detects_moves(cap):
    sc, rects = cap
    ex, calls = make_exec(sc)
    sc.set_window(WindowTarget(111, "PCSX2", "pcsx2"))
    with pytest.raises(ActionError, match="take a screenshot first"):
        ex.execute("left_click", {"coordinate": [5, 5]})
    ex.execute("screenshot", {})
    ex.execute("left_click", {"coordinate": [5, 5]})
    assert calls == [(-2995, 205)]
    rects[111] = Rect(0, 0, 1600, 900)  # user dragged the window to another monitor
    with pytest.raises(ActionError, match="moved or resized"):
        ex.execute("left_click", {"coordinate": [5, 5]})


def test_closed_window_falls_back_to_monitor(cap):
    sc, rects = cap
    ex, _ = make_exec(sc)
    sc.set_window(WindowTarget(111, "PCSX2", "pcsx2"))
    ex.execute("screenshot", {})
    del rects[111]
    with pytest.raises(ActionError, match="closed or minimized"):
        ex.execute("left_click", {"coordinate": [5, 5]})
    assert sc.window is None
    result = ex.execute("screenshot", {})
    assert result.content[0]["type"] == "image" and sc.last.source == MONITORS[1]


def test_focus_tool(cap, monkeypatch):
    sc, _ = cap
    registry = EmulatorRegistry()
    monkeypatch.setattr(registry.get("pcsx2"), "running_processes", lambda: [SimpleNamespace(pid=7)])
    focused = []
    api = (lambda pids: [(111, "PCSX2 v2.2"), (222, "PCSX2 Settings")] if 7 in pids else [],
           lambda h: focused.append(h) or True)
    tools = NativeTools(registry, screen=sc, window_api=api)

    text, err = tools.run("focus_emulator_window", {"emulator": "pcsx2", "window_title_contains": "settings"})
    assert not err and focused == [222] and sc.window.hwnd == 222 and sc.last is None
    text, err = tools.run("focus_emulator_window", {"emulator": "pcsx2", "window_title_contains": "nope"})
    assert err and "PCSX2 Settings" in text  # lists what's open
    text, err = tools.run("focus_emulator_window", {"emulator": "", "window_title_contains": ""})
    assert not err and sc.window is None
