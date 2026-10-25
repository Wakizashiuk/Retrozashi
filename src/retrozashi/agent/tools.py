"""Native (non-GUI) tools Claude can call. These are preferred over Computer Use whenever
an adapter covers the setting: they're faster, exact, and reversible (.bak files)."""
from __future__ import annotations

import json
from typing import Any

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


class NativeTools:
    def __init__(self, registry: EmulatorRegistry) -> None:
        self.registry = registry

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
            else:
                return f"Unknown tool {name}", True
            return (out if isinstance(out, str) else json.dumps(out, indent=1, default=str)), False
        except (EmulatorBusy, FileNotFoundError, KeyError, ValueError, OSError) as e:
            return f"{type(e).__name__}: {e}", True
