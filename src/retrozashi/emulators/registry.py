"""Adapter registry: the built-ins, plus any you register for other apps (RetroArch, Cemu, ...)."""
from __future__ import annotations

from .base import EmulatorAdapter
from .builtin import BUILTIN


class EmulatorRegistry:
    def __init__(self, exe_overrides: dict[str, str] | None = None) -> None:
        overrides = exe_overrides or {}
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
