"""Profiles: store CRUD, apply-on-launch, revert-on-exit (keeping in-game changes), crash recovery."""
from __future__ import annotations

import json
import threading

import pytest
import yaml

from retrozashi.agent.tools import NativeTools
from retrozashi.core.events import EventBus, EventKind
from retrozashi.emulators.base import EmulatorBusy
from retrozashi.emulators.ini_store import IniDocument
from retrozashi.emulators.registry import EmulatorRegistry
from retrozashi.profiles import Profile, ProfileManager, Setting

PCSX2_INI = "[EmuCore/GS]\nRenderer = -1\nupscale_multiplier = 1\n\n[UI]\nStartFullscreen = false\n"


class FakeEmulator:
    """Stands in for the emulator process: launch() records the call, exit() lets the watcher continue."""
    def __init__(self, adapter):
        self.adapter = adapter
        self.running = False
        self.launches = []
        self.exited = threading.Event()
        self.reverted = threading.Event()
        adapter.is_running = lambda: self.running
        adapter.launch = self.launch

    def launch(self, game, fullscreen):
        self.launches.append((game, fullscreen))
        self.running = True
        self.exited.clear()
        return 4321

    def exit(self):
        self.running = False
        self.exited.set()

    def wait(self, adapter, pid):
        self.exited.wait(timeout=5)


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "PCSX2.ini"
    cfg.write_text(PCSX2_INI)
    registry = EmulatorRegistry()
    adapter = registry.get("pcsx2")
    monkeypatch.setattr(adapter, "config_candidates", lambda: [cfg])
    emu = FakeEmulator(adapter)
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    bus.subscribe(lambda e: "restored" in e.message and emu.reverted.set())

    def make_manager(wait=emu.wait):
        return ProfileManager(registry, bus, tmp_path / "profiles.json", tmp_path / "pending.json", wait_for_exit=wait)

    manager = make_manager()
    manager.save(Profile("GT4", "pcsx2", game_path=r"D:\ps2\gt4.iso", settings=[
        Setting("EmuCore/GS", "upscale_multiplier", "6"),
        Setting("EmuCore/GS", "Renderer", "14"),
        Setting("EmuCore/GS", "VsyncEnable", "true"),   # absent before: must be removed on revert
    ]))
    return manager, make_manager, emu, cfg, events, tmp_path


def gs(cfg):
    return IniDocument.load(cfg).section("EmuCore/GS")


def test_store_crud_and_persistence(env):
    manager, make_manager, *_ = env
    manager.save(Profile("Crash Bandicoot", "duckstation"))
    assert [p.name for p in manager.list()] == ["Crash Bandicoot", "GT4"]
    with pytest.raises(ValueError, match="already exists"):
        manager.save(Profile("gt4", "pcsx2"))  # case-insensitive clash
    with pytest.raises(KeyError):
        manager.save(Profile("X", "not-an-emulator"))
    renamed = manager.get("GT4")
    renamed.name = "Gran Turismo 4"
    manager.save(renamed, replacing="GT4")             # rename keeps the profile
    manager.delete("crash bandicoot")
    reloaded = make_manager()
    assert [p.name for p in reloaded.list()] == ["Gran Turismo 4"]
    assert reloaded.get("gran turismo 4").settings[0] == Setting("EmuCore/GS", "upscale_multiplier", "6")


def test_launch_applies_then_reverts_on_exit_keeping_in_game_changes(env):
    manager, _, emu, cfg, events, _ = env
    result = manager.launch("gt4")
    assert emu.launches == [(r"D:\ps2\gt4.iso", True)]
    assert gs(cfg) == {"Renderer": "14", "upscale_multiplier": "6", "VsyncEnable": "true"}
    assert manager.active() == {"pcsx2": "GT4"} and result["pid"] == 4321

    # During play the user changes something else in PCSX2's own menus (it saves on exit).
    doc = IniDocument.load(cfg)
    doc.set("UI", "StartFullscreen", "true")
    doc.save(cfg)

    emu.exit()
    assert emu.reverted.wait(timeout=5)
    assert gs(cfg) == {"Renderer": "-1", "upscale_multiplier": "1"}           # profile keys restored/removed
    assert IniDocument.load(cfg).get("UI", "StartFullscreen") == "true"       # in-game change kept
    assert manager.active() == {}
    assert any("previous settings restored" in e.message for e in events if e.kind == EventKind.TOOL)


