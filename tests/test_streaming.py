"""Moonlight + Vibepollo: pad detection, controller sets (save / apply / auto-choose / sync), the
stream-start worker, and adding the prep command to Vibepollo's config without touching anything else."""
from __future__ import annotations

import json

import pytest
import yaml

from retrozashi.agent.tools import NativeTools
from retrozashi.controller_sets import ControllerSets
from retrozashi.desktop.pads import pads_from_ids
from retrozashi.emulators.base import EmulatorAdapter
from retrozashi.emulators.cfg_store import CfgDocument
from retrozashi.emulators.ini_store import IniDocument
from retrozashi.emulators.registry import EmulatorRegistry
from retrozashi.streaming import install_prep_command, sync_worker

X360, DS4 = "045E:028E", "054C:05C4"   # Vibepollo's virtual pads for an 8BitDo (X-input) and a DualSense
GUID_DS4 = "030000004c050000c405000000016800"
GUID_X360 = "030000005e0400008e02000000007801"


def test_pad_detection_from_device_ids():
    ids = [r"HID\VID_045E&PID_028E&IG_00\8&2B&0&0000",       # XInput virtual pad
           r"HID\VID_054C&PID_05C4\9&AB&0&0000",              # virtual DS4
           r"HID\VID_045E&PID_07A5&MI_00\1&2",                # Microsoft keyboard: ignored
           r"HID\VID_054C&PID_05C4&COL02\9&AB&0&0001"]        # second collection of the same pad: ignored
    assert pads_from_ids(ids) == [X360, DS4]


@pytest.fixture
def pc(tmp_path, monkeypatch):
    monkeypatch.setattr(EmulatorAdapter, "is_running", lambda self: False)
    eden = tmp_path / "Eden"
    (eden / "user" / "config").mkdir(parents=True)
    (eden / "eden.exe").write_bytes(b"")
    qt = eden / "user" / "config" / "qt-config.ini"
    qt.write_text("[Controls]\nplayer_0_connected=true\n\n[Renderer]\nresolution_setup=4\n")
    ra = tmp_path / "RetroArch"
    ra.mkdir()
    (ra / "retroarch.exe").write_bytes(b"")
    (ra / "retroarch.cfg").write_text('video_driver = "vulkan"\ninput_joypad_driver = "sdl2"\n')
    r3 = tmp_path / "RPCS3"
    (r3 / "config" / "input_configs" / "global").mkdir(parents=True)
    (r3 / "rpcs3.exe").write_bytes(b"")
    (r3 / "config" / "config.yml").write_text("Video: {Resolution Scale: 100}\n")
    pad_yml = r3 / "config" / "input_configs" / "global" / "Default.yml"
    registry = EmulatorRegistry({"eden": str(eden / "eden.exe"), "retroarch": str(ra / "retroarch.exe"),
                                 "rpcs3": str(r3 / "rpcs3.exe")})
    sets = ControllerSets(registry, tmp_path / "controller_sets.json")
    pads = {"now": [DS4]}
    tools = NativeTools(registry, controller_sets=sets, detect_pads=lambda: pads["now"])
    return tools, sets, registry, pads, qt, pad_yml, tmp_path


def map_eden(qt, guid, extra_button=None):
    """What Eden writes when a pad is mapped in Configure -> Controls."""
    doc = IniDocument.load(qt)
    doc.set("Controls", "player_0_button_a", f'"engine:sdl,port:0,guid:{guid},button:1"')
    doc.set("Controls", "player_0_button_a\\default", "false")
    if extra_button:
        doc.set("Controls", extra_button, f'"engine:sdl,port:0,guid:{guid},button:9"')
    doc.save(qt)


def run(tools, tool, **inp):
    text, err = tools.run(tool, inp)
    assert not err, text
    return json.loads(text) if text.lstrip()[:1] in "[{" else text


