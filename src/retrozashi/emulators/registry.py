"""Adapter registry: the built-ins, plus any you register for other apps (RetroArch, Cemu, ...).

Exe locations found by `locate_emulator` are remembered in %APPDATA%\\Retrozashi\\emulator_paths.json;
paths from settings.toml win over remembered ones.
"""
from __future__ import annotations

import json
from pathlib import Path

from .base import EmulatorAdapter
from .builtin import BUILTIN


class EmulatorRegistry:
    def __init__(self, exe_overrides: dict[str, str] | None = None, remembered_paths: Path | None = None) -> None:
        self._remembered_file = remembered_paths
        remembered = {}
        if remembered_paths and remembered_paths.exists():
            remembered = json.loads(remembered_paths.read_text(encoding="utf-8"))
        overrides = {**remembered, **(exe_overrides or {})}
        self._adapters: dict[str, EmulatorAdapter] = {
            key: cls(overrides.get(key)) for key, cls in BUILTIN.items()
        }

    def register(self, adapter: EmulatorAdapter) -> None:
        self._adapters[adapter.id] = adapter

    def get(self, emulator_id: str) -> EmulatorAdapter:
        try:
            return self._adapters[emulator_id.lower()]
        except KeyError:
            raise KeyError(f"Unknown emulator {emulator_id!r}. Known: {', '.join(self._adapters)}") from None

    def ids(self) -> list[str]:
        return list(self._adapters)

    def remember_exe(self, emulator_id: str, path: Path) -> None:
        adapter = self.get(emulator_id)
        adapter.set_exe(path)
        if self._remembered_file is None:
            return
        data = {}
        if self._remembered_file.exists():
            data = json.loads(self._remembered_file.read_text(encoding="utf-8"))
        data[adapter.id] = str(path)
        self._remembered_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
