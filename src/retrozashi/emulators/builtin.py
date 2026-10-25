"""Built-in adapters. Key names and enum values below are a starting point; they drift between
emulator releases, so the agent always reads the live file first and can fall back to the GUI.
"""
from __future__ import annotations

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

    def _write_setting(self, path: Path, section: str, key: str, value: str) -> str | None:
        return super()._write_setting(path, self._split(section)[1], key, value)

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

    def _write_setting(self, path: Path, section: str, key: str, value: str) -> str | None:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        node = data.setdefault(section, {})
        old = node.get(key)
        node[key] = yaml.safe_load(value) if value.strip() else value  # "300" -> 300, "true" -> True
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
        return None if old is None else str(old)

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return ["--no-gui", game] if game else []


class Eden(IniEmulator):
    """Qt-settings INI: every changed key also needs `key\\default=false` or Eden ignores it."""
    id, display_name = "eden", "Eden"
    process_names = ("eden.exe",)
    KNOWN_SETTINGS = (
        K("Renderer", "backend", "Graphics API enum (OpenGL / Vulkan): verify by reading after a GUI change"),
        K("Renderer", "resolution_setup", "Resolution scaling enum: prefer the GUI (Computer Use) until verified"),
        K("Renderer", "use_vsync", "VSync mode enum"),
        K("UI", "fullscreen", "true/false"),
    )
    notes = ("Enum values change between builds. Safe pattern: read the file, change one option in the "
             "GUI with Computer Use, re-read to learn the value, then reuse it via config writes.")

    def config_candidates(self) -> list[Path]:
        exe = self.find_exe()
        portable = [exe.parent / "user" / "config" / "qt-config.ini"] if exe else []
        roaming = env_path("APPDATA", "eden", "config", "qt-config.ini")
        return portable + ([roaming] if roaming else [])

    def _write_setting(self, path: Path, section: str, key: str, value: str) -> str | None:
        from .ini_store import IniDocument

        doc = IniDocument.load(path) if path.exists() else IniDocument()
        old = doc.set(section, key, value)
        doc.set(section, f"{key}\\default", "false")
        doc.save(path)
        return old

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return (["-f"] if fullscreen else []) + (["-g", game] if game else [])


BUILTIN: dict[str, type[EmulatorAdapter]] = {cls.id: cls for cls in (PCSX2, DuckStation, Dolphin, RPCS3, Eden)}
