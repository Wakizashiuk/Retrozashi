"""RetroArch: one frontend, many cores. Everything is flat `key = "value"` files:

    retroarch.cfg                               global settings (video, input, drivers, paths)
    retroarch-core-options.cfg                  core options, if global_core_options = "true"
    config/<Core>/<Core>.cfg | <Game>.cfg       core / game OVERRIDES of retroarch.cfg (partial files)
    config/<Core>/<Core>.opt | <Game>.opt       per-core / per-game CORE OPTIONS (3D upscaling lives here)
    config/remaps/<Core>/<Core>.rmp | <Game>.rmp controller REMAPS
    playlists/*.lpl                             game lists (JSON) incl. the core each game uses

game_id grammar (the `scope` of a per-game view):
    "Beetle PSX HW"                              core override
    "Beetle PSX HW/Crash Bandicoot (USA)"        game override
    "... [options]"                              core options for that scope ("[options]" alone = global file)
    "... [remap]"                                controller remap for that scope
<Core> is the core's library name (e.g. "Beetle PSX HW", "Mupen64Plus-Next", "Snes9x"); <Game> is the
content file name without extension. list_games returns ready-made ids.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .base import EmulatorAdapter, KnownSetting as K, env_path
from .cfg_store import CfgDocument

_V = " (verify with snapshot/diff on this build)"

RA_CONTROLLERS = """\
Controllers in RetroArch (DualSense/DS4, 8BitDo). RetroArch's menus aren't standard Windows controls,
so prefer config writes; use computer tools (keyboard: arrows, Enter, Backspace) for the menu.

