"""Typed events the agent emits. The UI subscribes to drive the status panel and chat log."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class EventKind(str, Enum):
    STATUS = "status"            # agent state changed: idle / thinking / acting / halted
    USER = "user"                # user message (typed or transcribed)
    ASSISTANT = "assistant"      # Claude's visible text
    ACTION = "action"            # a computer action about to execute
    TOOL = "tool"                # a native tool (config read/write, launch) ran
    SCREENSHOT = "screenshot"    # a screenshot was taken (payload: PNG bytes)
    ERROR = "error"
    HALTED = "halted"            # the kill switch fired


@dataclass
class AgentEvent:
    kind: EventKind
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


Listener = Callable[[AgentEvent], None]


class EventBus:
    """Fan-out to listeners. Thread-safe; listeners run on the publishing thread,
    so Qt listeners must re-emit through a Signal (see ui/bridge.py)."""

    def __init__(self) -> None:
        self._listeners: list[Listener] = []
        self._lock = threading.Lock()

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def publish(self, kind: EventKind, message: str, **data: Any) -> None:
        event = AgentEvent(kind, message, data)
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:  # a broken listener must never kill the agent loop
                pass
