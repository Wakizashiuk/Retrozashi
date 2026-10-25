"""Per-game profiles: a named set of emulator settings applied when a game is launched through
Retrozashi, and reverted when the emulator closes.

    launch("GT4") ──▶ apply settings (keys' old values recorded) ──▶ launch emulator + game
                                                                          │ watcher thread
    pending_reverts.json  ◀── persisted until reverted ───────────────────┤
                                                                          ▼
                       revert exactly those keys  ◀──  emulator process exits

Only the keys a profile set are put back, so changes you make in the emulator's own menus
during play survive. Pending reverts are saved to disk and recovered on the next start,
so a crash or closing Retrozashi mid-game never leaves profile settings behind.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import psutil

from .core.events import EventBus, EventKind
from .emulators.base import EmulatorAdapter, EmulatorBusy
from .emulators.registry import EmulatorRegistry


@dataclass
class Setting:
    section: str
    key: str
    value: str


@dataclass
class Profile:
    name: str
    emulator: str
    game_path: str = ""
    fullscreen: bool = True
    settings: list[Setting] = field(default_factory=list)
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        return cls(name=d["name"], emulator=d["emulator"], game_path=d.get("game_path", ""),
                   fullscreen=d.get("fullscreen", True), notes=d.get("notes", ""),
                   settings=[Setting(**s) for s in d.get("settings", [])])

    def summary(self) -> str:
        n = len(self.settings)
        game = Path(self.game_path).name if self.game_path else "no game"
        return f"{self.emulator} · {game} · {n} setting{'s' if n != 1 else ''}"


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _wait_for_exit(adapter: EmulatorAdapter, pid: int | None) -> None:
    """Block until the launched process and every other instance of the emulator are gone
    (some emulators hand off to a child process, and all must exit before config is final)."""
    if pid is not None:
        try:
            psutil.Process(pid).wait()
        except psutil.NoSuchProcess:
            pass
    while adapter.is_running():
        time.sleep(2)
    time.sleep(1)  # let the emulator finish writing its config on exit


class ProfileManager:
    def __init__(self, registry: EmulatorRegistry, bus: EventBus, store_path: Path, state_path: Path,
                 wait_for_exit: Callable[[EmulatorAdapter, int | None], None] = _wait_for_exit) -> None:
        self.registry = registry
        self.bus = bus
        self.store_path = store_path
        self.state_path = state_path
        self._wait = wait_for_exit
        self._lock = threading.Lock()
        self._profiles: list[Profile] = []
        self._pending: dict[str, dict[str, Any]] = {}  # emulator id -> {"profile", "changes"}
        if store_path.exists():
            self._profiles = [Profile.from_dict(d) for d in json.loads(store_path.read_text(encoding="utf-8"))]
        if state_path.exists():
            self._pending = json.loads(state_path.read_text(encoding="utf-8"))

    # ---- store --------------------------------------------------------------
    # Copies out, so editing a returned profile never changes the store until save() is called.
    def list(self) -> list[Profile]:
        return copy.deepcopy(self._profiles)

    def get(self, name: str) -> Profile:
        return copy.deepcopy(self._find(name))

    def _find(self, name: str) -> Profile:
        for p in self._profiles:
            if p.name.lower() == name.strip().lower():
                return p
        raise KeyError(f"No profile named {name!r}. Profiles: {', '.join(p.name for p in self._profiles) or 'none'}")

    def save(self, profile: Profile, replacing: str | None = None) -> None:
        """Add `profile`, or replace the one named `replacing` (which also handles renames)."""
        profile.name = profile.name.strip()
        if not profile.name:
            raise ValueError("Profile name can't be empty")
        self.registry.get(profile.emulator)  # raises KeyError for an unknown emulator
        clash = [p for p in self._profiles if p.name.lower() == profile.name.lower()
                 and (replacing is None or p.name.lower() != replacing.lower())]
        if clash:
            raise ValueError(f"A profile named {profile.name!r} already exists")
        if replacing is not None:
            self._profiles = [p for p in self._profiles if p.name.lower() != replacing.lower()]
        self._profiles.append(copy.deepcopy(profile))
        self._profiles.sort(key=lambda p: p.name.lower())
        self._persist_profiles()

    def delete(self, name: str) -> None:
        self._profiles.remove(self._find(name))
        self._persist_profiles()

    def _persist_profiles(self) -> None:
        _write_json(self.store_path, [asdict(p) for p in self._profiles])

    def _persist_pending(self) -> None:
        _write_json(self.state_path, self._pending)

    # ---- launch / revert ----------------------------------------------------
    def active(self) -> dict[str, str]:
        """emulator id -> name of the profile whose settings are currently applied."""
        with self._lock:
            return {emu: entry["profile"] for emu, entry in self._pending.items()}

    def launch(self, name: str) -> dict[str, Any]:
        profile = self.get(name)
        adapter = self.registry.get(profile.emulator)
        if adapter.is_running():
            raise EmulatorBusy(f"{adapter.display_name} is already running. Close it first, so the profile "
                               "can be applied and reverted cleanly.")
        self.revert(adapter.id)  # settings left over from a crash: put them back first
        changes = adapter.apply_settings([(s.section, s.key, s.value) for s in profile.settings])
        with self._lock:
            self._pending[adapter.id] = {"profile": profile.name, "changes": changes}
            self._persist_pending()
        try:
            pid = adapter.launch(profile.game_path or None, profile.fullscreen)
        except Exception:
            self.revert(adapter.id)  # don't leave the profile applied if the emulator never started
            raise
        self._watch(adapter, pid)
        self.bus.publish(EventKind.TOOL, f"Profile '{profile.name}': applied {len(profile.settings)} setting(s), "
                                         f"launched {adapter.display_name}. Settings revert when it closes.", ok=True)
        return {"profile": profile.name, "emulator": adapter.id, "pid": pid,
                "applied": [f"[{s.section}] {s.key} = {s.value}" for s in profile.settings],
                "revert": "automatic when the emulator closes"}

    def revert(self, emulator_id: str) -> bool:
        """Put back the keys a profile changed. Returns False if nothing was pending."""
        with self._lock:
            entry = self._pending.get(emulator_id)
            if entry is None:
                return False
            self.registry.get(emulator_id).revert_changes(entry["changes"])
            del self._pending[emulator_id]
            self._persist_pending()
        self.bus.publish(EventKind.TOOL, f"Profile '{entry['profile']}': previous settings restored.", ok=True)
        return True

    def recover(self) -> None:
        """At startup: revert anything left pending by a crash, or watch an emulator still running it."""
        for emulator_id in list(self.active()):
            adapter = self.registry.get(emulator_id)
            if adapter.is_running():
                self._watch(adapter, None)
            else:
                try:
                    self.revert(emulator_id)
                except Exception as e:
                    self.bus.publish(EventKind.ERROR, f"Couldn't restore {adapter.display_name} settings: {e}")

    def _watch(self, adapter: EmulatorAdapter, pid: int | None) -> None:
        def run() -> None:
            self._wait(adapter, pid)
            try:
                self.revert(adapter.id)
            except Exception as e:  # e.g. the user relaunched the emulator in the meantime
                self.bus.publish(EventKind.ERROR, f"Couldn't restore {adapter.display_name} settings: {e}. "
                                                  "They'll be restored the next time Retrozashi starts.")

        threading.Thread(target=run, name=f"ProfileWatch-{adapter.id}", daemon=True).start()