Before you start
- Connect the controller before starting RetroArch. Close Steam or disable Steam Input for the pad
  (doubled inputs otherwise; Steam's own RetroArch build is especially affected).
- 8BitDo: X-input mode (Start+X) is recognised as an Xbox pad by every driver; Switch mode (Start+Y)
  works with sdl2.

Steps
1. Controller driver: input_joypad_driver = "sdl2" (best for DualSense/DS4: correct buttons,
   rumble) or "xinput" (8BitDo in X-input mode). Needs RetroArch closed (close_emulator) or
   Settings -> Drivers -> Controller, then restart RetroArch.
2. Autoconfig: input_autodetect_enable = "true". RetroArch ships profiles in autoconfig/<driver>/ for
   DualSense, DS4 and 8BitDo pads, so most pads work immediately with the right driver.
3. Player 1 device: input_player1_joypad_index = "0" (the first pad). Check in RetroArch:
   Settings -> Input -> Port 1 Controls -> Device Index shows the pad's name.
4. If the pad isn't recognised: Settings -> Input -> Port 1 Controls -> "Set All Controls", press each
   button, then "Save Controller Profile" (written to autoconfig/).
5. Menu hotkey so the user can open the menu from the pad:
   input_menu_toggle_gamepad_combo = "2" (L3 + R3; 4 = Start + Select)""" + _V + """.
6. Per-core / per-game button layouts are remaps: game_id "<Core> [remap]" or "<Core>/<Game> [remap]",
   keys like input_player1_btn_a = "<retropad button id>". Create one in the GUI first if unsure
   (Quick Menu -> Controls -> Manage Remap Files -> Save Core/Game Remap File), then diff.
Verify: read_emulator_config(retroarch, search="joypad") and search="player1".
"""

RA_UPSCALING = """\
Upscaling in RetroArch depends on the core.

3D cores: internal resolution is a CORE OPTION (in .opt files / retroarch-core-options.cfg). Keys and
values are the core's own, e.g.
  Beetle PSX HW     beetle_psx_hw_internal_resolution  "1x(native)", "2x", "4x", "8x", "16x"
  SwanStation       swanstation_GPU_ResolutionScale    "1" .. "16"
  Mupen64Plus-Next  mupen64plus-rdp-plugin "parallel" + mupen64plus-parallel-rdp-upscaling "2x"/"4x",
                    or (GLideN64) mupen64plus-43screensize "1920x1440"
  Flycast           reicast_internal_resolution        "1920x1440", "2880x2160"
  PPSSPP            ppsspp_internal_resolution         "1920x1088", "2400x1360", ...
  Dolphin           dolphin_efb_scale                  "x2 (1280x1056)", ...
These change between core versions""" + _V + """. Reliable path:
1. read_emulator_config(retroarch, search="<core prefix> resolution", game_id="[options]") or with
   game_id "<Core> [options]": after the core has run once, its options and current values are there.
2. If the valid values are unknown: snapshot_config(retroarch, "<Core> [options]") -> in RetroArch
   (with a game running) Quick Menu -> Core Options -> change Internal Resolution -> diff -> remember.
3. Write with set_emulator_setting(retroarch, "", "<key>", "<value>", game_id="<Core> [options]") for
   all games on that core, or "<Core>/<Game> [options]" for one game. Close RetroArch first.

2D cores (SNES, Mega Drive, GBA...): there's nothing to upscale internally; for sharp, correctly
proportioned output set video_scale_integer = "true" (global, or in a core override) and optionally
a shader: video_shader_enable = "true" and pick a preset in Quick Menu -> Shaders -> Load
(e.g. crt/crt-royale.slangp or interpolation/sharp-bilinear.slangp) -> Save -> Save Core/Game Preset.

Global video: video_driver = "vulkan" (or "d3d11"/"glcore"), video_fullscreen = "true",
video_vsync = "true".
"""

RA_PER_GAME = """\
Per-game settings in RetroArch (all partial files: only the keys you set are overridden).
1. list_games(retroarch, "<name>") reads RetroArch's playlists and returns the game's core and
   ready-made ids: "<Core>/<Game>" for overrides, plus "[options]" / "[remap]" variants.
   If a game shows core: null, it was added with "DETECT": ask which core to use, or load it once.
2. Settings overrides (video/audio/input settings from retroarch.cfg):
   game_id "<Core>/<Game>" -> config/<Core>/<Game>.cfg ; "<Core>" -> config/<Core>/<Core>.cfg
3. Core options (internal resolution, renderer, enhancements): "<Core>/<Game> [options]" or
   "<Core> [options]" -> .opt files. Note: if global_core_options = "true" RetroArch ignores per-core
   .opt files and uses retroarch-core-options.cfg (game_id "[options]").
4. Controller remaps: "<Core>/<Game> [remap]" / "<Core> [remap]" -> config/remaps/<Core>/...
5. Section is ignored for RetroArch (files are flat): pass "".
RetroArch must be closed to write (it saves its config on exit).
"""


def _resolve(base: Path, value: str | None, default: str) -> Path:
    """RetroArch path settings: ':\\x' (or ':/x') is relative to the RetroArch folder; 'default'/empty -> default."""
    v = (value or "").strip()
    if not v or v == "default":
        v = default
    if v.startswith(":"):
        return base.joinpath(*[p for p in re.split(r"[\\/]", v[1:]) if p])
    return Path(v)


class RetroArch(EmulatorAdapter):
    id, display_name = "retroarch", "RetroArch"
    process_names = ("retroarch.exe",)
    default_install_dirs = (r"C:\RetroArch-Win64", r"%ProgramFiles%\RetroArch",
                            r"%ProgramFiles(x86)%\Steam\steamapps\common\RetroArch")
    KNOWN_SETTINGS = (
        K("", "video_driver", "vulkan, d3d11, d3d12, glcore, gl"),
        K("", "video_fullscreen", "true/false"),
        K("", "video_scale_integer", "Integer scaling (sharp pixels): true/false"),
        K("", "aspect_ratio_index", "Aspect ratio preset index" + _V),
        K("", "video_shader_enable", "Shaders on/off: true/false"),
        K("", "video_vsync", "true/false"),
        K("", "input_joypad_driver", "sdl2 (DualSense/DS4), xinput, dinput"),
        K("", "input_autodetect_enable", "Auto-configure known pads: true/false"),
        K("", "input_player1_joypad_index", "Which pad is player 1: 0, 1, ..."),
        K("", "input_menu_toggle_gamepad_combo", "Menu from the pad: 2 = L3+R3, 4 = Start+Select" + _V),
        K("", "global_core_options", "true = one shared core options file, false = per-core .opt files"),
        K("", "config_save_on_exit", "Save retroarch.cfg on exit: true/false"),
        K("", "beetle_psx_hw_internal_resolution", "Core option (PS1, Beetle PSX HW): 1x(native)..16x" + _V),
        K("", "mupen64plus-parallel-rdp-upscaling", "Core option (N64, ParaLLEl-RDP): 1x, 2x, 4x, 8x" + _V),
        K("", "reicast_internal_resolution", "Core option (Dreamcast, Flycast): e.g. 1920x1440" + _V),
        K("", "ppsspp_internal_resolution", "Core option (PSP, PPSSPP): e.g. 1920x1088" + _V),
    )
    GUIDES = {"controllers": RA_CONTROLLERS, "upscaling": RA_UPSCALING, "per_game": RA_PER_GAME}
    notes = ("Flat key = \"value\" files: pass section \"\". Per-game/core overrides, core options and "
             "remaps via game_id (see emulator_guide retroarch per_game). Core options appear in config "
             "only after the core has run once. Menus aren't Qt: prefer config writes.")

    # ---- locations ------------------------------------------------------------
    def config_candidates(self) -> list[Path]:
        exe = self.find_exe()
        appdata = env_path("APPDATA", "RetroArch", "retroarch.cfg")
        return ([exe.parent / "retroarch.cfg"] if exe else []) + ([appdata] if appdata else [])

    def _global_cfg(self) -> Path | None:
        return next((p for p in self.config_candidates() if p.exists()), None)

    def base_dir(self) -> Path | None:
        exe = self.find_exe()
        if exe:
            return exe.parent
        cfg = self._global_cfg()
        return cfg.parent if cfg else None

    def _setting(self, key: str) -> str | None:
        cfg = self._global_cfg()
        return CfgDocument.load(cfg).get(key) if cfg else None

    def dir_for(self, key: str, default: str) -> Path:
        base = self.base_dir()
        if base is None:
            raise FileNotFoundError("RetroArch not found; use locate_emulator first")
        return _resolve(base, self._setting(key), default)

    def config_dir(self) -> Path:
        return self.dir_for("rgui_config_directory", ":\\config")

    def core_options_file(self) -> Path:
        base = self.base_dir()
        path = (self._setting("core_options_path") or "").strip()
        return _resolve(base, path, ":\\retroarch-core-options.cfg") if base else Path("retroarch-core-options.cfg")

    def extra_watch_files(self) -> list[Path]:
        if self.base_dir() is None:
            return []
        files = [self.core_options_file()]
        cfg_dir = self.config_dir()
        if cfg_dir.is_dir():
            files += sorted(cfg_dir.rglob("*.cfg")) + sorted(cfg_dir.rglob("*.opt")) + sorted(cfg_dir.rglob("*.rmp"))
        return [f for f in files if f.exists()]

    # ---- per-game / per-core ----------------------------------------------------
    _SCOPE = re.compile(r"^(?P<scope>.*?)\s*(?:\[(?P<kind>options|remap)\])?\s*$", re.IGNORECASE)

    def normalize_game_id(self, game_id: str) -> str:
        m = self._SCOPE.match(game_id.strip())
        scope = (m.group("scope") or "").strip().strip("/")
        kind = (m.group("kind") or "").lower()
        parts = [p.strip() for p in scope.split("/") if p.strip()] if scope else []
        if len(parts) > 2 or any(re.search(r'[<>:"|?*\\]', p) for p in parts):
            raise ValueError(f"{game_id!r} isn't a RetroArch scope. Use '<Core>', '<Core>/<Game>', optionally "
                             "followed by [options] or [remap] (see emulator_guide retroarch per_game).")
        if not parts and kind != "options":
            raise ValueError("A core name is required, e.g. 'Beetle PSX HW' or 'Beetle PSX HW/Crash Bandicoot (USA)'")
        return "/".join(parts) + (f" [{kind}]" if kind else "")

    def game_config_file(self, game_id: str) -> Path:
        m = self._SCOPE.match(game_id)
        parts = [p for p in (m.group("scope") or "").split("/") if p]
        kind = (m.group("kind") or "").lower()
        if kind == "options" and not parts:
            return self.core_options_file()
        core, name = parts[0], parts[-1]  # "<Core>" -> <Core>.ext ; "<Core>/<Game>" -> <Game>.ext
        if kind == "remap":
            return self.dir_for("input_remapping_directory", ":\\config\\remaps") / core / f"{name}.rmp"
        return self.config_dir() / core / f"{name}.{'opt' if kind == 'options' else 'cfg'}"

    # ---- games --------------------------------------------------------------------
    def _core_names(self) -> dict[str, str]:
        """core dll stem -> library name (from info/*.info 'corename')."""
        try:
            info_dir = self.dir_for("libretro_info_path", ":\\info")
        except FileNotFoundError:
            return {}
        names = {}
        for info in info_dir.glob("*.info") if info_dir.is_dir() else []:
            name = CfgDocument.load(info).get("corename")
            if name:
                names[info.stem.lower()] = name
        return names

    def list_games(self, query: str = "") -> list[dict[str, Any]]:
        playlists = self.dir_for("playlist_directory", ":\\playlists")
        if not playlists.is_dir():
            return []
        cores = self._core_names()
        q = query.lower()
        games = []
        for lpl in sorted(playlists.glob("*.lpl")):
            try:
                data = json.loads(lpl.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue  # very old plain-text playlists aren't supported
            default_core = data.get("default_core_path", "")
            for item in data.get("items", []):
                label, path = item.get("label", ""), item.get("path", "")
                if q and q not in label.lower() and q not in path.lower():
                    continue
                core_path = item.get("core_path") or ""
                if not core_path or core_path.upper() == "DETECT":  # "use the playlist's default core"
                    core_path = default_core
                core = None
                if core_path and core_path.upper() != "DETECT":
                    core = cores.get(Path(core_path.replace("\\", "/")).stem.lower())
                game = Path(path.split("#")[0].replace("\\", "/")).stem  # archives: name of the archive
                entry = {"label": label, "file": path, "playlist": lpl.stem, "core": core,
                         "core_path": core_path if core_path and core_path.upper() != "DETECT" else None}
                if core:
                    entry["id"] = f"{core}/{game}"
                    entry["options_id"] = f"{core}/{game} [options]"
                    entry["remap_id"] = f"{core}/{game} [remap]"
                games.append(entry)
                if len(games) >= 150:
                    return games
        return games

    # ---- config access (flat files: section is ignored) ------------------------
    def read_settings(self, section: str | None = None) -> dict[str, Any]:
        path = self.config_path()
        return {path.name: CfgDocument.load(path).items()} if path else {}

    def _expand(self, section: str, key: str, value: str) -> list[tuple[str, str, str]]:
        return [("", key, value)]

    def _get_raw(self, path: Path, section: str, key: str) -> str | None:
        value = CfgDocument.load(path).get(key)
        return None if value is None else f'"{value}"'  # stored quoted: reverts write it back verbatim

    def _set_raw(self, path: Path, writes: list[tuple[str, str, str | None]]) -> None:
        doc = CfgDocument.load(path) if path.exists() else CfgDocument()
        for _section, key, value in writes:
            if value is None:
                doc.remove(key)
            else:
                doc.set(key, value)
        doc.save(path)

    # ---- launching -----------------------------------------------------------------
    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        args = ["-f"] if fullscreen else []
        if not game:
            return args
        # RetroArch needs a core: take the one the playlists use for this file.
        match = next((g for g in self.list_games("") if Path(g["file"].split("#")[0]) == Path(game)
                      and g.get("core_path")), None)
        if match:
            core = _resolve(self.base_dir(), match["core_path"], match["core_path"])
            return args + ["-L", str(core), game]
        return args + [game]