def test_refuses_when_already_running_and_reverts_if_launch_fails(env):
    manager, _, emu, cfg, _, _ = env
    emu.running = True
    with pytest.raises(EmulatorBusy):
        manager.launch("GT4")
    emu.running = False

    def broken_launch(game, fullscreen):
        raise FileNotFoundError("PCSX2 executable not found")

    emu.adapter.launch = broken_launch
    with pytest.raises(FileNotFoundError):
        manager.launch("GT4")
    assert gs(cfg) == {"Renderer": "-1", "upscale_multiplier": "1"} and manager.active() == {}


def test_crash_recovery_on_next_start(env):
    manager, make_manager, emu, cfg, _, tmp_path = env
    never = threading.Event()
    stuck = make_manager(wait=lambda a, pid: never.wait())  # Retrozashi "dies" before the game ends
    stuck.launch("GT4")
    assert json.loads((tmp_path / "pending.json").read_text())["pcsx2"]["profile"] == "GT4"

    emu.running = False                                     # game closed while Retrozashi was down
    restarted = make_manager()
    restarted.recover()
    assert gs(cfg) == {"Renderer": "-1", "upscale_multiplier": "1"} and restarted.active() == {}


def test_yaml_and_eden_reverts(tmp_path, monkeypatch):
    registry = EmulatorRegistry()
    rpcs3, eden = registry.get("rpcs3"), registry.get("eden")
    yml = tmp_path / "config.yml"
    yml.write_text(yaml.safe_dump({"Video": {"Renderer": "Vulkan", "Resolution Scale": 100, "VSync": False}}))
    qt = tmp_path / "qt-config.ini"
    qt.write_text("[Renderer]\nresolution_setup\\default=true\nresolution_setup=2\n")
    for adapter, path in ((rpcs3, yml), (eden, qt)):
        monkeypatch.setattr(adapter, "config_candidates", lambda p=path: [p])
        monkeypatch.setattr(adapter, "is_running", lambda: False)

    changes = rpcs3.apply_settings([("Video", "Resolution Scale", "300"), ("Video", "VSync", "true")])
    assert yaml.safe_load(yml.read_text())["Video"] == {"Renderer": "Vulkan", "Resolution Scale": 300, "VSync": True}
    rpcs3.revert_changes(changes)
    assert yaml.safe_load(yml.read_text())["Video"] == {"Renderer": "Vulkan", "Resolution Scale": 100, "VSync": False}

    changes = eden.apply_settings([("Renderer", "resolution_setup", "5")])
    assert IniDocument.load(qt).section("Renderer") == {"resolution_setup\\default": "false", "resolution_setup": "5"}
    eden.revert_changes(changes)
    assert IniDocument.load(qt).section("Renderer") == {"resolution_setup\\default": "true", "resolution_setup": "2"}


def test_agent_tools(env):
    manager, _, emu, cfg, _, _ = env
    tools = NativeTools(EmulatorRegistry(), profiles=manager)
    text, err = tools.run("list_profiles", {})
    assert not err and "GT4" in text and "upscale_multiplier = 6" in text
    text, err = tools.run("launch_profile", {"name": "nope"})
    assert err and "No profile named" in text
    text, err = tools.run("launch_profile", {"name": "gt4"})
    assert not err and emu.launches
    emu.exit()
    assert emu.reverted.wait(timeout=5)
