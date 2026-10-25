"""Settings Claude has learned (by diffing a GUI change) for this PC's emulator versions.

Stored in %APPDATA%\\Retrozashi\\learned_settings.json and fed back through list_emulators, so the
second time you ask for "3x upscaling" it's a direct config write, not a GUI session.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LearnedSettings:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._data: dict[str, dict[str, dict[str, Any]]] = {}
        if path and path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))

    def remember(self, emulator: str, label: str, section: str, key: str, value: str, meaning: str = "") -> None:
        entry = {"section": section, "key": key, "value": value}
        if meaning:
            entry["meaning"] = meaning
        self._data.setdefault(emulator, {})[label.strip()] = entry
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def for_emulator(self, emulator: str) -> dict[str, dict[str, Any]]:
        return dict(self._data.get(emulator, {}))
