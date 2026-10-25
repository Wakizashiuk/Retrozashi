"""RetroArch on a realistic install: flat quoted cfg, ':\\' relative dirs, core info, JSON playlists,
core/game overrides, core options (.opt) and remaps, search across all of them, launch with -L core."""
from __future__ import annotations

import json

import pytest

from retrozashi.agent.tools import NativeTools
from retrozashi.emulators.base import EmulatorAdapter
from retrozashi.emulators.cfg_store import CfgDocument
from retrozashi.emulators.registry import EmulatorRegistry
from retrozashi.knowledge import LearnedSettings

CORE = "Beetle PSX HW"


@pytest.fixture
def ra(tmp_path, monkeypatch):
    monkeypatch.setattr(EmulatorAdapter, "is_running", lambda self: False)
    root = tmp_path / "RetroArch-Win64"
    for d in ("config", "info", "playlists", "cores", "autoconfig/sdl2"):
        (root / d).mkdir(parents=True)
    (root / "retroarch.exe").write_bytes(b"")
    (root / "retroarch.cfg").write_text(
        '# RetroArch config\n'
        'video_driver = "glcore"\n'
        'video_scale_integer = "false"\n'
        'input_joypad_driver = "dinput"\n'
        'rgui_config_directory = ":\\config"\n'
        'playlist_directory = ":\\playlists"\n'
        'libretro_info_path = ":\\info"\n'
        'input_remapping_directory = "default"\n'
        'global_core_options = "false"\n', newline="\r\n")
    (root / "info" / "mednafen_psx_hw_libretro.info").write_text(
        f'display_name = "Sony - PlayStation (Beetle PSX HW)"\ncorename = "{CORE}"\n')
    (root / "info" / "snes9x_libretro.info").write_text('corename = "Snes9x"\n')
    (root / "playlists" / "Sony - PlayStation.lpl").write_text(json.dumps({
        "version": "1.5", "default_core_path": "", "items": [
            {"path": "D:\\ROMs\\PS1\\Crash Bandicoot (USA).chd", "label": "Crash Bandicoot (USA)",
             "core_path": ":\\cores\\mednafen_psx_hw_libretro.dll", "core_name": "Sony - PlayStation (Beetle PSX HW)"},
            {"path": "D:\\ROMs\\PS1\\Ape Escape (USA).chd", "label": "Ape Escape (USA)",
             "core_path": "DETECT", "core_name": "DETECT"}]}))
    (root / "playlists" / "Nintendo - SNES.lpl").write_text(json.dumps({
        "version": "1.5", "default_core_path": "C:\\RetroArch-Win64\\cores\\snes9x_libretro.dll", "items": [
            {"path": "D:\\ROMs\\SNES\\Super Metroid (USA).zip#Super Metroid (USA).sfc", "label": "Super Metroid (USA)",
             "core_path": "DETECT"}]}))
    (root / "config" / CORE).mkdir()
    (root / "config" / CORE / f"{CORE}.opt").write_text(
        'beetle_psx_hw_internal_resolution = "1x(native)"\nbeetle_psx_hw_pgxp_mode = "disabled"\n')
    (root / "autoconfig" / "sdl2" / "DualSense Wireless Controller.cfg").write_text('input_device = "DualSense Wireless Controller"\n')

    registry = EmulatorRegistry({"retroarch": str(root / "retroarch.exe")})
    return NativeTools(registry, learned=LearnedSettings(None)), registry.get("retroarch"), root


def run(tools, name, **inp):
    text, err = tools.run(name, inp)
    assert not err, text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def test_global_settings_keep_retroarch_format(ra):
    tools, adapter, root = ra
    run(tools, "set_emulator_setting", emulator="retroarch", section="", key="video_driver", value="vulkan", game_id="")
    run(tools, "set_emulator_setting", emulator="retroarch", section="", key="input_joypad_driver", value='"sdl2"', game_id="")
    raw = (root / "retroarch.cfg").read_bytes().decode()
    assert 'video_driver = "vulkan"\r\n' in raw and 'input_joypad_driver = "sdl2"\r\n' in raw   # quoted, CRLF kept
    assert raw.startswith("# RetroArch config")
    info = next(e for e in run(tools, "list_emulators") if e["id"] == "retroarch")
    assert info["per_game"] and set(info["guides"]) == {"controllers", "upscaling", "per_game"}


