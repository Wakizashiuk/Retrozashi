"""Built-in adapters. Key names and enum values below are a starting point; they drift between
emulator releases, so the agent always reads the live file first and can fall back to the GUI.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import yaml

from .base import IniEmulator, EmulatorAdapter, KnownSetting as K, documents_dir, env_path


def _portable(exe: Path | None, *markers: str) -> bool:
    return bool(exe) and any((exe.parent / m).exists() for m in markers)


class PCSX2(IniEmulator):
    id, display_name = "pcsx2", "PCSX2"
    process_names = ("pcsx2-qt.exe", "pcsx2.exe")
    default_install_dirs = (r"%ProgramFiles%\PCSX2",)
    KNOWN_SETTINGS = (
        K("EmuCore/GS", "upscale_multiplier", "Internal resolution multiplier: 1 = native, 3 = 1440p-ish, 6 = ~4K"),
        K("EmuCore/GS", "Renderer", "-1 Auto, 3 Direct3D 11, 12 OpenGL, 13 Software, 14 Vulkan (verify on your build)"),
        K("EmuCore/GS", "VsyncEnable", "true/false"),
        K("UI", "StartFullscreen", "true/false"),
        K("GameList", "RecursivePaths", "Game folder scanned recursively (repeat key for more)"),
        K("Pad1", "Type", "Controller type, e.g. DualShock2"),
        K("Pad1", "Cross", "Binding, e.g. SDL-0/FaceSouth or XInput-0/A"),
    )
    notes = "Portable if portable.ini or portable.txt sits next to the exe."

    def config_candidates(self) -> list[Path]:
        exe = self.find_exe()
        paths = [exe.parent / "inis" / "PCSX2.ini"] if _portable(exe, "portable.ini", "portable.txt") else []
        return paths + [documents_dir() / "PCSX2" / "inis" / "PCSX2.ini"]

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        args = ["-fullscreen"] if fullscreen else []
        return args + (["--", game] if game else [])


class DuckStation(IniEmulator):
    id, display_name = "duckstation", "DuckStation"
    process_names = ("duckstation-qt-x64-releaseltcg.exe", "duckstation-qt.exe")
    default_install_dirs = (r"%LOCALAPPDATA%\Programs\DuckStation",)
    KNOWN_SETTINGS = (
        K("GPU", "Renderer", "Automatic, Vulkan, D3D11, D3D12, OpenGL, Software"),
        K("GPU", "ResolutionScale", "Internal resolution multiplier: 1 = native 240p, 9 = ~4K"),
        K("GPU", "Multisamples", "MSAA sample count, 1 = off"),
        K("GPU", "TextureFilter", "Nearest, Bilinear, JINC2, xBR ..."),
        K("Display", "AspectRatio", "Auto (Game Native), 4:3, 16:9 ..."),
        K("Main", "StartFullscreen", "true/false"),
        K("GameList", "RecursivePaths", "Game folder scanned recursively"),
        K("Pad1", "Type", "AnalogController, DigitalController, DualShock ..."),
        K("Pad1", "Cross", "Binding, e.g. SDL-0/A"),
    )
    notes = "Portable if portable.txt sits next to the exe."

    def config_candidates(self) -> list[Path]:
        exe = self.find_exe()
        paths = [exe.parent / "settings.ini"] if _portable(exe, "portable.txt") else []
        local = env_path("LOCALAPPDATA", "DuckStation", "settings.ini")
        return paths + ([local] if local else []) + [documents_dir() / "DuckStation" / "settings.ini"]

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return (["-fullscreen"] if fullscreen else []) + ([game] if game else [])


class Dolphin(IniEmulator):
    """Dolphin splits config across files, so sections are addressed as 'File.ini::Section'."""
    id, display_name = "dolphin", "Dolphin"
    process_names = ("dolphin.exe",)
    default_install_dirs = (r"%ProgramFiles%\Dolphin",)
    KNOWN_SETTINGS = (
        K("GFX.ini::Settings", "InternalResolution", "1 = native 480p, 3 = 1080p, 6 = ~4K"),
        K("GFX.ini::Settings", "MSAA", "Anti-aliasing samples"),
        K("Dolphin.ini::Core", "GFXBackend", "Vulkan, D3D12, D3D, OGL"),
        K("Dolphin.ini::Display", "Fullscreen", "True/False"),
        K("Dolphin.ini::General", "ISOPath0", "First game folder (ISOPaths = count)"),
        K("GCPadNew.ini::GCPad1", "Device", "e.g. XInput/0/Gamepad"),
    )

    def config_dir(self) -> Path:
        exe = self.find_exe()
        if _portable(exe, "portable.txt"):
            return exe.parent / "User" / "Config"
        appdata = env_path("APPDATA", "Dolphin Emulator", "Config")
        if appdata and appdata.exists():
            return appdata
        return documents_dir() / "Dolphin Emulator" / "Config"

    def config_candidates(self) -> list[Path]:
        return [self.config_dir() / "Dolphin.ini"]

    @staticmethod
    def _split(section: str) -> tuple[str, str]:
        if "::" not in section:
            return "Dolphin.ini", section
        file, sec = section.split("::", 1)
        return file, sec

    def read_settings(self, section: str | None = None) -> dict[str, Any]:
        from .ini_store import IniDocument

        out: dict[str, Any] = {}
        for ini in ("Dolphin.ini", "GFX.ini", "GCPadNew.ini"):
            path = self.config_dir() / ini
            if not path.exists():
                continue
            doc = IniDocument.load(path)
            for s in doc.sections():
                name = f"{ini}::{s}"
                if section is None or section == name:
                    out[name] = doc.section(s)
        return out

    def target_file(self, section: str) -> Path | None:
        return self.config_dir() / self._split(section)[0]

    def config_files(self) -> list[Path]:
        return [self.config_dir() / f for f in ("Dolphin.ini", "GFX.ini", "GCPadNew.ini")]

    def _expand(self, section: str, key: str, value: str) -> list[tuple[str, str, str]]:
        return [(self._split(section)[1], key, value)]  # the file is chosen by target_file()

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return ["-b", "-e", game] if game else []


class RPCS3(EmulatorAdapter):
    """YAML config: section = top-level key ('Video'), key = nested key ('Resolution Scale')."""
    id, display_name = "rpcs3", "RPCS3"
    process_names = ("rpcs3.exe",)
    KNOWN_SETTINGS = (
        K("Video", "Renderer", "Vulkan, OpenGL, Null"),
        K("Video", "Resolution Scale", "Percent: 100 = 720p, 300 = 4K"),
        K("Video", "Anisotropic Filter Override", "0, 2, 4, 8, 16"),
        K("Video", "Frame limit", "Auto, Off, 30, 50, 60, ..."),
        K("Miscellaneous", "Start games in fullscreen mode", "true/false"),
    )
    notes = "Per-game overrides live in config/custom_configs/config_<SERIAL>.yml. Games are installed via the GUI."

    def config_candidates(self) -> list[Path]:
        exe = self.find_exe()
        if not exe:
            return []
        return [exe.parent / "config" / "config.yml", exe.parent / "config.yml"]

    def read_settings(self, section: str | None = None) -> dict[str, Any]:
        path = self.config_path()
        if path is None:
            return {}
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return {section: data.get(section, {})} if section else data

    def _get_raw(self, path: Path, section: str, key: str) -> str | None:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        value = (data.get(section) or {}).get(key)
        if value is None:
            return None
        # Text that YAML reads back unchanged stays readable; anything else (bools, numbers stored
        # as strings, lists) is JSON, which YAML parses back to the identical value on revert.
        return value if isinstance(value, str) and yaml.safe_load(value) == value else json.dumps(value)

    def _set_raw(self, path: Path, writes: list[tuple[str, str, str | None]]) -> None:
        data = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}
        for section, key, value in writes:
            node = data.setdefault(section, {})
            if value is None:
                node.pop(key, None)
            else:
                node[key] = yaml.safe_load(value) if value.strip() else value  # "300" -> 300, "true" -> True
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return ["--no-gui", game] if game else []


TITLE_ID = re.compile(r"(?<![0-9A-Fa-f])(0100[0-9A-Fa-f]{12})(?![0-9A-Fa-f])")
SWITCH_GAME_EXTS = {".nsp", ".xci", ".nca", ".nro", ".nso"}

EDEN_CONTROLLERS_GUIDE = """\
Set up a controller in Eden (DualSense/DS4, 8BitDo, or any SDL pad). Use ui_* tools first, computer tools as fallback.

