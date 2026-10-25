"""Controller sets: one saved controller mapping per emulator per pad, switched automatically.

Over Moonlight, emulators see Vibepollo's virtual pad, whose type follows the client pad in Auto
mode. Emulators that bind to a specific device (Eden by GUID, Dolphin by name, RPCS3 by handler)
lose their mapping when the type changes. So during setup the user maps each pad once and saves
it as a set; at every stream start `sync()` detects the connected pads and puts each emulator's
matching set back. Sets are written globally, with a backup, like any other config change.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .emulators import backups
from .emulators.base import EmulatorBusy
from .emulators.inspect import read_flat
from .emulators.registry import EmulatorRegistry


class ControllerSets:
    def __init__(self, registry: EmulatorRegistry, path: Path | None) -> None:
        self.registry = registry
        self.path = path
        self._data: dict[str, Any] = {"sets": {}, "active": {}}
        if path and path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))

    def _persist(self) -> None:
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
            tmp.replace(self.path)

    # ---- save / list -----------------------------------------------------------------
    def save(self, emulator: str, name: str, pads: list[str]) -> dict[str, Any]:
        """Capture the emulator's current controller mapping as set `name`, tied to `pads`."""
        adapter = self.registry.get(emulator)
        if not pads:
            raise ValueError("No controller is connected. Start a Moonlight session with the pad (so Vibepollo "
                             "creates its virtual pad) or plug it in, then save again.")
        files: dict[str, str] = {}
        keys: list[list[str]] = []
        for path, keep in adapter.controller_scope():
            if not path.exists():
                continue
            if keep is None:
                files[str(path)] = path.read_bytes().decode("utf-8", errors="replace")
            else:
                keys += [[f, s, k, v] for (f, s, k), v in read_flat(path).items() if keep(s, k)]
        if not files and not keys:
            raise ValueError(f"No controller mapping found for {adapter.display_name}. Map the pad first "
                             f"(emulator_guide {adapter.id} controllers).")
        self._data["sets"].setdefault(adapter.id, {})[name.strip()] = {
            "pads": sorted(set(pads)), "files": files, "keys": keys, "saved": time.time()}
        self._data["active"][adapter.id] = name.strip()
        self._persist()
        return {"emulator": adapter.id, "set": name.strip(), "pads": sorted(set(pads)),
                "captured": {"files": [Path(f).name for f in files], "keys": len(keys)}}

    def list(self) -> dict[str, Any]:
        return {emu: {"sets": {n: {"pads": s["pads"]} for n, s in sets.items()},
                      "active": self._data["active"].get(emu)}
                for emu, sets in self._data["sets"].items()}

    def delete(self, emulator: str, name: str) -> None:
        emu = self.registry.get(emulator).id
        self._data["sets"].get(emu, {}).pop(name, None)
        if self._data["active"].get(emu) == name:
            self._data["active"].pop(emu)
        self._persist()

    # ---- apply ----------------------------------------------------------------------
    def apply(self, emulator: str, name: str) -> dict[str, Any]:
        adapter = self.registry.get(emulator)
        saved = self._data["sets"].get(adapter.id, {}).get(name)
        if saved is None:
            raise KeyError(f"No controller set {name!r} for {adapter.display_name}. Sets: "
                           f"{', '.join(self._data['sets'].get(adapter.id, {})) or 'none'}")
        adapter._ensure_closed()  # it would overwrite the mapping on exit
        scope = {str(p): keep for p, keep in adapter.controller_scope()}
        for file, content in saved["files"].items():
            path = Path(file)
            if path.exists():
                backups.backup_file(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        by_file: dict[str, dict[tuple[str, str], str]] = {}
        for f, s, k, v in saved["keys"]:
            by_file.setdefault(f, {})[(s, k)] = v
        for file, wanted in by_file.items():
            path = Path(file)
            keep = scope.get(file)
            # Remove mapping keys the set doesn't have (e.g. buttons bound only for the other pad).
            stale = [(s, k) for (_f, s, k) in read_flat(path) if keep and keep(s, k) and (s, k) not in wanted] \
                if path.exists() else []
            if path.exists():
                backups.backup_file(path)
            adapter._set_raw(path, [(s, k, v) for (s, k), v in wanted.items()] + [(s, k, None) for s, k in stale])
        self._data["active"][adapter.id] = name
        self._persist()
        return {"emulator": adapter.id, "applied": name}

    def choose(self, emulator: str, pads: list[str]) -> str | None:
        """The set whose pads best match the connected ones (newest wins a tie)."""
        present = set(pads)
        best, best_key = None, (0, 0.0)
        for name, s in self._data["sets"].get(self.registry.get(emulator).id, {}).items():
            key = (len(present & set(s["pads"])), s.get("saved", 0.0))
            if key[0] and key > best_key:
                best, best_key = name, key
        return best

    def sync(self, pads: list[str]) -> list[dict[str, Any]]:
        """Switch every emulator with saved sets to the one matching the connected pads."""
        results = []
        for emu in self._data["sets"]:
            name = self.choose(emu, pads)
            if name is None:
                results.append({"emulator": emu, "result": "no set matches the connected pads"})
            elif self._data["active"].get(emu) == name:
                results.append({"emulator": emu, "result": f"already using '{name}'"})
            else:
                try:
                    self.apply(emu, name)
                    results.append({"emulator": emu, "result": f"switched to '{name}'"})
                except EmulatorBusy:
                    results.append({"emulator": emu, "result": f"running, left as is (wanted '{name}')"})
                except (OSError, ValueError, KeyError) as e:
                    results.append({"emulator": emu, "result": f"failed: {e}"})
        return results

    def known_pads(self) -> set[str]:
        return {p for sets in self._data["sets"].values() for s in sets.values() for p in s["pads"]}
