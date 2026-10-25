"""Retrozashi as an MCP server for the Claude desktop app: `Retrozashi.exe --mcp`.

The user chats with Claude in the Claude app (covered by their Claude plan, no API key) and Claude
calls Retrozashi's tools: config search/edit, per-game settings, guides, profiles, controller sets,
Vibepollo sync, UI Automation, and screen + input control for emulator windows.

A minimal MCP implementation (JSON-RPC 2.0, one message per line over stdin/stdout): initialize,
tools/list, tools/call and ping. Safety carries over: computer actions go through the same executor
(Ctrl+Alt+K kill switch, clamped clicks, stale-frame guard), and closing an emulator asks the user
in a Windows Yes/No box.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import IO, Any, Callable

from . import __version__
from .agent.prompts import SYSTEM_PROMPT
from .core.safety import AgentHalted

log = logging.getLogger("retrozashi.mcp")

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
SERVER_INFO = {"name": "retrozashi", "title": "Retrozashi", "version": __version__}

INSTRUCTIONS = SYSTEM_PROMPT.replace(
    "Requests often arrive by voice, so expect transcription slips", "Requests may contain typos or voice slips"
) + """
Running inside the Claude app (MCP): the computer tools below take the place of built-in computer use.
Call focus_emulator_window, then screenshot, and use the coordinates of that screenshot for click,
drag and scroll. If a computer tool reports that the user halted control (Ctrl+Alt+K), stop and ask
the user before calling resume_control.
"""

_XY = {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2,
       "description": "[x, y] in the latest screenshot's pixels"}
_MODS = {"type": "string", "description": "Modifier keys held during the action, e.g. 'ctrl' or 'ctrl+shift'; '' for none"}


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required if required is not None else list(props),
            "additionalProperties": False}


# (MCP tool name, executor member, description, input schema)
COMPUTER_TOOLS: list[tuple[str, str, str, dict[str, Any]]] = [
    ("screenshot", "screenshot", "Screenshot of the current capture target (a monitor, or the emulator window after "
     "focus_emulator_window). Coordinates for the other computer tools are in this image's pixels.", _schema({})),
    ("zoom", "zoom", "A higher-detail crop of a region of the latest screenshot. Coordinates stay in full-screenshot space.",
     _schema({"region": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4,
                         "description": "[x0, y0, x1, y1]"}})),
    ("click", "left_click", "Left-click at a point.", _schema({"coordinate": _XY, "modifiers": _MODS}, ["coordinate"])),
    ("double_click", "double_click", "Double-click at a point.", _schema({"coordinate": _XY}, ["coordinate"])),
    ("right_click", "right_click", "Right-click at a point.", _schema({"coordinate": _XY}, ["coordinate"])),
    ("move_mouse", "mouse_move", "Move the pointer (e.g. to reveal a tooltip or hover menu).",
     _schema({"coordinate": _XY})),
    ("drag", "left_click_drag", "Drag with the left button from one point to another (sliders, scroll bars).",
     _schema({"start_coordinate": _XY, "coordinate": _XY})),
    ("scroll", "scroll", "Scroll the mouse wheel at a point.",
     _schema({"coordinate": _XY, "scroll_direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
              "scroll_amount": {"type": "integer", "minimum": 1, "maximum": 30}})),
    ("type_text", "type", "Type text into the focused field.", _schema({"text": {"type": "string"}})),
    ("press_keys", "key", "Press a key or combo, e.g. 'Return', 'Escape', 'ctrl+s', 'alt+Tab', 'F11'.",
     _schema({"keys": {"type": "string"}, "repeat": {"type": "integer", "minimum": 1, "maximum": 100}}, ["keys"])),
    ("wait", "wait", "Wait for the screen to update (seconds).",
     _schema({"seconds": {"type": "number", "minimum": 0.1, "maximum": 30}})),
]
_EXTRA_TOOLS = [
    ("resume_control", "After the user halted control with Ctrl+Alt+K and agreed to continue, re-enable the "
     "computer tools.", _schema({})),
]


def _to_executor_input(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """MCP arguments -> computer_toolset member input."""
    inp = dict(args)
    if name == "click" and inp.get("modifiers"):
        inp["text"] = inp.pop("modifiers")
    inp.pop("modifiers", None)
    if name == "press_keys":
        inp = {"text": inp["keys"], **({"repeat": inp["repeat"]} if inp.get("repeat") else {})}
    if name == "wait":
        inp = {"duration": inp["seconds"]}
    return inp


def _mcp_content(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Messages-API content blocks -> MCP content."""
    out = []
    for b in blocks:
        if b["type"] == "image":
            out.append({"type": "image", "data": b["source"]["data"], "mimeType": b["source"]["media_type"]})
        else:
            out.append({"type": "text", "text": b.get("text", "")})
    return out