def test_list_games_resolves_cores_and_ids(ra):
    tools, *_ = ra
    games = {g["label"]: g for g in run(tools, "list_games", emulator="retroarch", query="")}
    assert games["Crash Bandicoot (USA)"]["id"] == f"{CORE}/Crash Bandicoot (USA)"
    assert games["Crash Bandicoot (USA)"]["options_id"] == f"{CORE}/Crash Bandicoot (USA) [options]"
    assert games["Ape Escape (USA)"]["core"] is None and "id" not in games["Ape Escape (USA)"]   # DETECT
    assert games["Super Metroid (USA)"]["id"] == "Snes9x/Super Metroid (USA)"   # playlist default core, zip name


def test_overrides_options_and_remaps(ra):
    tools, adapter, root = ra
    game = f"{CORE}/Crash Bandicoot (USA)"
    # 3D upscaling = core option, for this game only:
    run(tools, "set_emulator_setting", emulator="retroarch", section="", key="beetle_psx_hw_internal_resolution",
        value="4x", game_id=f"{game} [options]")
    opt = root / "config" / CORE / "Crash Bandicoot (USA).opt"
    assert CfgDocument.load(opt).items() == {"beetle_psx_hw_internal_resolution": "4x"}
    # ...and for every game on the core:
    run(tools, "set_emulator_setting", emulator="retroarch", section="", key="beetle_psx_hw_pgxp_mode",
        value="memory only", game_id=f"{CORE} [options]")
    assert CfgDocument.load(root / "config" / CORE / f"{CORE}.opt").get("beetle_psx_hw_pgxp_mode") == "memory only"
    # Settings override for one game, and a remap:
    run(tools, "set_emulator_setting", emulator="retroarch", section="", key="video_scale_integer", value="true", game_id=game)
    assert CfgDocument.load(root / "config" / CORE / "Crash Bandicoot (USA).cfg").items() == {"video_scale_integer": "true"}
    run(tools, "set_emulator_setting", emulator="retroarch", section="", key="input_player1_btn_a", value="8",
        game_id=f"{game} [remap]")
    assert (root / "config" / "remaps" / CORE / "Crash Bandicoot (USA).rmp").exists()
    assert CfgDocument.load(root / "retroarch.cfg").get("video_scale_integer") == "false"   # global untouched
    # Bad scopes are explained:
    text, err = tools.run("set_emulator_setting", {"emulator": "retroarch", "section": "", "key": "x", "value": "1",
                                                   "game_id": "a/b/c"})
    assert err and "[options]" in text


def test_search_and_diff_cover_every_file(ra):
    tools, adapter, root = ra
    hits = run(tools, "read_emulator_config", emulator="retroarch", section="", search="internal_resolution", game_id="")
    assert hits == [{"file": f"{CORE}.opt", "section": "", "key": "beetle_psx_hw_internal_resolution", "value": "1x(native)"}]
    run(tools, "snapshot_config", emulator="retroarch", game_id=f"{CORE} [options]")
    doc = CfgDocument.load(root / "config" / CORE / f"{CORE}.opt")
    doc.set("beetle_psx_hw_internal_resolution", "8x")       # "changed in Quick Menu -> Core Options"
    doc.save(root / "config" / CORE / f"{CORE}.opt")
    changes = run(tools, "diff_config", emulator="retroarch", game_id=f"{CORE} [options]")["changes"]
    assert changes == [{"file": f"{CORE}.opt", "section": "", "key": "beetle_psx_hw_internal_resolution",
                        "before": "1x(native)", "after": "8x"}]


def test_undo_and_profile_revert_restore_quotes(ra):
    tools, adapter, root = ra
    changes = adapter.apply_settings([("", "video_driver", "vulkan"), ("", "video_fullscreen", "true")])
    adapter.revert_changes(changes)
    doc = CfgDocument.load(root / "retroarch.cfg")
    assert doc.get("video_driver") == "glcore" and doc.get("video_fullscreen") is None
    assert 'video_driver = "glcore"' in (root / "retroarch.cfg").read_text()


def test_launch_uses_the_playlist_core(ra):
    tools, adapter, root = ra
    args = adapter.launch_args("D:\\ROMs\\PS1\\Crash Bandicoot (USA).chd", fullscreen=True)
    assert args[0] == "-f" and args[1] == "-L"
    assert args[2].replace("\\", "/").endswith("RetroArch-Win64/cores/mednafen_psx_hw_libretro.dll")
    assert adapter.launch_args("D:\\ROMs\\unknown.chd", fullscreen=False) == ["D:\\ROMs\\unknown.chd"]
