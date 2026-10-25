"""Built-in adapters. Key names and enum values below are a starting point; they drift between
emulator releases, so the agent always reads the live file first and can fall back to the GUI.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .base import IniEmulator, EmulatorAdapter, KnownSetting as K, documents_dir, env_path
from .discs import normalize_ps_serial, read_gc_wii_id, read_ps_serial


def _portable(exe: Path | None, *markers: str) -> bool:
    return bool(exe) and any((exe.parent / m).exists() for m in markers)


_V = " (verify with snapshot/diff on this build)"

PAD_PREP = """\
Before you start (DualSense/DS4 and 8BitDo)
- Connect the controller before opening the emulator.
- Steam Input grabs PlayStation and 8BitDo pads and causes doubled or missing inputs: ask the user
  to close Steam or disable Steam Input for that pad.
- 8BitDo modes: X-input (Start+X) appears as an Xbox pad; Switch mode (Start+Y) as a Pro Controller.
  Both work with SDL; keep the same mode once mapped.
"""

PCSX2_CONTROLLERS = PAD_PREP + """
Steps (ui_* tools first, computer tools as fallback)
1. PCSX2 running (launch_emulator pcsx2, game_path ''), focus_emulator_window(pcsx2).
2. Settings -> Controllers. Under the global options make sure "Enable SDL Input Source" is on, and
   for DualSense/DS4 "DualShock 4 / DualSense Enhanced Mode" (gives rumble, LEDs, full buttons).
3. "Controller Port 1" -> type "DualShock 2".
4. Click "Automatic Mapping" (wand icon) -> pick the pad (e.g. "SDL-0 DualSense Wireless Controller").
   All buttons, sticks and pressure are mapped.
5. Optional: save as an input profile (Controllers window -> profile -> New), stored in inputprofiles/.
6. Close the window (PCSX2 saves immediately). Verify: read_emulator_config(pcsx2, search="Pad1").
"""

PCSX2_UPSCALING = """\
Upscaling in PCSX2: [EmuCore/GS] upscale_multiplier. PS2 games render around 448-480 lines, so
3x = ~1080p, 4x = ~1440p, 6x = ~4K (PCSX2 labels these in Graphics -> Rendering -> Internal Resolution).
1. If PCSX2 is running, close_emulator(pcsx2) (user confirms) or use Settings -> Graphics in the GUI.
2. set_emulator_setting(pcsx2, "EmuCore/GS", "upscale_multiplier", "<n>"). Renderer: Vulkan or DX12
   for high multipliers (enum values: check learned settings or learn by diff).
3. Useful extras: MaxAnisotropy 16, [EmuCore] EnableWideScreenPatches true (needs patches for the game).
4. Verify with read_emulator_config(pcsx2, search="upscale").
Per game: see per_game. Some games show lines/ghosting when upscaled: suggest 'Half-pixel offset'
or 'Round sprite' fixes in the per-game Graphics -> Upscaling Fixes tab (learn the keys by diff).
"""

PCSX2_PER_GAME = """\
PCSX2 per-game settings: gamesettings/<SERIAL>_<CRC>.ini (same sections/keys as PCSX2.ini; they
override the global values for that game).
1. list_games(pcsx2, "<name>") reads the serial from the disc image (SYSTEM.CNF). The CRC isn't in the
   file: if the game already has a per-game file the serial alone is enough; otherwise get the CRC
   from PCSX2: right-click the game -> Properties (Summary shows Serial and CRC), or create the
   per-game settings once there.
