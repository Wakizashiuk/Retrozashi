"""Native tools Claude can call, in order of preference:
  1. config tools: exact, reversible (.bak), work with the emulator closed;
  2. UI Automation tools: operate dialogs by control name, verify via returned state;
  3. (agent/loop.py) Computer Use pixels: the universal fallback."""
from __future__ import annotations

import json
from typing import Any

from ..desktop.uia import ACTIONS, UiaController, UiaError
from ..emulators.base import EmulatorBusy
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

NATIVE_TOOLS: list[dict[str, Any]] = [
    _tool("list_emulators",
          "List supported emulators with exe path, config file path, whether running, and the config "
          "keys Retrozashi knows. Call this first in any emulator task.", {}),
    _tool("read_emulator_config",
          "Read an emulator's current config. Always read before writing so you see real key names and values.",
          {"emulator": _EMU, "section": {"type": "string", "description": "Section name, or '' for all sections"}}),
    _tool("set_emulator_setting",
          "Change one config value on disk (a timestamped .bak is kept). Fails if the emulator is running, "
          "because it would overwrite the change on exit; close it first or use the GUI instead.",
          {"emulator": _EMU, "section": {"type": "string"}, "key": {"type": "string"},
           "value": {"type": "string", "description": "New value exactly as it should appear in the file"}}),
    _tool("launch_emulator",
          "Start an emulator, optionally booting a game. Use '' for game_path to open just the frontend.",
          {"emulator": _EMU, "game_path": {"type": "string"}, "fullscreen": {"type": "boolean"}}),
    _tool("close_emulator", "Gracefully close a running emulator so it flushes its config.", {"emulator": _EMU}),
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
    def __init__(self, registry: EmulatorRegistry, uia: UiaController | None = None) -> None:
        self.registry = registry
        self.uia = uia

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return NATIVE_TOOLS + (UIA_TOOLS if self.uia else [])

    def run(self, name: str, inp: dict[str, Any]) -> tuple[str, bool]:
        """Returns (result text, is_error)."""
        try:
            if name == "list_emulators":
                out: Any = [self.registry.get(i).describe() for i in self.registry.ids()]
            elif name == "read_emulator_config":
                out = self.registry.get(inp["emulator"]).read_settings(inp.get("section") or None)
                if not out:
                    out = "No config file found (launch the emulator once to create it)."
            elif name == "set_emulator_setting":
                out = self.registry.get(inp["emulator"]).set_setting(inp["section"], inp["key"], inp["value"])
            elif name == "launch_emulator":
                pid = self.registry.get(inp["emulator"]).launch(inp.get("game_path") or None,
                                                                bool(inp.get("fullscreen")))
                out = {"pid": pid, "hint": "Wait a few seconds, then take a screenshot to confirm it opened."}
            elif name == "close_emulator":
                self.registry.get(inp["emulator"]).close()
                out = "Closed."
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
