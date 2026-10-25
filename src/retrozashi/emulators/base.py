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

from . import backups


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

    # Raw access to one file. A value of None means "remove the key".
    @abstractmethod
    def _get_raw(self, path: Path, section: str, key: str) -> str | None: ...

    @abstractmethod
    def _set_raw(self, path: Path, writes: list[tuple[str, str, str | None]]) -> None: ...

    def _expand(self, section: str, key: str, value: str) -> list[tuple[str, str, str]]:
        """The raw writes one logical setting needs (e.g. Eden also sets `key\\default`)."""
        return [(section, key, value)]

    def target_file(self, section: str) -> Path | None:
        """The file a write to `section` lands in (multi-file emulators override this)."""
        return self.config_path()

    def config_files(self) -> list[Path]:
        """Every file this adapter may write: what backups and undo cover."""
        path = self.config_path()
        return [path] if path else []

    def _ensure_closed(self) -> None:
        if self.is_running():
            raise EmulatorBusy(f"{self.display_name} is running and would overwrite the change on exit. "
                               "Close it first (close_emulator), or change the setting through its GUI.")

    def apply_settings(self, settings: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
        """Write several settings with one backup per touched file. Returns every raw change
        ({file, section, key, old, new, backup}) so `revert_changes` can put back exactly these keys."""
        self._ensure_closed()
        by_file: dict[Path, list[tuple[str, str, str]]] = {}
        for section, key, value in settings:
            path = self.target_file(section)
            if path is None:
                raise FileNotFoundError(f"No {self.display_name} config file found. Launch it once so it creates one.")
            by_file.setdefault(path, []).extend(self._expand(section, key, value))
        changes: list[dict[str, Any]] = []
        for path, writes in by_file.items():
            bak = backups.backup_file(path) if path.exists() else None
            for s, k, v in writes:
                old = self._get_raw(path, s, k) if path.exists() else None
                changes.append({"file": str(path), "section": s, "key": k, "old": old, "new": v,
                                "backup": str(bak) if bak else None})
            self._set_raw(path, writes)
        return changes

    def revert_changes(self, changes: list[dict[str, Any]]) -> None:
        """Undo `apply_settings` key by key (newest first): anything else changed meanwhile, e.g. in
        the emulator's own menus during play, is kept. Keys that didn't exist before are removed."""
        self._ensure_closed()
        by_file: dict[Path, list[tuple[str, str, str | None]]] = {}
        for c in reversed(changes):
            by_file.setdefault(Path(c["file"]), []).append((c["section"], c["key"], c["old"]))
        for path, writes in by_file.items():
            if path.exists():
                backups.backup_file(path)
            self._set_raw(path, writes)

    def set_setting(self, section: str, key: str, value: str) -> dict[str, Any]:
        first = self.apply_settings([(section, key, value)])[0]
        return {**first, "section": section}

    def list_backups(self) -> list[Path]:
        """All backups for this emulator's config files, newest first."""
        found = [b for f in self.config_files() for b in backups.backups_for(f)]
        return sorted(found, key=backups.stamp_of, reverse=True)

    def restore_backup(self, backup: Path | None = None) -> dict[str, Any]:
        """Restore `backup` (default: the newest). Only this adapter's own backups are accepted."""
        self._ensure_closed()
        known = self.list_backups()
        if not known:
            raise FileNotFoundError(f"No {self.display_name} backups found.")
        if backup is None:
            backup = known[0]
        elif Path(backup) not in known:
            raise ValueError(f"{backup} is not a {self.display_name} config backup")
        snapshot = backups.restore(Path(backup))
        return {"restored": str(backups.original_of(Path(backup))), "from": str(backup),
                "previous_state_saved_as": str(snapshot) if snapshot else None}

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

    def _get_raw(self, path: Path, section: str, key: str) -> str | None:
        from .ini_store import IniDocument

        return IniDocument.load(path).get(section, key)

    def _set_raw(self, path: Path, writes: list[tuple[str, str, str | None]]) -> None:
        from .ini_store import IniDocument

        doc = IniDocument.load(path) if path.exists() else IniDocument()
        for section, key, value in writes:
            if value is None:
                doc.remove(section, key)
            else:
                doc.set(section, key, value)
        doc.save(path)
