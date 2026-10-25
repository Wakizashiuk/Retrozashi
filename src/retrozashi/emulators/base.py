"""The emulator integration framework.

Each adapter knows three things about one app:
  1. Where it lives: the exe, its process name, and its config file (incl. portable mode).
  2. How to change it without the GUI: config keys it understands, and CLI launch args.
  3. What the agent should know: `KNOWN_SETTINGS` is surfaced to Claude so it edits the
     right keys instead of guessing, and falls back to Computer Use for anything missing.
"""
from __future__ import annotations

import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil


@dataclass(frozen=True)
class KnownSetting:
    section: str
    key: str
    meaning: str


def env_path(var: str, *parts: str) -> Path | None:
    base = os.environ.get(var)
    return Path(base, *parts) if base else None


def documents_dir() -> Path:
    # Respect OneDrive-redirected Documents, which is common on Windows 11.
    for p in (env_path("OneDrive", "Documents"), Path.home() / "Documents"):
        if p and p.exists():
            return p
    return Path.home() / "Documents"


class EmulatorBusy(Exception):
    """Config edits are refused while the emulator runs: most rewrite their config on exit."""


class EmulatorAdapter(ABC):
    id: str = ""
    display_name: str = ""
    process_names: tuple[str, ...] = ()
    default_install_dirs: tuple[str, ...] = ()
    KNOWN_SETTINGS: tuple[KnownSetting, ...] = ()
    notes: str = ""

    def __init__(self, exe_override: str | None = None) -> None:
        self._exe_override = Path(exe_override) if exe_override else None

    # ---- discovery ----------------------------------------------------------
    def find_exe(self) -> Path | None:
        if self._exe_override and self._exe_override.exists():
            return self._exe_override
        for proc in psutil.process_iter(["name", "exe"]):
            if (proc.info["name"] or "").lower() in self.process_names and proc.info["exe"]:
                return Path(proc.info["exe"])
        for d in self.default_install_dirs:
            for name in self.process_names:
                p = Path(os.path.expandvars(d)) / name
                if p.exists():
                    return p
        return None

    @abstractmethod
    def config_candidates(self) -> list[Path]:
        """Ordered candidate config paths; portable (next-to-exe) locations first."""

    def config_path(self) -> Path | None:
        return next((p for p in self.config_candidates() if p.exists()), None)

    # ---- process management -------------------------------------------------
    def running_processes(self) -> list[psutil.Process]:
        return [p for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() in self.process_names]

    def is_running(self) -> bool:
        return bool(self.running_processes())

    def launch_args(self, game: str | None, fullscreen: bool) -> list[str]:
        return [game] if game else []

    def launch(self, game: str | None = None, fullscreen: bool = False) -> int:
        exe = self.find_exe()
        if not exe:
            raise FileNotFoundError(f"{self.display_name} executable not found; set it in settings.toml")
        proc = subprocess.Popen([str(exe), *self.launch_args(game, fullscreen)], cwd=exe.parent)
        return proc.pid

    def close(self, timeout: float = 10.0) -> None:
        procs = self.running_processes()
        for p in procs:
            p.terminate()  # WM_CLOSE-equivalent lets the emulator flush its config
        _, alive = psutil.wait_procs(procs, timeout=timeout)
        for p in alive:
            p.kill()

    # ---- config -------------------------------------------------------------
    @abstractmethod
    def read_settings(self, section: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def _write_setting(self, path: Path, section: str, key: str, value: str) -> str | None: ...

    def set_setting(self, section: str, key: str, value: str) -> dict[str, Any]:
        if self.is_running():
            raise EmulatorBusy(f"{self.display_name} is running and would overwrite the change on exit. "
                               "Close it first (close_emulator), or change the setting through its GUI.")
        path = self.config_path()
        if path is None:
            raise FileNotFoundError(f"No {self.display_name} config file found. Launch it once so it creates one.")
        old = self._write_setting(path, section, key, value)
        return {"file": str(path), "section": section, "key": key, "old": old, "new": value}

    def describe(self) -> dict[str, Any]:
        exe = self.find_exe()
        cfg = self.config_path()
        return {
            "id": self.id,
            "name": self.display_name,
            "exe": str(exe) if exe else None,
            "config_file": str(cfg) if cfg else None,
            "running": self.is_running(),
            "known_settings": [f"[{s.section}] {s.key}: {s.meaning}" for s in self.KNOWN_SETTINGS],
            "notes": self.notes,
        }


class IniEmulator(EmulatorAdapter):
    def read_settings(self, section: str | None = None) -> dict[str, Any]:
        from .ini_store import IniDocument

        path = self.config_path()
        if path is None:
            return {}
        doc = IniDocument.load(path)
        if section:
            return {section: doc.section(section)}
        return {s: doc.section(s) for s in doc.sections()}

    def _write_setting(self, path: Path, section: str, key: str, value: str) -> str | None:
        from .ini_store import IniDocument

        doc = IniDocument.load(path)
        old = doc.set(section, key, value)
        doc.save(path)
        return old
