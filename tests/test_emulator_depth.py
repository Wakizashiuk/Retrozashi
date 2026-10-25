"""PCSX2, DuckStation, Dolphin and RPCS3 at Eden's depth: per-game overrides, game IDs read from real
disc structures, input profiles in search/diff, guides. Disc images are built here byte by byte."""
from __future__ import annotations

import json

import pytest
import yaml

from retrozashi.agent.tools import NativeTools
from retrozashi.emulators.base import EmulatorAdapter
from retrozashi.emulators.discs import normalize_ps_serial, read_gc_wii_id, read_ps_serial
from retrozashi.emulators.ini_store import IniDocument
from retrozashi.emulators.registry import EmulatorRegistry
from retrozashi.knowledge import LearnedSettings


# ---- disc image builders ---------------------------------------------------------------------
def _dir_record(name: bytes, lba: int, size: int, is_dir: bool = False) -> bytes:
    length = 33 + len(name) + (len(name) + 1) % 2  # pad to even length
    rec = bytearray(length)
    rec[0] = length
    rec[2:6], rec[6:10] = lba.to_bytes(4, "little"), lba.to_bytes(4, "big")
    rec[10:14], rec[14:18] = size.to_bytes(4, "little"), size.to_bytes(4, "big")
    rec[25] = 2 if is_dir else 0
    rec[32] = len(name)
    rec[33:33 + len(name)] = name
    return bytes(rec)


def iso_with_system_cnf(cnf: str) -> list[bytes]:
    """2048-byte user-data sectors of a minimal ISO9660 volume containing SYSTEM.CNF."""
    sectors = [bytes(2048)] * 21
    pvd = bytearray(2048)
    pvd[0], pvd[1:6], pvd[6] = 1, b"CD001", 1
    pvd[156:190] = _dir_record(b"\x00", 18, 2048, is_dir=True)
    sectors[16] = bytes(pvd)
    root = (_dir_record(b"\x00", 18, 2048, True) + _dir_record(b"\x01", 18, 2048, True)
            + _dir_record(b"SYSTEM.CNF;1", 20, len(cnf)))
    sectors[18] = root.ljust(2048, b"\x00")
    sectors[20] = cnf.encode().ljust(2048, b"\x00")
    return sectors


def write_iso(path, sectors):
    path.write_bytes(b"".join(sectors))


def write_raw_mode2(path, sectors):
    """Raw 2352-byte Mode 2 Form 1 sectors, as in a PS1 .bin dump."""
    sync = b"\x00" + b"\xff" * 10 + b"\x00"
    path.write_bytes(b"".join(sync + b"\x00\x02\x00\x02" + bytes(8) + s + bytes(280) for s in sectors))


