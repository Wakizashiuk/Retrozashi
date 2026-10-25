"""Drives AgentLoop with a scripted fake Claude client and a fake input backend: no network, no Windows."""
from __future__ import annotations

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from retrozashi.agent.loop import BATCH_ABORT_TEXT, HALT_TEXT, AgentLoop
from retrozashi.agent.tools import NativeTools
from retrozashi.config import AgentSettings
from retrozashi.core.events import EventBus, EventKind
from retrozashi.core.safety import KillSwitch
from retrozashi.desktop.executor import ActionExecutor
from retrozashi.desktop.screen import Frame, Rect
from retrozashi.emulators.registry import EmulatorRegistry


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.pos = (0, 0)

    def set_cursor_pos(self, x, y):
        self.calls.append(("move", x, y))
        self.pos = (x, y)

    def get_cursor_pos(self):
        return self.pos

    def mouse_button(self, button, down):
        self.calls.append(("btn", button, down))

    def mouse_wheel(self, clicks, horizontal=False):
        self.calls.append(("wheel", clicks, horizontal))

    def key_down(self, vk):
        self.calls.append(("kd", vk))

    def key_up(self, vk):
        self.calls.append(("ku", vk))

    def type_unicode(self, text):
        self.calls.append(("type", text))


class FakeScreen:
    """Second monitor at x=1920, 4K, sent to Claude at half size."""
    def __init__(self):
        self.monitor_index = 2
        self.capture()

    def describe_target(self):
        return "monitor 2"

    def frame_still_valid(self, frame):
        return True

    def capture(self):
        self.last = Frame(b"\x89PNG", 1920, 1080, Rect(1920, 0, 3840, 2160), 0.5)
        return self.last

    def zoom(self, region):
        return b"\x89PNG"


class FakeStream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self.msg


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.messages = self

    def stream(self, **params):
        self.requests.append(params)
        return FakeStream(self.script.pop(0))


def msg(*blocks, stop="tool_use"):
    return Message(id="m", type="message", role="assistant", model="claude-opus-5-5",
                   content=list(blocks), stop_reason=stop, stop_sequence=None,
                   usage=Usage(input_tokens=1, output_tokens=1))


def computer(id_, name, **inp):
    return ToolUseBlock(id=id_, type="tool_use", name=name, input=inp, toolset_name="computer")


def make(script):
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    kill = KillSwitch(bus)
    backend = FakeBackend()
    executor = ActionExecutor(FakeScreen(), kill, backend=backend, action_delay=0)
    client = FakeClient(script)
    loop = AgentLoop(AgentSettings(), bus, kill, executor, NativeTools(EmulatorRegistry()), client=client)
    return loop, client, backend, events, kill


def test_batch_executes_in_order_and_maps_coordinates():
    loop, client, backend, events, _ = make([
        msg(computer("t1", "screenshot"), computer("t2", "left_click", coordinate=[100, 50]),
            computer("t3", "key", text="ctrl+s")),
        msg(TextBlock(type="text", text="Done."), stop="end_turn"),
    ])
    loop.run("save it")

    results = loop.messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2", "t3"]
    assert all(r["toolset_name"] == "computer" for r in results)
    assert results[0]["content"][0]["type"] == "image"
    # (100, 50) in a half-scale image of a monitor at x=1920 -> (2120, 100) physical
    assert ("move", 2120, 100) in backend.calls
    assert backend.calls[-4:] == [("kd", 0x11), ("kd", 0x53), ("ku", 0x53), ("ku", 0x11)]
    assert any(e.kind == EventKind.ASSISTANT and e.message == "Done." for e in events)

    req = client.requests[0]
    assert req["tools"][0] == {"type": "computer_toolset_20260801"}
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"] == {"type": "adaptive"}


def test_failed_action_aborts_rest_of_batch_but_answers_every_block():
    loop, _, _, _, _ = make([
        msg(computer("t1", "key", text="ctrl+banana"), computer("t2", "left_click", coordinate=[1, 1])),
        msg(TextBlock(type="text", text="ok"), stop="end_turn"),
    ])
    loop.run("x")
    r1, r2 = loop.messages[2]["content"]
    assert r1["is_error"] and "banana" in r1["content"]
    assert r2["is_error"] and r2["content"] == BATCH_ABORT_TEXT


def test_kill_switch_halts_and_keeps_history_valid():
    loop, client, backend, events, kill = make([
        msg(computer("t1", "left_click", coordinate=[1, 1]), computer("t2", "type", text="hello")),
    ])
    backend.mouse_button = lambda b, d: kill.trip("test")  # trip mid-batch
    loop.run("x")
    results = loop.messages[2]["content"]
    assert len(results) == 2 and results[1]["content"] == HALT_TEXT
    assert len(client.requests) == 1  # no further API calls after the halt
    assert any(e.kind == EventKind.HALTED for e in events)
    assert ("type", "hello") not in backend.calls


def test_native_tool_errors_are_reported_not_raised():
    native = ToolUseBlock(id="n1", type="tool_use", name="read_emulator_config",
                          input={"emulator": "nope", "section": ""})
    loop, _, _, _, _ = make([msg(native), msg(TextBlock(type="text", text="ok"), stop="end_turn")])
    loop.run("x")
    (r,) = loop.messages[2]["content"]
    assert r["is_error"] and "toolset_name" not in r
