"""UiaController against a fake backend: listing, scoping to the emulator's process, actions, kill switch."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from retrozashi.agent.tools import NativeTools
from retrozashi.core.events import EventBus
from retrozashi.core.safety import AgentHalted, KillSwitch
from retrozashi.desktop.uia import UiaController, UiaError, UiNode
from retrozashi.emulators.registry import EmulatorRegistry

PCSX2_PID = 4242


class FakeBackend:
    def __init__(self):
        self.performed = []
        self.state_by_name = {"Enable VSync": {"toggled": 0}, "Internal Resolution:": {"text": "Native (PS2)"}}
        self.settings = UiNode("PCSX2 Settings", "Window", raw="win-settings")
        self.other = UiNode("Notepad", "Window", raw="win-notepad")
        self.children = {
            "win-settings": [
                UiNode("Graphics", "TabItem", depth=0),
                UiNode("Internal Resolution:", "ComboBox", "resolution", depth=1),
                UiNode("Enable VSync", "CheckBox", depth=1),
                UiNode("Some label", "Text", depth=1),
                UiNode("", "Pane", depth=1),
                UiNode("Apply", "Button", enabled=False, depth=1),
            ],
            "win-notepad": [UiNode("Save", "Button")],
        }

    def windows(self, pids):
        # Only windows of the requested pids may come back; Notepad belongs to another process.
        return [self.settings] if PCSX2_PID in pids else []

    def descendants(self, window):
        return self.children[window.raw]

    def perform(self, node, action, value):
        self.performed.append((node.name, action, value))
        if action == "toggle_on":
            self.state_by_name[node.name] = {"toggled": 1}
        if action == "select":
            self.state_by_name[node.name] = {"text": value}

    def state(self, node):
        return self.state_by_name.get(node.name, {})


@pytest.fixture
def setup(monkeypatch):
    registry = EmulatorRegistry()
    pcsx2 = registry.get("pcsx2")
    monkeypatch.setattr(pcsx2, "running_processes", lambda: [SimpleNamespace(pid=PCSX2_PID)])
    monkeypatch.setattr(registry.get("dolphin"), "running_processes", lambda: [])
    kill = KillSwitch(EventBus())
    backend = FakeBackend()
    return UiaController(registry, kill, backend), backend, kill, registry


def test_list_filters_to_interactive_named_controls(setup):
    uia, *_ = setup
    listing = uia.list_controls("pcsx2")
    (win,) = listing["windows"]
    assert win["title"] == "PCSX2 Settings"
    names = [c["name"] for c in win["controls"]]
    assert names == ["Graphics", "Internal Resolution:", "Enable VSync", "Apply"]  # no label, no unnamed pane
    assert win["controls"][1]["automation_id"] == "resolution"
    assert win["controls"][3]["enabled"] is False
    assert "Some label" in json.dumps(uia.list_controls("pcsx2", include_labels=True))


def test_actions_return_new_state(setup):
    uia, backend, *_ = setup
    uia.list_controls("pcsx2")
    assert uia.act("e2", "toggle_on")["state"] == {"toggled": 1}
    assert uia.act("e1", "select", "6x Native (~4K)")["state"] == {"text": "6x Native (~4K)"}
    assert backend.performed == [("Enable VSync", "toggle_on", ""), ("Internal Resolution:", "select", "6x Native (~4K)")]
    assert uia.act("e1", "read")["state"]["text"] == "6x Native (~4K)"
    assert len(backend.performed) == 2  # read performs nothing


def test_guards(setup):
    uia, backend, kill, _ = setup
    with pytest.raises(UiaError, match="not running"):
        uia.list_controls("dolphin")
    with pytest.raises(UiaError, match="Unknown element"):
        uia.act("e0", "click")  # before any listing
    uia.list_controls("pcsx2")
    with pytest.raises(UiaError, match="disabled"):
        uia.act("e3", "click")
    with pytest.raises(UiaError, match="Unknown action"):
        uia.act("e0", "delete_everything")
    kill.trip()
    with pytest.raises(AgentHalted):
        uia.act("e0", "click")
    assert backend.performed == []


def test_native_tools_expose_uia_only_when_available(setup):
    uia, _, _, registry = setup
    assert "ui_act" not in [t["name"] for t in NativeTools(registry).definitions]
    tools = NativeTools(registry, uia)
    assert "ui_act" in [t["name"] for t in tools.definitions]
    text, err = tools.run("ui_list_controls", {"emulator": "pcsx2", "window_title_contains": "", "include_labels": False})
    assert not err and "Enable VSync" in text
    text, err = tools.run("ui_act", {"element_id": "e99", "action": "click", "value": ""})
    assert err and "UiaError" in text