def test_disc_readers(tmp_path):
    ps2 = tmp_path / "Gran Turismo 4.iso"
    write_iso(ps2, iso_with_system_cnf("BOOT2 = cdrom0:\\SCUS_973.28;1\r\nVER = 1.00\r\nVMODE = NTSC\r\n"))
    assert read_ps_serial(ps2) == "SCUS-97328"

    bin_ = tmp_path / "Crash (Track 1).bin"
    write_raw_mode2(bin_, iso_with_system_cnf("BOOT = cdrom:\\SCUS_949.00;1\nTCB = 4\n"))
    cue = tmp_path / "Crash.cue"
    cue.write_text('FILE "Crash (Track 1).bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    assert read_ps_serial(cue) == "SCUS-94900"
    assert read_ps_serial(bin_) == "SCUS-94900"

    gc = tmp_path / "Mario Kart DD.iso"
    gc.write_bytes(b"GM4E01" + bytes(0x400))
    wbfs = tmp_path / "Wii Sports.wbfs"
    wbfs.write_bytes(b"WBFS" + bytes(0x1FC) + b"RSPE01" + bytes(0x100))
    assert read_gc_wii_id(gc) == "GM4E01" and read_gc_wii_id(wbfs) == "RSPE01"
    assert read_ps_serial(gc) is None                                  # not ISO9660: no crash
    assert read_ps_serial(tmp_path / "x.chd") is None
    assert normalize_ps_serial("slus_209.84") == normalize_ps_serial("SLUS20984") == "SLUS-20984"


# ---- emulator layouts ------------------------------------------------------------------------
@pytest.fixture
def pc(tmp_path, monkeypatch):
    monkeypatch.setattr(EmulatorAdapter, "is_running", lambda self: False)
    games = tmp_path / "Games"
    (games / "PS2" / "Racing").mkdir(parents=True)
    write_iso(games / "PS2" / "Racing" / "Gran Turismo 4.iso",
              iso_with_system_cnf("BOOT2 = cdrom0:\\SCUS_973.28;1\n"))
    (games / "PS1").mkdir()
    write_raw_mode2(games / "PS1" / "Crash.bin", iso_with_system_cnf("BOOT = cdrom:\\SCUS_949.00;1\n"))
    (games / "PS1" / "Crash.cue").write_text('FILE "Crash.bin" BINARY\n')
    (games / "GC").mkdir()
    (games / "GC" / "Mario Kart DD.iso").write_bytes(b"GM4E01" + bytes(0x400))

    # PCSX2 portable
    p2 = tmp_path / "PCSX2"
    (p2 / "inis").mkdir(parents=True)
    (p2 / "inputprofiles").mkdir()
    (p2 / "pcsx2-qt.exe").write_bytes(b"")
    (p2 / "portable.ini").write_text("")
    (p2 / "inis" / "PCSX2.ini").write_text(
        f"[EmuCore/GS]\nupscale_multiplier = 1\n\n[GameList]\nRecursivePaths = {games / 'PS2'}\n"
        f"Paths = {games / 'PS1'}\n\n[Pad1]\nType = DualShock2\nCross = SDL-0/FaceSouth\n")
    (p2 / "inputprofiles" / "DualSense.ini").write_text("[Pad1]\nCross = SDL-0/FaceSouth\n")

    # DuckStation portable
    ds = tmp_path / "DuckStation"
    (ds / "inputprofiles").mkdir(parents=True)
    (ds / "duckstation-qt-x64-ReleaseLTCG.exe").write_bytes(b"")
    (ds / "portable.txt").write_text("")
    (ds / "settings.ini").write_text(f"[GPU]\nResolutionScale = 1\n\n[GameList]\nRecursivePaths = {games / 'PS1'}\n")

    # Dolphin portable
    dol = tmp_path / "Dolphin"
    (dol / "User" / "Config" / "Profiles" / "GCPad").mkdir(parents=True)
    (dol / "Dolphin.exe").write_bytes(b"")
    (dol / "portable.txt").write_text("")
    cfg = dol / "User" / "Config"
    (cfg / "Dolphin.ini").write_text(f"[General]\nISOPaths = 1\nISOPath0 = {games}\nRecursiveISOPaths = True\n")
    (cfg / "GFX.ini").write_text("[Settings]\nInternalResolution = 1\n")
    (cfg / "Profiles" / "GCPad" / "DualSense.ini").write_text("[Profile]\nDevice = SDL/0/DualSense Wireless Controller\n")

    # RPCS3
    r3 = tmp_path / "RPCS3"
    (r3 / "config" / "input_configs" / "global").mkdir(parents=True)
    (r3 / "rpcs3.exe").write_bytes(b"")
    (r3 / "config" / "config.yml").write_text(yaml.safe_dump({"Video": {"Resolution Scale": 100}}))
    (r3 / "config" / "games.yml").write_text(yaml.safe_dump({"BLUS30443": "D:/PS3/Demon's Souls/",
                                                             "NPUB30024": "D:/PS3/dev_hdd0/game/NPUB30024/"}))
    (r3 / "config" / "input_configs" / "global" / "Default.yml").write_text(
        yaml.safe_dump({"Player 1 Input": {"Handler": "DualSense", "Device": "DualSense Pad #1"}}))

    registry = EmulatorRegistry({"pcsx2": str(p2 / "pcsx2-qt.exe"), "duckstation": str(ds / "duckstation-qt-x64-ReleaseLTCG.exe"),
                                 "dolphin": str(dol / "Dolphin.exe"), "rpcs3": str(r3 / "rpcs3.exe")})
    tools = NativeTools(registry, learned=LearnedSettings(None))
    return tools, registry, dict(p2=p2, ds=ds, dol=dol, r3=r3)


def run(tools, name, **inp):
    text, err = tools.run(name, inp)
    assert not err, text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def test_every_emulator_has_guides_and_per_game(pc):
    tools, *_ = pc
    for emu in run(tools, "list_emulators"):
        assert {"controllers", "upscaling", "per_game", "streaming"} <= set(emu["guides"]), emu["id"]
        assert emu["per_game"] is True, emu["id"]
        assert emu["config_file"] or emu["id"] in ("eden", "retroarch"), emu["id"]  # not installed in this fixture
        guide = run(tools, "emulator_guide", emulator=emu["id"], topic="controllers")
        assert "Steam Input" in guide and "8BitDo" in guide


def test_pcsx2(pc):
    tools, registry, d = pc
    games = run(tools, "list_games", emulator="pcsx2", query="")
    assert {(g["file"].split("Games")[-1].replace("\\", "/"), g["id"]) for g in games} == {
        ("/PS1/Crash.cue", "SCUS-94900"),                    # .bin hidden behind its .cue
        ("/PS2/Racing/Gran Turismo 4.iso", "SCUS-97328")}    # recursive folder scanned
    text, err = tools.run("set_emulator_setting", {"emulator": "pcsx2", "section": "EmuCore/GS",
                                                   "key": "upscale_multiplier", "value": "6", "game_id": "SCUS-97328"})
    assert err and "CRC" in text                             # serial alone isn't enough the first time
    run(tools, "set_emulator_setting", emulator="pcsx2", section="EmuCore/GS", key="upscale_multiplier",
        value="6", game_id="scus_973.28_9a51b2f4")
    game_file = d["p2"] / "gamesettings" / "SCUS-97328_9A51B2F4.ini"
    assert IniDocument.load(game_file).section("EmuCore/GS") == {"upscale_multiplier": "6"}
    # Now the serial alone resolves to that file:
    run(tools, "set_emulator_setting", emulator="pcsx2", section="EmuCore/GS", key="MaxAnisotropy",
        value="16", game_id="SCUS-97328")
    assert IniDocument.load(game_file).get("EmuCore/GS", "MaxAnisotropy") == "16"
    assert IniDocument.load(d["p2"] / "inis" / "PCSX2.ini").get("EmuCore/GS", "upscale_multiplier") == "1"
    hits = run(tools, "read_emulator_config", emulator="pcsx2", section="", search="cross", game_id="")
    assert {h["file"] for h in hits} == {"PCSX2.ini", "DualSense.ini"}


def test_duckstation(pc):
    tools, registry, d = pc
    assert run(tools, "list_games", emulator="duckstation", query="crash")[0]["id"] == "SCUS-94900"
    run(tools, "set_emulator_setting", emulator="duckstation", section="GPU", key="ResolutionScale",
        value="9", game_id="scus_949.00")
    assert IniDocument.load(d["ds"] / "gamesettings" / "SCUS-94900.ini").get("GPU", "ResolutionScale") == "9"
    run(tools, "snapshot_config", emulator="duckstation", game_id="SCUS-94900")
    doc = IniDocument.load(d["ds"] / "gamesettings" / "SCUS-94900.ini")
    doc.set("GPU", "PGXPEnable", "true")                     # "changed in Game Properties"
    doc.save(d["ds"] / "gamesettings" / "SCUS-94900.ini")
    changes = run(tools, "diff_config", emulator="duckstation", game_id="SCUS-94900")["changes"]
    assert changes == [{"file": "SCUS-94900.ini", "section": "GPU", "key": "PGXPEnable", "before": None, "after": "true"}]


def test_dolphin(pc):
    tools, registry, d = pc
    assert run(tools, "list_games", emulator="dolphin", query="mario")[0]["id"] == "GM4E01"
    run(tools, "set_emulator_setting", emulator="dolphin", section="Video_Settings", key="InternalResolution",
        value="6", game_id="gm4e01")
    user = d["dol"] / "User"
    assert IniDocument.load(user / "GameSettings" / "GM4E01.ini").get("Video_Settings", "InternalResolution") == "6"
    assert IniDocument.load(user / "Config" / "GFX.ini").get("Settings", "InternalResolution") == "1"
    run(tools, "set_emulator_setting", emulator="dolphin", section="GFX.ini::Settings", key="InternalResolution",
        value="3", game_id="")
    assert IniDocument.load(user / "Config" / "GFX.ini").get("Settings", "InternalResolution") == "3"
    hits = run(tools, "read_emulator_config", emulator="dolphin", section="", search="dualsense", game_id="")
    assert hits[0]["file"] == "DualSense.ini"                # GCPad profiles are searched
    per_game = run(tools, "read_emulator_config", emulator="dolphin", section="", search="", game_id="GM4E01")
    assert per_game == {"Video_Settings": {"InternalResolution": "6"}}


def test_rpcs3(pc):
    tools, registry, d = pc
    games = run(tools, "list_games", emulator="rpcs3", query="demon")
    assert games == [{"file": "D:/PS3/Demon's Souls/", "id": "BLUS30443"}]
    text, err = tools.run("set_emulator_setting", {"emulator": "rpcs3", "section": "Video", "key": "Resolution Scale",
                                                   "value": "300", "game_id": "BLUS-30443"})
    assert err and "per_game" in text                      # RPCS3 needs the GUI to create it first
    custom = d["r3"] / "config" / "custom_configs" / "config_BLUS30443.yml"
    custom.parent.mkdir()
    custom.write_text(yaml.safe_dump({"Video": {"Resolution Scale": 100, "Renderer": "Vulkan"}}))  # made by the GUI
    run(tools, "set_emulator_setting", emulator="rpcs3", section="Video", key="Resolution Scale",
        value="300", game_id="blus30443")
    assert yaml.safe_load(custom.read_text())["Video"] == {"Resolution Scale": 300, "Renderer": "Vulkan"}
    assert yaml.safe_load((d["r3"] / "config" / "config.yml").read_text())["Video"]["Resolution Scale"] == 100
    hits = run(tools, "read_emulator_config", emulator="rpcs3", section="", search="handler", game_id="")
    assert hits == [{"file": "Default.yml", "section": "Player 1 Input", "key": "Handler", "value": "DualSense"}]


def test_per_game_writes_are_undoable(pc):
    tools, registry, d = pc
    run(tools, "set_emulator_setting", emulator="duckstation", section="GPU", key="ResolutionScale",
        value="4", game_id="SCUS-94900")
    tools.begin_run()
    run(tools, "set_emulator_setting", emulator="duckstation", section="GPU", key="ResolutionScale",
        value="9", game_id="SCUS-94900")
    tools.undo_last_run()                                    # the Undo button
    assert IniDocument.load(d["ds"] / "gamesettings" / "SCUS-94900.ini").get("GPU", "ResolutionScale") == "4"
    baks = run(tools, "list_config_backups", emulator="duckstation", game_id="SCUS-94900")
    assert baks and all(b["file"] == "SCUS-94900.ini" for b in baks)
    assert run(tools, "list_config_backups", emulator="duckstation", game_id="") == "No backups yet."
