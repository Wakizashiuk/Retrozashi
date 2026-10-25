"""MCP server for the Claude desktop app: protocol handshake, tool listing/calls, computer tools with
the kill switch, a real stdio subprocess, and registering with the Claude app's config."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

from retrozashi.agent.tools import NativeTools
from retrozashi.claude_app import install
from retrozashi.core.events import EventBus
from retrozashi.core.safety import KillSwitch
from retrozashi.desktop.executor import ActionExecutor
from retrozashi.desktop.screen import Frame, Rect
from retrozashi.emulators.registry import EmulatorRegistry
from retrozashi.mcp_server import McpServer


class FakeScreen:
    monitor_index = 1

    def __init__(self):
        self.last = None

    def capture(self):
        self.last = Frame(b"\x89PNG-fake", 800, 600, Rect(100, 0, 1600, 1200), 0.5)
        return self.last

    def describe_target(self):
        return "monitor 1"

    def frame_still_valid(self, frame):
        return True


class FakeInput:
    def __init__(self, kill=None):
        self.calls, self.kill = [], kill

    def set_cursor_pos(self, x, y):
        self.calls.append(("move", x, y))

    def mouse_button(self, button, down):
        self.calls.append(("btn", button, down))

    def key_down(self, vk):
        self.calls.append(("kd", vk))

    def key_up(self, vk):
        self.calls.append(("ku", vk))

    def type_unicode(self, text):
        self.calls.append(("type", text))


def make_server():
    kill = KillSwitch(EventBus())
    io_ = FakeInput()
    executor = ActionExecutor(FakeScreen(), kill, backend=io_, action_delay=0)
    return McpServer(NativeTools(EmulatorRegistry()), executor, kill), io_, kill


def rpc(server, method, params=None, msg_id=1):
    return server.handle({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})


def test_handshake_and_tool_list():
    server, *_ = make_server()
    init = rpc(server, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "claude-ai", "version": "1"}})["result"]
    assert init["protocolVersion"] == "2025-03-26" and init["serverInfo"]["name"] == "retrozashi"
    assert "tools" in init["capabilities"] and "focus_emulator_window" in init["instructions"]
    assert rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-06-18"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in rpc(server, "tools/list")["result"]["tools"]}
    assert {"list_emulators", "set_emulator_setting", "emulator_guide", "screenshot", "click",
            "press_keys", "resume_control"} <= names
    for t in rpc(server, "tools/list")["result"]["tools"]:
        assert t["inputSchema"]["type"] == "object" and t["description"]
    assert rpc(server, "nope")["error"]["code"] == -32601
    assert rpc(server, "ping")["result"] == {}


def test_native_and_computer_tools():
    server, io_, kill = make_server()
    out = rpc(server, "tools/call", {"name": "emulator_guide", "arguments": {"emulator": "eden", "topic": "controllers"}})
    assert not out["result"]["isError"] and "Input Device" in out["result"]["content"][0]["text"]
    bad = rpc(server, "tools/call", {"name": "read_emulator_config",
                                     "arguments": {"emulator": "nope", "section": "", "search": "", "game_id": ""}})
    assert bad["result"]["isError"]

    shot = rpc(server, "tools/call", {"name": "screenshot", "arguments": {}})["result"]
    assert shot["content"][0] == {"type": "image", "data": "iVBORy1mYWtl", "mimeType": "image/png"}
    rpc(server, "tools/call", {"name": "click", "arguments": {"coordinate": [10, 20], "modifiers": "ctrl"}})
    assert ("move", 120, 40) in io_.calls and ("kd", 0x11) in io_.calls     # scaled + offset, ctrl held
    rpc(server, "tools/call", {"name": "press_keys", "arguments": {"keys": "alt+F4"}})
    assert io_.calls[-4:] == [("kd", 0x12), ("kd", 0x73), ("ku", 0x73), ("ku", 0x12)]
    err = rpc(server, "tools/call", {"name": "press_keys", "arguments": {"keys": "ctrl+banana"}})["result"]
    assert err["isError"] and "banana" in err["content"][0]["text"]

    kill.trip("Ctrl+Alt+K")                                                  # user halts
    halted = rpc(server, "tools/call", {"name": "click", "arguments": {"coordinate": [1, 1]}})["result"]
    assert halted["isError"] and "resume_control" in halted["content"][0]["text"]
    before = len(io_.calls)
    rpc(server, "tools/call", {"name": "type_text", "arguments": {"text": "hi"}})
    assert len(io_.calls) == before                                          # nothing typed while halted
    rpc(server, "tools/call", {"name": "resume_control", "arguments": {}})
    assert not rpc(server, "tools/call", {"name": "type_text", "arguments": {"text": "hi"}})["result"]["isError"]


def test_serve_loop_handles_parse_errors_and_notifications():
    server, *_ = make_server()
    rx = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\n\nnot json\n'
                     '{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    tx = io.StringIO()
    server.serve(rx, tx)
    lines = [json.loads(line) for line in tx.getvalue().splitlines()]
    assert lines == [{"jsonrpc": "2.0", "id": 1, "result": {}},
                     {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}]


def test_real_stdio_subprocess(tmp_path):
    """What the Claude app does: start `retrozashi --mcp` with pipes and talk JSON-RPC."""
    env = {**os.environ, "APPDATA": str(tmp_path), "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    proc = subprocess.Popen([sys.executable, "-m", "retrozashi", "--mcp"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18",
                                                                           "capabilities": {}, "clientInfo": {"name": "t"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "emulator_guide", "arguments": {"emulator": "retroarch", "topic": "streaming"}}}]
    out, err = proc.communicate("".join(json.dumps(m) + "\n" for m in msgs), timeout=60)
    replies = [json.loads(line) for line in out.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3], err
    assert replies[0]["result"]["serverInfo"]["name"] == "retrozashi"
    assert len(replies[1]["result"]["tools"]) > 20
    assert "video_windowed_fullscreen" in replies[2]["result"]["content"][0]["text"]
    assert (tmp_path / "Retrozashi" / "retrozashi.log").exists()             # logs went to the file, not stdout


def test_install_into_claude_app_config(tmp_path):
    cfg = tmp_path / "Claude" / "claude_desktop_config.json"
    cfg.parent.mkdir()
    cfg.write_text(json.dumps({"mcpServers": {"filesystem": {"command": "npx", "args": ["fs"]}},
                               "globalShortcut": "Ctrl+Space"}))
    entry = {"command": r"C:\Tools\Retrozashi\Retrozashi.exe", "args": ["--mcp"]}
    store = tmp_path / "Packages" / "Claude_x" / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json"
    res = install([cfg, store], entry)                                       # Store build not installed: skipped
    assert len(res) == 1 and res[0]["other_servers"] == 1 and not store.exists()
    data = json.loads(cfg.read_text())
    assert data["mcpServers"]["retrozashi"] == entry and data["mcpServers"]["filesystem"]["command"] == "npx"
    assert data["globalShortcut"] == "Ctrl+Space"
    assert install([cfg], entry)[0]["updated_existing"] is True
    fresh = tmp_path / "new" / "Claude" / "claude_desktop_config.json"     # no Claude config yet: created
    install([fresh], entry)
    assert json.loads(fresh.read_text()) == {"mcpServers": {"retrozashi": entry}}