def test_save_and_switch_sets_per_virtual_pad(pc):
    tools, sets, registry, pads, qt, pad_yml, _ = pc
    # Session 1: DualSense client -> virtual DS4. Map in Eden and RPCS3, save.
    map_eden(qt, GUID_DS4, extra_button="player_0_motionleft")
    pad_yml.write_text(yaml.safe_dump({"Player 1 Input": {"Handler": "DualShock 4", "Device": "DS4 Pad #1"}}))
    assert run(tools, "detect_controllers")[0]["looks_like"].startswith("DualShock 4")
    saved = run(tools, "save_controller_set", emulator="eden", name="DualSense")
    assert saved["pads"] == [DS4] and saved["captured"]["keys"] == 4   # connected, button_a(+default), motionleft
    run(tools, "save_controller_set", emulator="rpcs3", name="DualSense")

    # Session 2: 8BitDo client -> virtual Xbox 360. Remap, save.
    pads["now"] = [X360]
    doc = IniDocument.load(qt)
    doc.remove("Controls", "player_0_motionleft")                       # no motion on a 360 pad
    doc.save(qt)
    map_eden(qt, GUID_X360)
    pad_yml.write_text(yaml.safe_dump({"Player 1 Input": {"Handler": "XInput", "Device": "XInput Pad #1"}}))
    run(tools, "save_controller_set", emulator="eden", name="8BitDo")
    run(tools, "save_controller_set", emulator="rpcs3", name="8BitDo")
    listed = run(tools, "list_controller_sets")
    assert set(listed["eden"]["sets"]) == {"DualSense", "8BitDo"} and listed["eden"]["active"] == "8BitDo"

    # Next stream: DualSense again -> sync switches both emulators back, exactly.
    pads["now"] = [DS4]
    out = run(tools, "sync_controllers")
    assert {r["emulator"]: r["result"] for r in out["results"]} == {
        "eden": "switched to 'DualSense'", "rpcs3": "switched to 'DualSense'"}
    controls = IniDocument.load(qt).section("Controls")
    assert GUID_DS4 in controls["player_0_button_a"] and "player_0_motionleft" in controls
    assert IniDocument.load(qt).get("Renderer", "resolution_setup") == "4"      # nothing else touched
    assert yaml.safe_load(pad_yml.read_text())["Player 1 Input"]["Handler"] == "DualShock 4"
    # 8BitDo again: the motion key that only the DualSense set has is removed.
    pads["now"] = [X360]
    run(tools, "sync_controllers")
    controls = IniDocument.load(qt).section("Controls")
    assert GUID_X360 in controls["player_0_button_a"] and "player_0_motionleft" not in controls
    # Same pad again: nothing to do.
    assert run(tools, "sync_controllers")["results"][0]["result"] == "already using '8BitDo'"


def test_sync_skips_running_emulators_and_unknown_pads(pc, monkeypatch):
    tools, sets, registry, pads, qt, *_ = pc
    map_eden(qt, GUID_DS4)
    sets.save("eden", "DualSense", [DS4])
    pads["now"] = ["1234:5678"]
    assert sets.sync(pads["now"])[0]["result"] == "no set matches the connected pads"
    sets.save("eden", "8BitDo", [X360])
    monkeypatch.setattr(type(registry.get("eden")), "is_running", lambda self: True)
    assert sets.sync([DS4])[0]["result"] == "running, left as is (wanted 'DualSense')"
    text, err = tools.run("save_controller_set", {"emulator": "eden", "name": "x"})
    assert not err  # saving reads only; allowed while running
    pads["now"] = []
    text, err = tools.run("save_controller_set", {"emulator": "eden", "name": "nothing"})
    assert err and "Moonlight session" in text


