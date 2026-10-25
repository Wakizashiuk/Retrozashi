"""Native tools Claude can call, in order of preference:
  1. config tools: exact, reversible (.bak), work with the emulator closed;
  2. UI Automation tools: operate dialogs by control name, verify via returned state;
  3. (agent/loop.py) Computer Use pixels: the universal fallback."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..desktop.screen import ScreenCapture, WindowTarget
from ..desktop.uia import ACTIONS, UiaController, UiaError
from ..emulators.backups import original_of
from ..emulators.base import EmulatorAdapter, EmulatorBusy
from ..emulators.inspect import diff, search, snapshot
from ..emulators.registry import EmulatorRegistry


def _tool(name: str, description: str, props: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": props, "required": list(props),
                         "additionalProperties": False},
    }


_EMU = {"type": "string", "description": "Emulator id from list_emulators, e.g. 'pcsx2'"}
_GAME = {"type": "string", "description": "Per-game override: the game's ID (Eden: 16-hex title ID), or '' for "
                                          "the global config"}
MAX_READ_CHARS = 40_000

NATIVE_TOOLS: list[dict[str, Any]] = [
    _tool("list_emulators",
          "List supported emulators with exe path, config file path, whether running, the config keys "
          "Retrozashi knows, settings learned on this PC (use these exact values), and available guides. "
          "Call this first in any emulator task.", {}),
    _tool("read_emulator_config",
          "Read an emulator's config: one section, everything, or every key/value matching `search` across "
          "ALL sections (e.g. 'resolution', 'player_0 button', 'vsync'). Read before writing so you use the "
          "real key names and values.",
          {"emulator": _EMU, "section": {"type": "string", "description": "Section name, or '' for all"},
           "search": {"type": "string", "description": "Words that must all appear in section/key/value; '' = no filter"},
           "game_id": _GAME}),
    _tool("set_emulator_setting",
          "Change one config value on disk, in any section (a timestamped .bak is kept). Fails if the emulator "
          "is running, because it would overwrite the change on exit; close it first or use the GUI instead.",
          {"emulator": _EMU, "section": {"type": "string"}, "key": {"type": "string"},
           "value": {"type": "string", "description": "New value exactly as it should appear in the file. "
                                                      "Eden per-game: 'global' removes the override"},
           "game_id": _GAME}),
    _tool("snapshot_config",
          "Remember the emulator's current config (all sections, plus saved input profiles), so diff_config can "
          "show exactly what a GUI change wrote. Use before changing an option in the emulator's own window "
          "whose key or value you don't know.",
          {"emulator": _EMU, "game_id": _GAME}),
    _tool("diff_config",
          "Show every key that changed since snapshot_config: file, section, key, before -> after. The emulator "
          "writes most settings when you press OK/Apply (some only on exit).",
          {"emulator": _EMU, "game_id": _GAME}),
    _tool("remember_setting",
          "Save a setting learned from a diff (e.g. label '3x resolution' = [Renderer] resolution_setup=5) so "
          "future requests can write it directly. Shown in list_emulators as learned_settings.",
          {"emulator": _EMU, "label": {"type": "string", "description": "What the option is called in the GUI"},
           "section": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"},
           "meaning": {"type": "string", "description": "Optional extra context, or ''"}}),
    _tool("emulator_guide",
          "Step-by-step guide for a task in an emulator's GUI (topics are listed under 'guides' in "
          "list_emulators, e.g. eden: controllers, upscaling, per_game). Read it before starting that task.",
          {"emulator": _EMU, "topic": {"type": "string"}}),
    _tool("locate_emulator",
          "Search this PC for an emulator's exe when list_emulators shows exe=null, and remember the location.",
          {"emulator": _EMU}),
    _tool("list_games",
          "List game files in the emulator's game folders with their IDs where they can be read from the file "
          "name (Eden: title IDs). Filter by part of the name, or '' for all.",
          {"emulator": _EMU, "query": {"type": "string"}}),
    _tool("list_config_backups",
          "List an emulator's config backups (one is made before every write), newest first.",
          {"emulator": _EMU, "game_id": _GAME}),
    _tool("restore_config_backup",
          "Undo config changes by restoring a backup from list_config_backups ('' = the newest). The current "
          "file is backed up first, so a restore can itself be undone. Fails while the emulator runs.",
          {"emulator": _EMU, "backup": {"type": "string", "description": "Full backup path, or '' for newest"},
           "game_id": _GAME}),
    _tool("launch_emulator",
          "Start an emulator, optionally booting a game. Use '' for game_path to open just the frontend.",
          {"emulator": _EMU, "game_path": {"type": "string"}, "fullscreen": {"type": "boolean"}}),
    _tool("close_emulator",
          "Gracefully close a running emulator so it flushes its config. The user is shown a confirmation "
          "dialog first; if they decline, make the change through the emulator's GUI instead.",
          {"emulator": _EMU}),
    _tool("list_profiles",
          "List the user's per-game profiles (made in the Profiles panel): name, emulator, game, settings, "
          "and which are currently applied.", {}),
    _tool("launch_profile",
          "Launch a game with its profile: applies the profile's settings, starts the emulator, and "
          "automatically restores the previous settings when the emulator closes. Use this when the user "
          "asks to play/launch/start a game that has a profile.",
          {"name": {"type": "string", "description": "Profile name from list_profiles (case-insensitive)"}}),
    _tool("focus_emulator_window",
          "Bring an emulator window to the front and point screenshots at just that window: sharper detail, "
          "and clicks are confined to it. Take a new screenshot afterwards (coordinates change). "
          "Use emulator '' to go back to capturing the whole monitor.",
          {"emulator": {"type": "string", "description": "Emulator id, or '' for whole-monitor capture"},
           "window_title_contains": {"type": "string", "description": "Pick e.g. the 'Settings' window; '' = main window"}}),
]

UIA_TOOLS: list[dict[str, Any]] = [
    _tool("ui_list_controls",
          "List the buttons, check boxes, combo boxes, text fields, tabs and menu items in a running emulator's "
          "windows (via Windows UI Automation), each with an id. Use this before screenshots/clicks when "
          "operating an emulator's settings dialogs: it is exact and needs no coordinates.",
          {"emulator": _EMU,
           "window_title_contains": {"type": "string", "description": "Filter windows by title, or '' for all"},
           "include_labels": {"type": "boolean", "description": "Also list static text labels (noisier)"}}),
    _tool("ui_act",
          "Act on a control from the latest ui_list_controls by id. Returns the control's new state; check it. "
          "click = press button / pick tab or item; toggle_on/toggle_off = set a check box; select = choose "
          "`value` in a combo box (or select a list/tab item); set_text = replace an edit field with `value`; "
          "expand/collapse = open/close a combo box or tree item; focus = bring a window (w#) or control to front; "
          "read = state only.",
          {"element_id": {"type": "string", "description": "e.g. 'e12' (control) or 'w0' (window)"},
           "action": {"type": "string", "enum": list(ACTIONS)},
           "value": {"type": "string", "description": "Item text for select, text for set_text, otherwise ''"}}),
]


class NativeTools:
    def __init__(self, registry: EmulatorRegistry, uia: UiaController | None = None,
                 screen: ScreenCapture | None = None, window_api=None, profiles=None,
                 learned=None, confirm=None) -> None:
        self.registry = registry
        self.profiles = profiles  # ProfileManager
        self.learned = learned  # LearnedSettings
        # Asks the user a yes/no question (the UI shows a dialog); None = no UI, allow.
        self.confirm = confirm
        self._snapshots: dict[tuple[str, str], Any] = {}
        self.uia = uia
        self.screen = screen
        self._window_api = window_api  # (list_windows, focus_window); win32 by default
        # One entry per user request: (emulator, game_id or "", backup) for each config write it made.
        # Drives the Undo button.
        self.journal: list[list[tuple[str, str, Path]]] = []

    def begin_run(self) -> None:
        self.journal.append([])

    def undo_last_run(self) -> list[dict[str, Any]]:
        """Restore each file the latest request changed to its state before that request."""
        while self.journal and not self.journal[-1]:
            self.journal.pop()
        if not self.journal:
            return []
        run = self.journal.pop()
        earliest: dict[Path, tuple[str, str, Path]] = {}
        for emulator, game, bak in run:  # recorded oldest first: keep the first backup per file
            earliest.setdefault(original_of(bak), (emulator, game, bak))
        restored = []
        for emu, game, bak in earliest.values():
            adapter = self.registry.get(emu)
            restored.append((adapter.for_game(game) if game else adapter).restore_backup(bak))
        return restored

    def _adapter(self, inp: dict[str, Any]) -> EmulatorAdapter:
        adapter = self.registry.get(inp["emulator"])
        game = (inp.get("game_id") or "").strip()
        return adapter.for_game(game) if game else adapter

    def _read(self, inp: dict[str, Any]) -> Any:
        adapter = self._adapter(inp)
        game = (inp.get("game_id") or "").strip()
        if inp.get("search"):
            hits = search(snapshot(adapter.watch_files()), inp["search"])
            return hits or f"Nothing matching {inp['search']!r} in {adapter.display_name}'s config."
        out = adapter.read_settings(inp.get("section") or None)
        if not out:
            return (f"No overrides for game {game} yet." if game
                    else "No config file found (launch the emulator once to create it, or locate_emulator).")
        text = json.dumps(out, indent=1, default=str)
        if len(text) > MAX_READ_CHARS:  # huge configs: give the map, not the whole territory
            return {"too_large": f"{len(text)} chars", "sections": {k: len(v) if isinstance(v, dict) else 1
                                                                    for k, v in out.items()},
                    "hint": "Read one section, or use search."}
        return out

    def _close(self, inp: dict[str, Any]) -> str:
        adapter = self.registry.get(inp["emulator"])
        if not adapter.is_running():
            return f"{adapter.display_name} isn't running."
        if self.confirm and not self.confirm(f"Retrozashi wants to close {adapter.display_name} so it can save "
                                             "config changes.\n\nAny unsaved game progress will be lost. Close it?"):
            raise ValueError(f"The user chose not to close {adapter.display_name}. Make the change through its "
                             "GUI instead (e.g. Configure / Properties), or ask them to close it.")
        adapter.close()
        return "Closed."

    def _locate(self, inp: dict[str, Any]) -> Any:
        adapter = self.registry.get(inp["emulator"])
        found = adapter.locate()
        if not found:
            return (f"Couldn't find {', '.join(adapter.process_names)} in the usual places. Ask the user where "
                    f"{adapter.display_name} is installed; they can set it in settings.toml [emulator_paths].")
        self.registry.remember_exe(adapter.id, found[0])
        cfg = adapter.config_path()
        return {"using": str(found[0]), "other_copies": [str(p) for p in found[1:5]],
                "config_file": str(cfg) if cfg else None}

    def _focus(self, emulator: str, title_contains: str) -> Any:
        if self.screen is None:
            raise ValueError("Window capture is not available")
        if not emulator:
            self.screen.set_window(None)
            return f"Capturing {self.screen.describe_target()}. Take a screenshot."
        if self._window_api:
            list_windows, focus_window = self._window_api
        else:
            from ..desktop.win32 import focus_window, list_windows
        adapter = self.registry.get(emulator)
        pids = {p.pid for p in adapter.running_processes()}
        if not pids:
            raise ValueError(f"{adapter.display_name} is not running. Launch it first.")
        windows = list_windows(pids)
        matches = [w for w in windows if title_contains.lower() in w[1].lower()]
        if not matches:
            raise ValueError(f"No {adapter.display_name} window matching {title_contains!r}. "
                             f"Open windows: {[t for _, t in windows]}")
        hwnd, title = matches[0]  # EnumWindows is z-ordered: the topmost match wins
        focused = focus_window(hwnd)
        self.screen.set_window(WindowTarget(hwnd, title, adapter.id))
        return {"capturing": title, "foreground": focused,
                "hint": "Take a screenshot now; coordinates are relative to this window."}

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return NATIVE_TOOLS + (UIA_TOOLS if self.uia else [])

    def run(self, name: str, inp: dict[str, Any]) -> tuple[str, bool]:
        """Returns (result text, is_error)."""
        try:
            if name == "list_emulators":
                out: Any = []
                for i in self.registry.ids():
                    info = self.registry.get(i).describe()
                    if self.learned and (learned := self.learned.for_emulator(i)):
                        info["learned_settings"] = learned
                    out.append(info)
            elif name == "read_emulator_config":
                out = self._read(inp)
            elif name == "set_emulator_setting":
                out = self._adapter(inp).set_setting(inp["section"], inp["key"], inp["value"])
                if out.get("backup"):
                    if not self.journal:
                        self.begin_run()
                    adapter = self._adapter(inp)
                    self.journal[-1].append((adapter.id, adapter._game_id or "", Path(out["backup"])))
            elif name == "list_config_backups":
                baks = self._adapter(inp).list_backups()[:20]
                out = [{"backup": str(b), "file": original_of(b).name} for b in baks] or "No backups yet."
            elif name == "restore_config_backup":
                out = self._adapter(inp).restore_backup(Path(inp["backup"]) if inp.get("backup") else None)
            elif name == "launch_emulator":
                pid = self.registry.get(inp["emulator"]).launch(inp.get("game_path") or None,
                                                                bool(inp.get("fullscreen")))
                out = {"pid": pid, "hint": "Wait a few seconds, then take a screenshot to confirm it opened."}
            elif name == "close_emulator":
                out = self._close(inp)
            elif name == "snapshot_config":
                adapter = self._adapter(inp)
                snap = snapshot(adapter.watch_files())
                self._snapshots[(adapter.id, inp.get("game_id") or "")] = snap
                out = {"keys": len(snap), "files": [p.name for p in adapter.watch_files()],
                       "next": "Make the change (GUI: press OK/Apply), then diff_config."}
            elif name == "diff_config":
                adapter = self._adapter(inp)
                before = self._snapshots.get((adapter.id, inp.get("game_id") or ""))
                if before is None:
                    raise ValueError("No snapshot yet: call snapshot_config first.")
                changes = diff(before, snapshot(adapter.watch_files()))
                out = {"changes": changes, "hint": "remember_setting for options worth reusing."} if changes else (
                    "No changes yet. Did the GUI change get applied (OK/Apply)? Some emulators only write on exit.")
            elif name == "remember_setting":
                if self.learned is None:
                    raise ValueError("Learning is not available")
                self.learned.remember(self.registry.get(inp["emulator"]).id, inp["label"], inp["section"],
                                      inp["key"], inp["value"], inp.get("meaning", ""))
                out = f"Remembered '{inp['label']}'."
            elif name == "emulator_guide":
                adapter = self.registry.get(inp["emulator"])
                guide = adapter.GUIDES.get(inp["topic"].strip().lower())
                out = guide or f"No guide {inp['topic']!r} for {adapter.display_name}. Available: {sorted(adapter.GUIDES) or 'none'}"
            elif name == "locate_emulator":
                out = self._locate(inp)
            elif name == "list_games":
                adapter = self.registry.get(inp["emulator"])
                if not hasattr(adapter, "list_games"):
                    raise ValueError(f"Game listing isn't supported for {adapter.display_name} yet")
                out = adapter.list_games(inp.get("query", "")) or "No matching games in the configured game folders."
            elif name == "list_profiles":
                if self.profiles is None:
                    raise ValueError("Profiles are not available")
                active = self.profiles.active()
                out = [{"name": p.name, "emulator": p.emulator, "game": p.game_path or None,
                        "settings": [f"[{s.section}] {s.key} = {s.value}" for s in p.settings],
                        "applied_now": active.get(p.emulator) == p.name, "notes": p.notes or None}
                       for p in self.profiles.list()] or "No profiles yet. The user creates them in the Profiles tab."
            elif name == "launch_profile":
                if self.profiles is None:
                    raise ValueError("Profiles are not available")
                out = self.profiles.launch(inp["name"])
            elif name == "focus_emulator_window":
                out = self._focus(inp["emulator"], inp.get("window_title_contains", ""))
            elif name == "ui_list_controls" and self.uia:
                out = self.uia.list_controls(inp["emulator"], inp.get("window_title_contains", ""),
                                             bool(inp.get("include_labels")))
            elif name == "ui_act" and self.uia:
                out = self.uia.act(inp["element_id"], inp["action"], inp.get("value", ""))
            else:
                return f"Unknown tool {name}", True
            return (out if isinstance(out, str) else json.dumps(out, indent=1, default=str)), False
        except (EmulatorBusy, UiaError, FileNotFoundError, KeyError, ValueError, OSError) as e:
            return f"{type(e).__name__}: {e}", True