class McpServer:
    def __init__(self, native, executor=None, kill=None) -> None:
        self.native = native        # agent.tools.NativeTools
        self.executor = executor    # desktop.executor.ActionExecutor (None: no computer tools)
        self.kill = kill            # core.safety.KillSwitch
        self._computer = {name: (member, desc, schema) for name, member, desc, schema in COMPUTER_TOOLS}

    # ---- tools ------------------------------------------------------------------------
    def tools(self) -> list[dict[str, Any]]:
        out = [{"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
               for t in self.native.definitions]
        if self.executor is not None:
            out += [{"name": n, "description": d, "inputSchema": s} for n, _m, d, s in COMPUTER_TOOLS]
            out += [{"name": n, "description": d, "inputSchema": s} for n, d, s in _EXTRA_TOOLS]
        return out

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == "resume_control" and self.kill is not None:
            self.kill.reset()
            return {"content": [{"type": "text", "text": "Control resumed."}], "isError": False}
        if name in self._computer and self.executor is not None:
            if self.kill is not None and self.kill.halted:
                return self._error("The user halted control with Ctrl+Alt+K. Stop and ask them whether to "
                                   "continue; call resume_control only if they agree.")
            member = self._computer[name][0]
            try:
                result = self.executor.execute(member, _to_executor_input(name, args))
            except AgentHalted:
                return self._error("Stopped: the user pressed the kill switch (Ctrl+Alt+K).")
            except Exception as e:  # ActionError, bad keys, capture problems ...
                return self._error(f"{type(e).__name__}: {e}")
            return {"content": _mcp_content(result.content), "isError": False}
        text, is_error = self.native.run(name, args)
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    @staticmethod
    def _error(text: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": text}], "isError": True}

    # ---- JSON-RPC ---------------------------------------------------------------------
    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """One request -> one response; notifications (no id) -> None."""
        method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if msg_id is None:
            return None  # notifications/initialized, notifications/cancelled, ...
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                result: Any = {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                               "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.tools()}
            elif method == "tools/call":
                result = self.call(params["name"], params.get("arguments") or {})
            else:
                return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        except Exception as e:
            log.exception("MCP request failed")
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def serve(self, rx: IO[str], tx: IO[str]) -> None:
        for line in rx:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                reply: dict[str, Any] | None = {"jsonrpc": "2.0", "id": None,
                                                "error": {"code": -32700, "message": "Parse error"}}
            else:
                reply = self.handle(msg)
            if reply is not None:
                tx.write(json.dumps(reply, separators=(",", ":")) + "\n")
                tx.flush()


def _std_streams() -> tuple[IO[str], IO[str]]:
    """stdin/stdout as text. A windowed exe has sys.stdin/stdout = None even when the Claude app
    gives it pipes, so open the inherited OS handles directly in that case."""
    if sys.stdin is not None and sys.stdout is not None:
        return (open(sys.stdin.fileno(), "r", encoding="utf-8", closefd=False),
                open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False, newline="\n"))
    import ctypes
    import msvcrt
    import os

    get = ctypes.WinDLL("kernel32").GetStdHandle
    get.restype = ctypes.c_void_p
    fd_in = msvcrt.open_osfhandle(get(-10), os.O_RDONLY | os.O_BINARY)
    fd_out = msvcrt.open_osfhandle(get(-11), os.O_WRONLY | os.O_BINARY)
    return (open(fd_in, "r", encoding="utf-8"), open(fd_out, "w", encoding="utf-8", newline="\n"))


def windows_confirm(text: str) -> bool:
    """Yes/No box on top of everything (no Qt in MCP mode)."""
    import ctypes

    MB_YESNO, MB_ICONQUESTION, MB_TOPMOST, MB_SETFOREGROUND, IDYES = 0x4, 0x20, 0x40000, 0x10000, 6
    return ctypes.WinDLL("user32").MessageBoxW(None, text, "Retrozashi",
                                               MB_YESNO | MB_ICONQUESTION | MB_TOPMOST | MB_SETFOREGROUND) == IDYES


def run_stdio(build_runtime: Callable[[], Any], setup_logging: Callable[[], Any] = lambda: None) -> int:
    from .desktop.win32 import IS_WINDOWS, enable_dpi_awareness

    rx, tx = _std_streams()  # before logging setup, which fills a missing sys.stdout with the log file
    setup_logging()
    sys.stdout = sys.stderr  # stray prints must never corrupt the JSON-RPC stream
    enable_dpi_awareness()
    runtime = build_runtime()
    runtime.profiles.recover()
    if IS_WINDOWS:
        runtime.native.confirm = windows_confirm if runtime.settings.safety.confirm_close_emulator else None
        try:
            runtime.kill.arm_hotkey(runtime.settings.safety.kill_hotkey)
        except OSError as e:  # e.g. the Retrozashi window is open and already owns the hotkey
            log.warning("Kill-switch hotkey unavailable in MCP mode: %s", e)
    log.info("MCP server ready")
    McpServer(runtime.native, runtime.executor, runtime.kill).serve(rx, tx)
    return 0
