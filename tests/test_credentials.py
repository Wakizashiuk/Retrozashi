"""API key lookup order and the agent's behaviour when no key is available."""
from __future__ import annotations

import anthropic

from retrozashi import credentials
from retrozashi.agent import loop as loop_mod
from retrozashi.agent.loop import AgentLoop
from retrozashi.agent.tools import NativeTools
from retrozashi.config import AgentSettings
from retrozashi.core.events import EventBus, EventKind
from retrozashi.core.safety import KillSwitch
from retrozashi.emulators.registry import EmulatorRegistry


class FakeKeyring:
    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        self.store[(service, user)] = value

    def delete_password(self, service, user):
        self.store.pop((service, user), None)


def test_lookup_order(monkeypatch):
    kr = FakeKeyring()
    monkeypatch.setattr(credentials, "_keyring", lambda: kr)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert credentials.get_api_key() is None
    credentials.set_api_key("  sk-ant-stored-key-0123456789  ")
    assert credentials.get_api_key() == "sk-ant-stored-key-0123456789"  # trimmed
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-wins")
    assert credentials.get_api_key() == "sk-ant-env-wins"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    credentials.clear_api_key()
    assert credentials.get_api_key() is None


def test_no_keyring_backend(monkeypatch):
    monkeypatch.setattr(credentials, "_keyring", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert credentials.get_api_key() is None


def test_looks_like_api_key():
    assert credentials.looks_like_api_key("sk-ant-api03-abcdefghijklmnop")
    assert not credentials.looks_like_api_key("sk-proj-abcdefghijklmnopqrstu")
    assert not credentials.looks_like_api_key("sk-ant-has space inside-xxxxxxx")


def test_missing_credentials_reported_not_raised(monkeypatch):
    def no_creds(**kwargs):
        raise anthropic.AnthropicError("Could not resolve authentication method")

    monkeypatch.setattr(loop_mod.anthropic, "Anthropic", no_creds)
    monkeypatch.setattr(credentials, "get_api_key", lambda: None)
    bus = EventBus()
    events = []
    bus.subscribe(events.append)
    agent = AgentLoop(AgentSettings(), bus, KillSwitch(bus), executor=None,
                      native=NativeTools(EmulatorRegistry()))
    agent.run("set up pcsx2")  # must not raise: the UI stays usable so the user can add a key
    errors = [e.message for e in events if e.kind == EventKind.ERROR]
    assert errors and "API key" in errors[0]
    assert events[-1].kind == EventKind.STATUS and events[-1].message == "idle"