Before you start
- Ask the user to connect the controller BEFORE opening Eden (it's enumerated at startup).
- Steam Input grabs PlayStation and 8BitDo pads and causes doubled or missing inputs. If Steam is
  running, ask the user to close it, or to disable Steam Input for that controller.
- 8BitDo pads have modes: Switch mode (hold Start+Y) shows Nintendo labels and gives motion on models
  with gyro; X-input mode (Start+X) appears as an Xbox pad. Both auto-map. Prefer Switch mode for motion games.

Steps
1. Eden must be running (launch_emulator eden, game_path ''), then focus_emulator_window(eden).
2. ui_list_controls(eden) -> open the "Emulation" menu -> "Configure..." -> "Controls" tab -> "Player 1".
3. "Input Device" combo box -> select the pad (e.g. "DualSense Wireless Controller 0", "8BitDo ...").
   Eden auto-maps buttons, sticks and (for DualSense/DS4) motion from this choice.
4. "Controller Type" -> "Pro Controller" (works docked and for nearly all games). Make sure Player 1
   is connected ("Connect Controller" / "Connected" checked) and other players are off unless wanted.
5. Check the mapping shows buttons for A/B/X/Y and both sticks, and that "Motion 1" is mapped for
   DualSense/DS4 (and 8BitDo in Switch mode). Turn on vibration if the user wants it.
6. Optional: in the Profile box, "New" -> name it (e.g. "DualSense") so it can be reused; it's saved
   as config/input/<name>.ini.
7. Click "OK" (not Cancel) so Eden saves. Then diff_config(eden) should show [Controls] player_0_* keys
   changing; read_emulator_config(eden, search="player_0") to confirm.
Snapshot before step 2 if you want an exact record of what changed (snapshot_config).
"""

EDEN_UPSCALING_GUIDE = """\
Upscaling in Eden. Base resolution: docked = 1080p, handheld = 720p ([System] use_docked_mode).
So docked 2x = 4K (2160p), 1.5x = 1620p (good for 1440p screens), 3x = 3240p (heavy).

Global default
1. If Eden is running, close_emulator(eden) (the user is asked to confirm), or do it via Configure ->
   Graphics in the GUI instead.
2. If list_emulators shows learned values for "resolution", use them. Otherwise learn this build's
   values once: snapshot_config(eden) -> in Eden: Emulation -> Configure... -> Graphics -> "Resolution"
   combo -> pick the option -> OK -> diff_config(eden) -> remember_setting(...) for that option.
   Do the same for "Window Adapting Filter" (scaling_filter) and "Anti-Aliasing Method" if needed.
3. set_emulator_setting(eden, "Renderer", "resolution_setup", <value>), plus filter/AA as asked.
4. read_emulator_config(eden, search="resolution") to verify.

Per game: see the per_game guide (override files take precedence over the global value).
Performance hint: demanding games (e.g. Zelda TotK) usually want 1x-1.5x; lighter ones handle 2x-3x.
AMD FSR filter sharpens when rendering below the output resolution; SMAA is cheap anti-aliasing.
"""

EDEN_PER_GAME_GUIDE = """\
Per-game overrides in Eden live in config/custom/<TitleID>.ini and win over the global config.

1. Find the title ID (16 hex digits, usually starting 0100): list_games(eden, "<part of game name>")
   reads it from the file name when present. If not, in Eden right-click the game -> Properties
   (the title ID is shown there), or check the game list.
2. Use game_id=<TitleID> with read_emulator_config / set_emulator_setting / snapshot_config /
   diff_config. Retrozashi adds the `\\use_global=false` and `\\default=false` flags for you.
3. To put a setting back to "use global", set its value to the word: global
4. Eden must be closed to write files; with it open, use right-click -> Properties in the GUI
   (snapshot/diff with the game_id afterwards to learn what it wrote).
"""


class Eden(IniEmulator):
    """Nintendo Switch emulator (yuzu lineage). Qt-settings INI: a changed key also needs
    `key\\default=false`, and per-game overrides additionally `key\\use_global=false`."""
    id, display_name = "eden", "Eden"
    process_names = ("eden.exe",)
    _VERIFY = " (yuzu-lineage values; confirm with snapshot/diff on this build)"
    KNOWN_SETTINGS = (
        K("Renderer", "resolution_setup", "Resolution scale: 0=0.5x, 1=0.75x, 2=1x native, 3=1.5x, 4=2x, 5=3x, "
          "6=4x, 7=5x, 8=6x, 9=7x, 10=8x" + _VERIFY),
        K("Renderer", "scaling_filter", "Window adapting filter: 0 Nearest, 1 Bilinear, 2 Bicubic, 3 Gaussian, "
          "4 ScaleForce, 5 AMD FSR" + _VERIFY),
        K("Renderer", "fsr_sharpening_slider", "FSR sharpness, 0-200"),
        K("Renderer", "anti_aliasing", "0 None, 1 FXAA, 2 SMAA" + _VERIFY),
        K("Renderer", "max_anisotropy", "Anisotropic filtering: 0 Automatic, 1 Default, 2 2x, 3 4x, 4 8x, 5 16x" + _VERIFY),
        K("Renderer", "backend", "Graphics API: 0 OpenGL, 1 Vulkan" + _VERIFY),
        K("Renderer", "vsync_mode", "0 Immediate (off), 1 Mailbox, 2 FIFO (on), 3 FIFO Relaxed" + _VERIFY),
        K("Renderer", "fullscreen_mode", "0 Borderless windowed, 1 Exclusive fullscreen" + _VERIFY),
        K("System", "use_docked_mode", "Docked (1080p base) vs handheld (720p base): true/false, or 1/0 on newer builds"),
        K("Controls", "player_0_connected", "Player 1 connected: true/false"),
        K("Controls", "player_0_type", "Player 1 controller type (Pro Controller recommended): enum; set via GUI "
          "once, then diff to learn the value"),
        K("Controls", "player_0_button_a", "Binding string like \"engine:sdl,port:0,guid:<device GUID>,button:1\". "
          "Same pattern for _button_b/_x/_y/_l/_r/_zl/_zr/_plus/_minus/_dup etc. Map via GUI (controllers guide)"),
        K("Controls", "player_0_lstick", "Stick binding \"engine:sdl,...,axis_x:0,axis_y:1,deadzone:0.150000\" (also _rstick)"),
        K("Controls", "player_0_motionleft", "Motion binding (DualSense/DS4 gyro); also _motionright"),
        K("Controls", "vibration_enabled", "Rumble on/off: true/false"),
        K("Controls", "motion_enabled", "Motion controls on/off: true/false"),
        K("UI", "fullscreen", "Start fullscreen: true/false"),
    )
    GUIDES = {"controllers": EDEN_CONTROLLERS_GUIDE, "upscaling": EDEN_UPSCALING_GUIDE,
              "per_game": EDEN_PER_GAME_GUIDE}
    notes = ("Config: qt-config.ini in user/config (portable, next to eden.exe) or %APPDATA%\\eden\\config. "
             "String values that contain commas/colons (bindings) are written in double quotes. Enum values can "
             "differ between builds: learn them with snapshot_config -> GUI change -> diff_config -> remember_setting. "
             "Per-game overrides via game_id (see emulator_guide eden per_game).")

    def __init__(self, exe_override: str | None = None) -> None:
        super().__init__(exe_override)
        self._game_id: str | None = None

    # ---- locations ------------------------------------------------------------
    def _global_candidates(self) -> list[Path]:
        exe = self.find_exe()
        portable = [exe.parent / "user" / "config" / "qt-config.ini"] if exe else []
        roaming = env_path("APPDATA", "eden", "config", "qt-config.ini")
        return portable + ([roaming] if roaming else [])

    def config_dir(self) -> Path | None:
        found = next((p for p in self._global_candidates() if p.exists()), None)
        return found.parent if found else None

    def config_candidates(self) -> list[Path]:
        if self._game_id:
            cfg = self.config_dir()
            return [cfg / "custom" / f"{self._game_id}.ini"] if cfg else []
        return self._global_candidates()

    def target_file(self, section: str) -> Path | None:
        if self._game_id:  # an override file is created on first write
            candidates = self.config_candidates()
            return candidates[0] if candidates else None
        return self.config_path()

    def config_files(self) -> list[Path]:
        if self._game_id:
            return self.config_candidates()
        return super().config_files()

    def watch_files(self) -> list[Path]:
        files = self.config_files()
        cfg = self.config_dir()
        if cfg and not self._game_id:
            files += sorted((cfg / "input").glob("*.ini"))  # saved controller profiles
        return files

    # ---- per-game -------------------------------------------------------------
    def for_game(self, game_id: str) -> "Eden":
        tid = game_id.strip().upper()
        if not re.fullmatch(r"[0-9A-F]{16}", tid):
            raise ValueError(f"{game_id!r} isn't a Switch title ID (16 hex digits, e.g. 0100F2C0115B6000). "
                             "Use list_games(eden, '<name>') or Eden's right-click -> Properties.")
        view = copy.copy(self)
        view._game_id = tid
        return view

    def _expand(self, section: str, key: str, value: str) -> list[tuple[str, str, str | None]]:
        if self._game_id:
            if value.strip().lower() == "global":  # drop the override, fall back to the global value
                return [(section, key, None), (section, f"{key}\\use_global", "true"),
                        (section, f"{key}\\default", None)]
            return [(section, key, value), (section, f"{key}\\use_global", "false"),
                    (section, f"{key}\\default", "false")]
        return [(section, key, value), (section, f"{key}\\default", "false")]

    def list_games(self, query: str = "") -> list[dict[str, str | None]]:
        """Switch game files in Eden's configured game folders, with title IDs parsed from file names."""
        from .ini_store import IniDocument

        cfg = self.config_path() if not self._game_id else None
        if cfg is None:
            return []
        folders = [Path(v.strip('"')) for k, v in IniDocument.load(cfg).section("UI").items()
                   if re.fullmatch(r"Paths\\gamedirs\\\d+\\path", k)]
        games = []
        for folder in folders:
            if not folder.is_dir():
                continue
            for f in sorted(folder.rglob("*")):
                if f.suffix.lower() in SWITCH_GAME_EXTS and query.lower() in f.name.lower():
                    m = TITLE_ID.search(f.name)
                    games.append({"file": str(f), "title_id": m.group(1).upper() if m else None})
                    if len(games) >= 100:
                        return games
        return games

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return (["-f"] if fullscreen else []) + (["-g", game] if game else [])


BUILTIN: dict[str, type[EmulatorAdapter]] = {cls.id: cls for cls in (PCSX2, DuckStation, Dolphin, RPCS3, Eden)}
