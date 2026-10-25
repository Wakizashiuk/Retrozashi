"""Eden end to end on a realistic portable layout: any-section edits, search, per-game overrides,
learn-by-diff, guides, game/title-ID listing, exe search, and the close confirmation gate."""
from __future__ import annotations

import json

import pytest

from retrozashi.agent.tools import NativeTools
from retrozashi.emulators import inspect
from retrozashi.emulators.ini_store import IniDocument
from retrozashi.emulators.registry import EmulatorRegistry
from retrozashi.knowledge import LearnedSettings

QT_CONFIG = r"""[Controls]
player_0_type\default=true
player_0_type=0
player_0_connected\default=true
player_0_connected=true
player_0_button_a\default=false
player_0_button_a="engine:sdl,port:0,guid:030000004c050000e60c000000016800,button:1"
player_0_button_b\default=false
player_0_button_b="engine:sdl,port:0,guid:030000004c050000e60c000000016800,button:0"
vibration_enabled\default=true
vibration_enabled=true

[Renderer]
backend\default=true
backend=1
resolution_setup\default=true
resolution_setup=2
scaling_filter\default=true
scaling_filter=1

[System]
use_docked_mode\default=true
use_docked_mode=true

[UI]
Paths\gamedirs\size=1
Paths\gamedirs\1\path={games}
Paths\gamedirs\1\deep_scan=false
"""

MK8 = "0100152000022000"


@pytest.fixture
def eden(tmp_path, monkeypatch):
    root = tmp_path / "Eden"
    cfg_dir = root / "user" / "config"
    (cfg_dir / "input").mkdir(parents=True)
    games = tmp_path / "Switch Games"
    (games / "Racing").mkdir(parents=True)
    (games / "Racing" / f"Mario Kart 8 Deluxe [{MK8}][v0].nsp").write_bytes(b"")
    (games / "Zelda TotK.xci").write_bytes(b"")
    (games / "readme.txt").write_text("not a game")
    (root / "eden.exe").write_bytes(b"")
    cfg = cfg_dir / "qt-config.ini"
    cfg.write_text(QT_CONFIG.format(games=str(games).replace("\\", "/")))
    (cfg_dir / "input" / "DualSense.ini").write_text("[Controls]\nplayer_0_button_a=\"engine:sdl,button:1\"\n")

    registry = EmulatorRegistry({"eden": str(root / "eden.exe")}, remembered_paths=tmp_path / "paths.json")
    adapter = registry.get("eden")
    running = {"value": False}
    monkeypatch.setattr(type(adapter), "is_running", lambda self: running["value"])
    asked = []
    tools = NativeTools(registry, learned=LearnedSettings(tmp_path / "learned.json"),
                        confirm=lambda q: asked.append(q) or False)
    return tools, adapter, cfg, cfg_dir, running, asked, tmp_path


def run(tools, name, **inp):
    text, err = tools.run(name, inp)
    assert not err, text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def test_finds_portable_config_and_edits_any_section(eden):
    tools, adapter, cfg, *_ = eden
    assert adapter.config_path() == cfg
    run(tools, "set_emulator_setting", emulator="eden", section="Renderer", key="resolution_setup",
        value="5", game_id="")
    run(tools, "set_emulator_setting", emulator="eden", section="Controls", key="player_0_button_x",
        value='"engine:sdl,port:0,guid:030000004c050000e60c000000016800,button:3"', game_id="")
    doc = IniDocument.load(cfg)
    assert doc.get("Renderer", "resolution_setup") == "5"
    assert doc.get("Renderer", "resolution_setup\\default") == "false"    # Eden ignores it otherwise
    assert doc.get("Controls", "player_0_button_x").endswith('button:3"')
    assert doc.get("UI", "Paths\\gamedirs\\size") == "1"                   # untouched sections intact


def test_search_across_all_sections_and_profiles(eden):
    tools, *_ = eden
    hits = run(tools, "read_emulator_config", emulator="eden", section="", search="player_0 button", game_id="")
    keys = {(h["file"], h["key"]) for h in hits}
    assert ("qt-config.ini", "player_0_button_a") in keys and ("qt-config.ini", "player_0_button_b") in keys
    assert ("DualSense.ini", "player_0_button_a") in keys                  # saved input profile searched too
    res = run(tools, "read_emulator_config", emulator="eden", section="", search="resolution", game_id="")
    assert [h["value"] for h in res if h["key"] == "resolution_setup"] == ["2"]