def test_retroarch_set_keeps_quotes(pc):
    tools, sets, registry, pads, qt, pad_yml, tmp_path = pc
    cfg = tmp_path / "RetroArch" / "retroarch.cfg"
    doc = CfgDocument.load(cfg)
    doc.set("input_player1_joypad_index", "0")
    doc.save(cfg)
    sets.save("retroarch", "DualSense", [DS4])
    doc.set("input_player1_joypad_index", "1")
    doc.set("input_joypad_driver", "xinput")
    doc.save(cfg)
    sets.save("retroarch", "8BitDo", [X360])
    sets.apply("retroarch", "DualSense")
    text = cfg.read_text()
    assert 'input_player1_joypad_index = "0"' in text and 'input_joypad_driver = "sdl2"' in text
    assert 'video_driver = "vulkan"' in text


def test_stream_start_worker_waits_for_the_virtual_pad(pc):
    tools, sets, registry, pads, qt, *_ = pc
    map_eden(qt, GUID_DS4)
    sets.save("eden", "DualSense", [DS4])
    map_eden(qt, GUID_X360)
    sets.save("eden", "8BitDo", [X360])
    # Vibepollo starts the stream; the client's pad arrives a few polls later.
    timeline = iter([[], [], [DS4], [DS4], [DS4]])
    now = {"t": 0.0}
    logs = []
    sync_worker(sets, lambda: next(timeline), wait=4, log=logs.append, poll=1.0,
                clock=lambda: now["t"], sleep=lambda s: now.__setitem__("t", now["t"] + s))
    assert GUID_DS4 in IniDocument.load(qt).get("Controls", "player_0_button_a")
    assert len(logs) == 1   # synced once, when the pad appeared


def test_install_prep_command_keeps_other_settings(tmp_path):
    conf = tmp_path / "Vibepollo" / "config" / "sunshine.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text('sunshine_name = Gaming PC\r\nglobal_prep_cmd = [{"do":"C:\\\\Tools\\\\hdr.exe on","undo":"C:\\\\Tools\\\\hdr.exe off","elevated":true}]\r\nport = 47989\r\n',
                    newline="")
    cmd = '"C:\\Retrozashi\\Retrozashi.exe" --sync-controllers'
    out = install_prep_command(conf, cmd)
    assert out["other_prep_commands"] == 1 and out["backup"]
    lines = conf.read_bytes().decode().split("\r\n")
    assert lines[0] == "sunshine_name = Gaming PC" and lines[2] == "port = 47989"
    prep = json.loads(lines[1].split("=", 1)[1])
    assert prep[0] == {"do": "C:\\Tools\\hdr.exe on", "undo": "C:\\Tools\\hdr.exe off", "elevated": True}
    assert prep[1] == {"do": cmd, "undo": "", "elevated": False}
    # Running it again updates our entry instead of adding a second one.
    install_prep_command(conf, cmd.replace("C:\\Retrozashi", "D:\\Apps\\Retrozashi"))
    prep = json.loads(conf.read_text().splitlines()[1].split("=", 1)[1])
    assert len(prep) == 2 and prep[1]["do"].startswith('"D:\\Apps')
    # A config without the setting gets one added.
    bare = tmp_path / "sunshine.conf"
    bare.write_text("port = 47989\n")
    install_prep_command(bare, cmd)
    assert json.loads(bare.read_text().splitlines()[1].split("=", 1)[1])[0]["do"] == cmd


def test_install_tool_falls_back_to_elevated_helper(pc, tmp_path, monkeypatch):
    tools, *_ = pc
    conf = tmp_path / "PF" / "Vibepollo" / "config" / "sunshine.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("port = 47989\n")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "PF"))
    import retrozashi.streaming as streaming
    monkeypatch.setattr(streaming, "install_prep_command",
                        lambda c, cmd: (_ for _ in ()).throw(PermissionError("Program Files")))
    calls = []
    tools._elevate = lambda exe, params: calls.append(params) or 0
    out = run(tools, "install_stream_sync")
    assert out["elevated"] and "--install-stream-sync" in calls[0] and "--sync-controllers" in calls[0]
