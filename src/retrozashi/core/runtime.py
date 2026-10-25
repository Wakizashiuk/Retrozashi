"""Composition root: wires settings, safety, desktop, emulators and the agent together,
and runs agent requests on a single background worker so the UI never blocks."""
from __future__ import annotations

import queue
import threading

from ..agent.loop import AgentLoop
from ..agent.tools import NativeTools
from ..config import Settings
from ..desktop.executor import ActionExecutor
from ..desktop.screen import ScreenCapture
from ..emulators.registry import EmulatorRegistry
from .events import EventBus, EventKind
from .safety import KillSwitch

_STOP = object()


class Runtime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bus = EventBus()
        self.kill = KillSwitch(self.bus)
        self.screen = ScreenCapture(settings.target_monitor)
        self.executor = ActionExecutor(self.screen, self.kill, action_delay=settings.safety.action_delay)
        self.registry = EmulatorRegistry(settings.emulator_paths)
        self.agent = AgentLoop(settings.agent, self.bus, self.kill, self.executor, NativeTools(self.registry))
        self._jobs: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._work, name="AgentWorker", daemon=True)
        self.busy = False

    def start(self) -> None:
        try:
            self.kill.arm_hotkey(self.settings.safety.kill_hotkey)
        except OSError as e:
            self.bus.publish(EventKind.ERROR, f"Kill-switch hotkey unavailable ({e}). Use the on-screen button.")
        self._worker.start()

    def submit(self, text: str) -> None:
        if self.busy:
            self.bus.publish(EventKind.ERROR, "Still working on the last request. Halt it first or wait.")
            return
        self._jobs.put(text)

    def set_monitor(self, index: int) -> None:
        self.screen.monitor_index = index
        self.screen.last = None

    def shutdown(self) -> None:
        self.kill.trip("Shutting down")
        self.kill.disarm()
        self._jobs.put(_STOP)

    def _work(self) -> None:
        while (job := self._jobs.get()) is not _STOP:
            self.busy = True
            try:
                self.agent.run(job)
            finally:
                self.busy = False