def test_per_game_overrides(eden):
    tools, adapter, cfg, cfg_dir, *_ = eden
    assert "No overrides" in run(tools, "read_emulator_config", emulator="eden", section="", search="", game_id=MK8)
    run(tools, "set_emulator_setting", emulator="eden", section="Renderer", key="resolution_setup",
        value="5", game_id=MK8.lower())
    custom = IniDocument.load(cfg_dir / "custom" / f"{MK8}.ini")
    assert custom.section("Renderer") == {"resolution_setup": "5", "resolution_setup\\use_global": "false",
                                          "resolution_setup\\default": "false"}
    assert IniDocument.load(cfg).get("Renderer", "resolution_setup") == "2"  # global untouched
    run(tools, "set_emulator_setting", emulator="eden", section="Renderer", key="resolution_setup",
        value="global", game_id=MK8)
    custom = IniDocument.load(cfg_dir / "custom" / f"{MK8}.ini")
    assert custom.section("Renderer") == {"resolution_setup\\use_global": "true"}
    text, err = tools.run("set_emulator_setting", {"emulator": "eden", "section": "Renderer",
                                                   "key": "x", "value": "1", "game_id": "mario kart"})
    assert err and "title ID" in text


def test_learn_by_diff_then_remember(eden):
    tools, adapter, cfg, cfg_dir, *_ = eden
    run(tools, "snapshot_config", emulator="eden", game_id="")
    # The user (or Claude via UI Automation) picks "3x" in Configure -> Graphics and presses OK:
    doc = IniDocument.load(cfg)
    doc.set("Renderer", "resolution_setup", "5")
    doc.set("Renderer", "resolution_setup\\default", "false")
    doc.save(cfg)
    (cfg_dir / "input" / "DualSense.ini").write_text("[Controls]\nplayer_0_button_a=\"engine:sdl,button:2\"\n")
    changes = run(tools, "diff_config", emulator="eden", game_id="")["changes"]
    by_key = {(c["file"], c["key"]): (c["before"], c["after"]) for c in changes}
    assert by_key[("qt-config.ini", "resolution_setup")] == ("2", "5")
    assert by_key[("DualSense.ini", "player_0_button_a")] == ('"engine:sdl,button:1"', '"engine:sdl,button:2"')

    run(tools, "remember_setting", emulator="eden", label="Resolution 3x", section="Renderer",
        key="resolution_setup", value="5", meaning="Graphics > Resolution")
    eden_info = next(e for e in run(tools, "list_emulators") if e["id"] == "eden")
    assert eden_info["learned_settings"]["Resolution 3x"]["value"] == "5"
    assert set(eden_info["guides"]) == {"controllers", "upscaling", "per_game"}


def test_guides_and_game_list(eden):
    tools, *_ = eden
    guide = run(tools, "emulator_guide", emulator="eden", topic="Controllers")
    assert "Input Device" in guide and "Steam Input" in guide and "8BitDo" in guide
    games = run(tools, "list_games", emulator="eden", query="")
    assert {g["id"] for g in games} == {MK8, None} and len(games) == 2   # readme.txt ignored
    assert run(tools, "list_games", emulator="eden", query="mario")[0]["id"] == MK8


def test_close_needs_user_confirmation(eden):
    tools, adapter, cfg, cfg_dir, running, asked, _ = eden
    closed = []
    adapter.close = lambda: closed.append(True)
    assert "isn't running" in run(tools, "close_emulator", emulator="eden")
    running["value"] = True
    text, err = tools.run("close_emulator", {"emulator": "eden"})
    assert err and "chose not to close" in text and asked and not closed
    tools.confirm = lambda q: True
    assert run(tools, "close_emulator", emulator="eden") == "Closed." and closed


def test_locate_remembers_exe(eden, monkeypatch, tmp_path):
    tools, *_ = eden
    elsewhere = tmp_path / "Downloads" / "eden-windows" / "Eden"
    elsewhere.mkdir(parents=True)
    (elsewhere / "eden.exe").write_bytes(b"")
    monkeypatch.setattr(inspect, "default_search_roots", lambda: [tmp_path / "Downloads"])
    out = run(tools, "locate_emulator", emulator="eden")
    assert out["using"].endswith("eden.exe")
    assert json.loads((tmp_path / "paths.json").read_text())["eden"] == out["using"]
    assert EmulatorRegistry(remembered_paths=tmp_path / "paths.json").get("eden").find_exe() == elsewhere / "eden.exe"