2. Use game_id="<SERIAL>_<CRC>" (e.g. SLUS-20984_E2C8A0D8) with read/set/snapshot/diff.
3. PCSX2 must be closed to write files; otherwise use right-click -> Properties in the GUI.
"""

DUCK_CONTROLLERS = PAD_PREP + """
Steps
1. DuckStation running, focus_emulator_window(duckstation).
2. Settings -> Controllers. Make sure "Enable SDL Input Source" is on (and "DualShock 4 / DualSense
   Enhanced Mode" for PlayStation pads).
3. "Controller Port 1" -> "Analog Controller" (DualShock) for analog games, "Digital Controller" for
   strict old games.
4. "Automatic Mapping" -> pick the pad (e.g. "SDL-0"). Check both sticks; turn on Analog mode
   behaviour ("Force Analog Mode on Reset") if games start in digital mode.
5. Optional: save an input profile (stored in inputprofiles/). Close the window.
6. Verify: read_emulator_config(duckstation, search="Pad1").
"""

DUCK_UPSCALING = """\
Upscaling in DuckStation: [GPU] ResolutionScale (PS1 renders ~240 lines: 4x ~ 1080p, 6x ~ 1440p,
9x ~ 4K). Hardware renderer required ([GPU] Renderer = Vulkan/D3D11/D3D12/OpenGL, not Software).
1. Close DuckStation first (close_emulator asks the user) or use Settings -> Graphics.
2. set_emulator_setting(duckstation, "GPU", "ResolutionScale", "<n>").
3. Recommended with upscaling: [GPU] PGXPEnable true (stops polygon wobble/texture warping),
   [GPU] TrueColor true; widescreen: [GPU] WidescreenHack true + [Display] AspectRatio 16:9.
4. Verify with read_emulator_config(duckstation, search="ResolutionScale").
"""

DUCK_PER_GAME = """\
DuckStation per-game settings: gamesettings/<SERIAL>.ini next to settings.ini (same sections/keys).
1. list_games(duckstation, "<name>") reads the serial (e.g. SCUS-94163) from .cue/.bin/.iso images.
   For .chd/.pbp: right-click the game in DuckStation -> Properties shows it.
2. Use game_id="<SERIAL>" with read/set/snapshot/diff. Close DuckStation first, or use
   right-click -> Game Properties in the GUI and diff afterwards.
"""

DOLPHIN_CONTROLLERS = PAD_PREP + """
GameCube games (Controllers -> Port 1 "Standard Controller" -> Configure)
1. Dolphin running, focus_emulator_window(dolphin). Open "Controllers" from the toolbar.
2. GameCube Port 1 = "Standard Controller" -> "Configure".
3. Device dropdown -> "SDL/0/<pad name>" (e.g. DualSense Wireless Controller). Click "Default" to
   load the standard layout for that device; if some fields stay empty, click a field and press the
   button. Sticks: Main Stick = left stick, C-Stick = right stick.
4. Optional: Profile box -> name -> Save (stored in Config/Profiles/GCPad). Close; then OK.
Wii games: Controllers -> Wii Remote 1 = "Emulated Wii Remote" -> Configure, same device/Default
steps (map the pointer to the right stick or motion), or connect a real Wii Remote.
DualSense motion can drive Wii Remote tilt/point via the Motion Input tab.
Verify: read_emulator_config(dolphin, search="GCPad1").
"""

DOLPHIN_UPSCALING = """\
Upscaling in Dolphin: GFX.ini [Settings] InternalResolution (1 = native 480p, 2 = 720p, 3 = 1080p,
4 = 1440p, 6 = 4K). Section names in tools: "GFX.ini::Settings".
1. Close Dolphin first or use Graphics -> Enhancements in the GUI.
2. set_emulator_setting(dolphin, "GFX.ini::Settings", "InternalResolution", "<n>").
3. Extras: "GFX.ini::Enhancements" MaxAnisotropy (0=1x .. 4=16x), "GFX.ini::Settings" MSAA,
   widescreen hack "GFX.ini::Settings" wideScreenHack True.
4. Verify with read_emulator_config(dolphin, search="InternalResolution").
"""

DOLPHIN_PER_GAME = """\
Dolphin per-game settings: User/GameSettings/<GameID>.ini (6-char ID, e.g. GM4E01; 3-char prefixes
like GM4.ini apply to all regions). Sections differ from the global files:
  [Video_Settings] InternalResolution, MSAA, SSAA, wideScreenHack
  [Video_Enhancements] MaxAnisotropy, ForceTextureFiltering
  [Core] GFXBackend, CPUThread, ...
1. list_games(dolphin, "<name>") reads the ID from .iso/.gcm/.wbfs. For .rvz/.gcz: right-click the game
   -> Properties -> Info shows it.
2. Use game_id="<ID>" with plain section names (e.g. section "Video_Settings").
3. If unsure of a key: right-click -> Properties -> Game Config -> Editor shows the file; or snapshot,
   change it in the GUI, diff.
"""

RPCS3_CONTROLLERS = PAD_PREP + """
Steps
1. RPCS3 running, focus_emulator_window(rpcs3). Open "Pads" (toolbar) / Configuration -> Pads.
2. Player 1 -> Handler: "DualSense" for a DualSense, "DualShock 4" for a DS4 (native: rumble, LED,
   motion); "SDL" or "XInput" for an 8BitDo (X-input mode).
3. Device -> the pad (e.g. "DualSense Pad #1"). RPCS3 fills the default layout for that handler.
4. Check sticks/deadzones with the visual tester, then "Save".
5. Verify: read_emulator_config(rpcs3, search="Handler") also searches input_configs.
"""

RPCS3_UPSCALING = """\
Upscaling in RPCS3: Video "Resolution Scale" (percent of 720p: 150 = 1080p, 200 = 1440p, 300 = 4K).
Keep Video "Resolution" at 1280x720 (the game's native output); scale on top of it.
1. Close RPCS3 first or use Configuration -> GPU in the GUI.
2. set_emulator_setting(rpcs3, "Video", "Resolution Scale", "<percent>").
3. Extras: "Anisotropic Filter Override" 16, "Output Scaling Mode" (Bilinear / Nearest / FidelityFX
   Super Resolution), "Renderer" Vulkan.
4. Verify with read_emulator_config(rpcs3, search="Resolution Scale").
Heavy games may need 100-150%; check the game's wiki page on rpcs3.net for known issues.
"""

RPCS3_PER_GAME = """\
RPCS3 per-game configs: config/custom_configs/config_<SERIAL>.yml (e.g. BLUS30443). RPCS3 writes a
full copy when one is created, so create it in the GUI first:
1. list_games(rpcs3, "<name>") reads serials from RPCS3's games.yml.
2. If the game has no custom config: right-click the game -> "Create Custom Configuration" -> OK.
3. Then use game_id="<SERIAL>" with read/set/snapshot/diff (same sections as config.yml, e.g. Video).
"""


class PCSX2(IniEmulator):
    id, display_name = "pcsx2", "PCSX2"
    process_names = ("pcsx2-qt.exe", "pcsx2.exe")
    default_install_dirs = (r"%ProgramFiles%\PCSX2",)
    GAME_EXTS = frozenset({".iso", ".chd", ".cso", ".zso", ".gz", ".bin", ".cue", ".img", ".elf"})
    KNOWN_SETTINGS = (
        K("EmuCore/GS", "upscale_multiplier", "Internal resolution: 1 native, 3 ~1080p, 4 ~1440p, 6 ~4K"),
        K("EmuCore/GS", "Renderer", "-1 Auto, 3 Direct3D 11, 12 OpenGL, 13 Software, 14 Vulkan" + _V),
        K("EmuCore/GS", "MaxAnisotropy", "Anisotropic filtering: 0 off, 2, 4, 8, 16"),
        K("EmuCore/GS", "AspectRatio", "Auto 4:3/3:2, 4:3, 16:9, Stretch"),
        K("EmuCore/GS", "VsyncEnable", "true/false"),
        K("EmuCore", "EnableWideScreenPatches", "true/false (applies widescreen patches when available)"),
        K("UI", "StartFullscreen", "true/false"),
        K("GameList", "RecursivePaths", "Game folder scanned recursively (key repeats per folder)"),
        K("InputSources", "SDL", "Enable SDL controllers: true/false"),
        K("InputSources", "SDLControllerEnhancedMode", "DualShock 4 / DualSense enhanced mode: true/false"),
        K("Pad1", "Type", "Controller type: DualShock2"),
        K("Pad1", "Cross", "Binding, e.g. SDL-0/FaceSouth (Circle FaceEast, Square FaceWest, Triangle FaceNorth)" + _V),
    )
    GUIDES = {"controllers": PCSX2_CONTROLLERS, "upscaling": PCSX2_UPSCALING, "per_game": PCSX2_PER_GAME}
    notes = "Portable if portable.ini or portable.txt sits next to the exe. Per-game: gamesettings/<SERIAL>_<CRC>.ini."

    def data_dir(self) -> Path:
        cfg = self.config_path() if self._game_file is None else None
        if cfg:
            return cfg.parent.parent  # <data>/inis/PCSX2.ini
        exe = self.find_exe()
        return exe.parent if _portable(exe, "portable.ini", "portable.txt") else documents_dir() / "PCSX2"

    def config_candidates(self) -> list[Path]:
        exe = self.find_exe()
        paths = [exe.parent / "inis" / "PCSX2.ini"] if _portable(exe, "portable.ini", "portable.txt") else []
        return paths + [documents_dir() / "PCSX2" / "inis" / "PCSX2.ini"]

    def extra_watch_files(self) -> list[Path]:
        return sorted((self.data_dir() / "inputprofiles").glob("*.ini"))

    def normalize_game_id(self, game_id: str) -> str:
        serial = normalize_ps_serial(game_id)
        if not serial:
            raise ValueError(f"{game_id!r} isn't a PS2 serial (e.g. SLUS-20984 or SLUS-20984_E2C8A0D8)")
        crc = re.search(r"_([0-9A-Fa-f]{8})\b", game_id)
        if crc:
            return f"{serial}_{crc.group(1).upper()}"
        existing = sorted((self.data_dir() / "gamesettings").glob(f"{serial}_*.ini"))
        if len(existing) == 1:
            return existing[0].stem
        raise ValueError(f"PCSX2 per-game files are named <SERIAL>_<CRC>. No existing file for {serial}: get the "
                         "CRC from PCSX2 (right-click the game -> Properties) and pass SERIAL_CRC.")

    def game_config_file(self, game_id: str) -> Path:
        return self.data_dir() / "gamesettings" / f"{game_id}.ini"

    def list_games(self, query: str = "") -> list[dict[str, Any]]:
        from .ini_store import IniDocument

        cfg = self.config_path()
        if cfg is None:
            return []
        doc = IniDocument.load(cfg)
        folders = [(Path(p), False) for p in doc.get_all("GameList", "Paths")]
        folders += [(Path(p), True) for p in doc.get_all("GameList", "RecursivePaths")]
        return self._scan_games(folders, read_ps_serial, query)

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        args = ["-fullscreen"] if fullscreen else []
        return args + (["--", game] if game else [])


class DuckStation(IniEmulator):
    id, display_name = "duckstation", "DuckStation"
    process_names = ("duckstation-qt-x64-releaseltcg.exe", "duckstation-qt.exe")
    default_install_dirs = (r"%LOCALAPPDATA%\Programs\DuckStation",)
    GAME_EXTS = frozenset({".cue", ".bin", ".iso", ".img", ".chd", ".pbp", ".m3u", ".ecm", ".mds"})
    KNOWN_SETTINGS = (
        K("GPU", "Renderer", "Automatic, Vulkan, D3D11, D3D12, OpenGL, Software"),
        K("GPU", "ResolutionScale", "Internal resolution: 1 native 240p, 4 ~1080p, 6 ~1440p, 9 ~4K"),
        K("GPU", "Multisamples", "MSAA sample count, 1 = off"),
        K("GPU", "TextureFilter", "Nearest, Bilinear, JINC2, xBR ..."),
        K("GPU", "PGXPEnable", "Geometry correction (no wobble): true/false"),
        K("GPU", "TrueColor", "24-bit colour, removes dithering banding: true/false"),
        K("GPU", "WidescreenHack", "Widescreen rendering hack: true/false (pair with AspectRatio 16:9)"),
        K("Display", "AspectRatio", "Auto (Game Native), 4:3, 16:9 ..."),
        K("Main", "StartFullscreen", "true/false"),
        K("GameList", "RecursivePaths", "Game folder scanned recursively (key repeats per folder)"),
        K("InputSources", "SDL", "Enable SDL controllers: true/false"),
        K("Pad1", "Type", "AnalogController (DualShock), DigitalController, ..."),
        K("Pad1", "Cross", "Binding, e.g. SDL-0/A" + _V),
    )
    GUIDES = {"controllers": DUCK_CONTROLLERS, "upscaling": DUCK_UPSCALING, "per_game": DUCK_PER_GAME}
    notes = "Portable if portable.txt sits next to the exe. Per-game: gamesettings/<SERIAL>.ini."

    def config_candidates(self) -> list[Path]:
        exe = self.find_exe()
        paths = [exe.parent / "settings.ini"] if _portable(exe, "portable.txt") else []
        local = env_path("LOCALAPPDATA", "DuckStation", "settings.ini")
        return paths + ([local] if local else []) + [documents_dir() / "DuckStation" / "settings.ini"]

    def data_dir(self) -> Path:
        cfg = next((p for p in self.config_candidates() if p.exists()), None)
        return (cfg or self.config_candidates()[-1]).parent

    def extra_watch_files(self) -> list[Path]:
        return sorted((self.data_dir() / "inputprofiles").glob("*.ini"))

    def normalize_game_id(self, game_id: str) -> str:
        serial = normalize_ps_serial(game_id)
        if not serial:
            raise ValueError(f"{game_id!r} isn't a PS1 serial (e.g. SCUS-94163)")
        return serial

    def game_config_file(self, game_id: str) -> Path:
        return self.data_dir() / "gamesettings" / f"{game_id}.ini"

    def list_games(self, query: str = "") -> list[dict[str, Any]]:
        from .ini_store import IniDocument

        cfg = self.config_path()
        if cfg is None:
            return []
        doc = IniDocument.load(cfg)
        folders = [(Path(p), False) for p in doc.get_all("GameList", "Paths")]
        folders += [(Path(p), True) for p in doc.get_all("GameList", "RecursivePaths")]
        return self._scan_games(folders, read_ps_serial, query)

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return (["-fullscreen"] if fullscreen else []) + ([game] if game else [])


class Dolphin(IniEmulator):
    """Dolphin splits config across files, so global sections are addressed as 'File.ini::Section'.
    Per-game files (GameSettings/<ID>.ini) use plain section names."""
    id, display_name = "dolphin", "Dolphin"
    process_names = ("dolphin.exe",)
    default_install_dirs = (r"%ProgramFiles%\Dolphin",)
    CONFIG_FILES = ("Dolphin.ini", "GFX.ini", "GCPadNew.ini", "WiimoteNew.ini")
    GAME_EXTS = frozenset({".iso", ".gcm", ".wbfs", ".rvz", ".wia", ".gcz", ".ciso", ".wad", ".dol", ".elf"})
    KNOWN_SETTINGS = (
        K("GFX.ini::Settings", "InternalResolution", "1 native 480p, 2 720p, 3 1080p, 4 1440p, 6 4K"),
        K("GFX.ini::Settings", "MSAA", "Anti-aliasing samples"),
        K("GFX.ini::Settings", "SSAA", "Supersampling instead of MSAA: True/False"),
        K("GFX.ini::Settings", "wideScreenHack", "Widescreen hack: True/False"),
        K("GFX.ini::Settings", "AspectRatio", "0 Auto, 1 Force 16:9, 2 Force 4:3, 3 Stretch" + _V),
        K("GFX.ini::Enhancements", "MaxAnisotropy", "0 1x, 1 2x, 2 4x, 3 8x, 4 16x"),
        K("Dolphin.ini::Core", "GFXBackend", "Vulkan, D3D12, D3D, OGL"),
        K("Dolphin.ini::Core", "SIDevice0", "GameCube port 1 device: 6 Standard Controller" + _V),
        K("Dolphin.ini::Display", "Fullscreen", "True/False"),
        K("Dolphin.ini::General", "ISOPath0", "First game folder (ISOPaths = count)"),
        K("GCPadNew.ini::GCPad1", "Device", "e.g. SDL/0/DualSense Wireless Controller"),
        K("WiimoteNew.ini::Wiimote1", "Source", "0 None, 1 Emulated, 2 Real Wii Remote" + _V),
    )
    GUIDES = {"controllers": DOLPHIN_CONTROLLERS, "upscaling": DOLPHIN_UPSCALING, "per_game": DOLPHIN_PER_GAME}
    notes = "Global sections are 'File.ini::Section'. Per-game: GameSettings/<GameID>.ini with plain sections."

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

        if self._game_file is not None:
            return super().read_settings(section)
        out: dict[str, Any] = {}
        for ini in self.CONFIG_FILES:
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
        if self._game_file is not None:
            return self._game_file
        return self.config_dir() / self._split(section)[0]

    def config_files(self) -> list[Path]:
        if self._game_file is not None:
            return [self._game_file]
        return [self.config_dir() / f for f in self.CONFIG_FILES]

    def extra_watch_files(self) -> list[Path]:
        profiles = self.config_dir() / "Profiles"
        return sorted(profiles.glob("GCPad/*.ini")) + sorted(profiles.glob("Wiimote/*.ini"))

    def _expand(self, section: str, key: str, value: str) -> list[tuple[str, str, str]]:
        return [(self._split(section)[1], key, value)]  # the file is chosen by target_file()

    def normalize_game_id(self, game_id: str) -> str:
        gid = game_id.strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{3,6}", gid):
            raise ValueError(f"{game_id!r} isn't a Dolphin game ID (6 characters like GM4E01, or a 3-char prefix)")
        return gid

    def game_config_file(self, game_id: str) -> Path:
        return self.config_dir().parent / "GameSettings" / f"{game_id}.ini"

    def list_games(self, query: str = "") -> list[dict[str, Any]]:
        from .ini_store import IniDocument

        cfg = self.config_dir() / "Dolphin.ini"
        if not cfg.exists():
            return []
        general = IniDocument.load(cfg).section("General")
        recursive = general.get("RecursiveISOPaths", "False").lower() == "true"
        folders = [(Path(v), recursive) for k, v in general.items() if re.fullmatch(r"ISOPath\d+", k)]
        return self._scan_games(folders, read_gc_wii_id, query)

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return ["-b", "-e", game] if game else []


class RPCS3(EmulatorAdapter):
    """YAML config: section = top-level key ('Video'), key = nested key ('Resolution Scale')."""
    id, display_name = "rpcs3", "RPCS3"
    process_names = ("rpcs3.exe",)
    game_file_must_exist = True
    KNOWN_SETTINGS = (
        K("Video", "Renderer", "Vulkan, OpenGL, Null"),
        K("Video", "Resolution Scale", "Percent of 720p: 150 ~1080p, 200 ~1440p, 300 ~4K"),
        K("Video", "Anisotropic Filter Override", "0, 2, 4, 8, 16"),
        K("Video", "Output Scaling Mode", "Bilinear, Nearest, FidelityFX Super Resolution" + _V),
        K("Video", "Frame limit", "Auto, Off, 30, 50, 60, ..."),
        K("Video", "VSync", "true/false"),
        K("Miscellaneous", "Start games in fullscreen mode", "true/false"),
    )
    GUIDES = {"controllers": RPCS3_CONTROLLERS, "upscaling": RPCS3_UPSCALING, "per_game": RPCS3_PER_GAME}
    notes = ("Per-game: config/custom_configs/config_<SERIAL>.yml (create it in RPCS3 first). Pads live in "
             "config/input_configs (searched too). Games are installed via the GUI.")

    def root(self) -> Path | None:
        exe = self.find_exe()
        return exe.parent if exe else None

    def config_candidates(self) -> list[Path]:
        root = self.root()
        if not root:
            return []
        return [root / "config" / "config.yml", root / "config.yml"]

    def extra_watch_files(self) -> list[Path]:
        root = self.root()
        if not root:
            return []
        return sorted((root / "config" / "input_configs").rglob("*.yml")) + sorted((root / "input_configs").rglob("*.yml"))

    def normalize_game_id(self, game_id: str) -> str:
        gid = game_id.strip().upper().replace("-", "").replace("_", "")
        if not re.fullmatch(r"[A-Z]{4}\d{5}", gid):
            raise ValueError(f"{game_id!r} isn't a PS3 serial (e.g. BLUS30443 or NPUB30024)")
        return gid

    def game_config_file(self, game_id: str) -> Path:
        root = self.root()
        if not root:
            raise FileNotFoundError("RPCS3 not found; use locate_emulator first")
        candidates = [root / "config" / "custom_configs" / f"config_{game_id}.yml",
                      root / "custom_configs" / f"config_{game_id}.yml"]
        return next((c for c in candidates if c.exists()), candidates[0])

    def list_games(self, query: str = "") -> list[dict[str, Any]]:
        root = self.root()
        games_yml = next((p for p in (root / "config" / "games.yml", root / "games.yml") if p.exists()), None) if root else None
        if games_yml is None:
            return []
        data = yaml.safe_load(games_yml.read_text(encoding="utf-8")) or {}
        q = query.lower()
        return [{"file": str(path), "id": serial} for serial, path in sorted(data.items())
                if q in str(serial).lower() or q in str(path).lower()][:150]

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

    GAME_EXTS = frozenset(SWITCH_GAME_EXTS)

    # ---- locations ------------------------------------------------------------
    def config_candidates(self) -> list[Path]:
        exe = self.find_exe()
        portable = [exe.parent / "user" / "config" / "qt-config.ini"] if exe else []
        roaming = env_path("APPDATA", "eden", "config", "qt-config.ini")
        return portable + ([roaming] if roaming else [])

    def config_dir(self) -> Path | None:
        found = next((p for p in self.config_candidates() if p.exists()), None)
        return found.parent if found else None

    def extra_watch_files(self) -> list[Path]:
        cfg = self.config_dir()
        return sorted((cfg / "input").glob("*.ini")) if cfg else []  # saved controller profiles

    # ---- per-game -------------------------------------------------------------
    def normalize_game_id(self, game_id: str) -> str:
        tid = game_id.strip().upper()
        if not re.fullmatch(r"[0-9A-F]{16}", tid):
            raise ValueError(f"{game_id!r} isn't a Switch title ID (16 hex digits, e.g. 0100F2C0115B6000). "
                             "Use list_games(eden, '<name>') or Eden's right-click -> Properties.")
        return tid

    def game_config_file(self, game_id: str) -> Path:
        cfg = self.config_dir()
        if cfg is None:
            raise FileNotFoundError("Eden's config folder wasn't found; launch Eden once or use locate_emulator")
        return cfg / "custom" / f"{game_id}.ini"

    def _expand(self, section: str, key: str, value: str) -> list[tuple[str, str, str | None]]:
        if self._game_file is not None:
            if value.strip().lower() == "global":  # drop the override, fall back to the global value
                return [(section, key, None), (section, f"{key}\\use_global", "true"),
                        (section, f"{key}\\default", None)]
            return [(section, key, value), (section, f"{key}\\use_global", "false"),
                    (section, f"{key}\\default", "false")]
        return [(section, key, value), (section, f"{key}\\default", "false")]

    def list_games(self, query: str = "") -> list[dict[str, Any]]:
        """Switch game files in Eden's configured game folders, with title IDs parsed from file names."""
        from .ini_store import IniDocument

        cfg = self.config_path()
        if cfg is None:
            return []
        ui = IniDocument.load(cfg).section("UI")
        folders = [(Path(v.strip('"')), True) for k, v in ui.items() if re.fullmatch(r"Paths\\gamedirs\\\d+\\path", k)]

        def title_id(f: Path) -> str | None:
            m = TITLE_ID.search(f.name)
            return m.group(1).upper() if m else None

        return self._scan_games(folders, title_id, query)

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return (["-f"] if fullscreen else []) + (["-g", game] if game else [])


from .retroarch import RetroArch  # noqa: E402  (separate module: it's the largest adapter)

BUILTIN: dict[str, type[EmulatorAdapter]] = {cls.id: cls for cls in (PCSX2, DuckStation, Dolphin, RPCS3, Eden, RetroArch)}
