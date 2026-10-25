"""Backups/undo across agent requests, and settle detection before screenshots."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from retrozashi.agent.tools import NativeTools
from retrozashi.core.events import EventBus
from retrozashi.core.safety import KillSwitch
from retrozashi.desktop import screen as screen_mod
from retrozashi.desktop.executor import ActionExecutor
from retrozashi.desktop.screen import Rect, ScreenCapture, frame_difference
from retrozashi.emulators import backups
from retrozashi.emulators.base import EmulatorBusy
from retrozashi.emulators.registry import EmulatorRegistry

ORIGINAL = "[EmuCore/GS]\nRenderer = -1\nupscale_multiplier = 1\n"


@pytest.fixture
def pcsx2(tmp_path, monkeypatch):
    cfg = tmp_path / "PCSX2.ini"
    cfg.write_text(ORIGINAL)
    registry = EmulatorRegistry()
    adapter = registry.get("pcsx2")
    monkeypatch.setattr(adapter, "config_candidates", lambda: [cfg])
    monkeypatch.setattr(adapter, "is_running", lambda: False)
    return NativeTools(registry), adapter, cfg


def set_(tools, key, value):
    text, err = tools.run("set_emulator_setting", {"emulator": "pcsx2", "section": "EmuCore/GS",
                                                   "key": key, "value": value})
    assert not err, text


def test_backup_names_round_trip(tmp_path):
    f = tmp_path / "config.yml"
    f.write_text("a")
    b1, b2 = backups.backup_file(f), backups.backup_file(f)  # same second: must not collide
    assert b1 != b2 and backups.original_of(b1) == f
    assert backups.backups_for(f) == [b2, b1]


def test_undo_reverts_whole_request_then_previous_one(pcsx2):
    tools, adapter, cfg = pcsx2
    tools.begin_run()                       # request 1: "4K with Vulkan"
    set_(tools, "upscale_multiplier", "6")
    set_(tools, "Renderer", "14")
    after_first = cfg.read_text()
    tools.begin_run()                       # request 2: "actually 1440p"
    set_(tools, "upscale_multiplier", "3")
    tools.begin_run()                       # request 3: just chatted, no writes

    tools.undo_last_run()                   # skips the empty run, reverts request 2
    assert cfg.read_text() == after_first
    tools.undo_last_run()                   # reverts both writes of request 1 at once
    assert cfg.read_text() == ORIGINAL
    assert tools.undo_last_run() == []


def test_restore_tool_and_guards(pcsx2, monkeypatch):
    tools, adapter, cfg = pcsx2
    set_(tools, "upscale_multiplier", "6")
    text, err = tools.run("list_config_backups", {"emulator": "pcsx2"})
    assert not err and "PCSX2.ini" in text
    text, err = tools.run("restore_config_backup", {"emulator": "pcsx2", "backup": "C:/Windows/win.ini"})
    assert err and "not a PCSX2 config backup" in text   # only its own backups
    text, err = tools.run("restore_config_backup", {"emulator": "pcsx2", "backup": ""})
    assert not err and cfg.read_text() == ORIGINAL
    monkeypatch.setattr(adapter, "is_running", lambda: True)
    with pytest.raises(EmulatorBusy):
        adapter.restore_backup()


def test_frame_difference():
    assert frame_difference(b"\x00" * 100, b"\x00" * 100) == 0.0
    assert frame_difference(b"\x00" * 100, b"\xff" * 100) == 1.0
    assert frame_difference(b"a", b"ab") == 1.0


def make_capture(monkeypatch, thumbs):
    seq = iter(thumbs)
    monkeypatch.setattr(ScreenCapture, "target_rect", lambda self: Rect(0, 0, 160, 90))
    monkeypatch.setattr(ScreenCapture, "_thumbnail", lambda self, rect: next(seq))
    monkeypatch.setattr(ScreenCapture, "_grab",
                        lambda self, rect, m: screen_mod.Frame(b"png", 160, 90, rect, 1.0))
    return ScreenCapture(1)


def test_wait_until_stable(monkeypatch):
    sleeps = []
    sc = make_capture(monkeypatch, [b"\x00" * 50, b"\xff" * 50, b"\xff" * 50])  # changes once, then settles
    assert sc.wait_until_stable(sleeps.append, timeout=5) is True
    assert len(sleeps) == 2


def test_screenshot_after_action_waits_and_flags_unsettled(monkeypatch):
    frames = [bytes([i % 2 * 255]) * 50 for i in range(1000)]  # flickers forever, like a running game
    sc = make_capture(monkeypatch, frames)
    backend = SimpleNamespace(set_cursor_pos=lambda x, y: None, mouse_button=lambda b, d: None)
    ex = ActionExecutor(sc, KillSwitch(EventBus()), backend=backend, action_delay=0, settle_timeout=0.05)

    first = ex.execute("screenshot", {})            # nothing happened yet: no settle wait, no note
    assert [b["type"] for b in first.content] == ["image"]
    ex.execute("left_click", {"coordinate": [5, 5]})
    shot = ex.execute("screenshot", {})
    assert [b["type"] for b in shot.content] == ["text", "image"]
    assert "still changing" in shot.content[0]["text"]
