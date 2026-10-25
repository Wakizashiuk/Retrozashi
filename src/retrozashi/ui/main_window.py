"""Main window: chat on the left, live agent status + kill switch on the right."""
from __future__ import annotations

import base64
import html
import threading
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMainWindow, QMessageBox, QPushButton, QSplitter, QTabWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from ..core.events import AgentEvent, EventKind
from ..core.runtime import Runtime
from .profiles_panel import ProfilesPanel

STATUS_STYLE = {
    "idle": ("IDLE", "#2e7d32"), "thinking": ("THINKING", "#1565c0"), "acting": ("CONTROLLING INPUT", "#e65100"),
    "halted": ("HALTED", "#b71c1c"), "listening": ("LISTENING", "#6a1b9a"), "transcribing": ("TRANSCRIBING", "#6a1b9a"),
    "loading voice model": ("LOADING VOICE MODEL", "#6a1b9a"),
}


class _Question:
    def __init__(self, text: str) -> None:
        self.text = text
        self.answer = False
        self.done = threading.Event()


class EventBridge(QObject):
    """Moves events from worker threads onto the Qt GUI thread."""
    event = Signal(object)
    question = Signal(object)
    voice_state = Signal(str)
    voice_text = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, runtime: Runtime, voice=None) -> None:
        super().__init__()
        self.runtime = runtime
        self.voice = voice
        self.bridge = EventBridge()
        self.bridge.event.connect(self._on_event)
        self.bridge.voice_state.connect(self._set_status)
        self.bridge.voice_text.connect(self._on_voice_text)
        runtime.bus.subscribe(self.bridge.event.emit)
        self.bridge.question.connect(self._answer_question)
        if runtime.settings.safety.confirm_close_emulator:
            runtime.native.confirm = self.ask_from_worker

        self.setWindowTitle("Retrozashi")
        self.resize(1200, 760)
        splitter = QSplitter(Qt.Horizontal)
        self.profiles_panel = ProfilesPanel(runtime.profiles, runtime.registry)
        tabs = QTabWidget()
        tabs.addTab(self._build_chat(), "💬 Chat")
        tabs.addTab(self.profiles_panel, "🎮 Profiles")
        splitter.addWidget(tabs)
        splitter.addWidget(self._build_status())
        splitter.setSizes([700, 500])
        self.setCentralWidget(splitter)
        self._set_status("idle")

    # ---- layout -------------------------------------------------------------
    def _build_chat(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(False)
        self.input = QLineEdit(placeholderText="e.g. Configure PCSX2 for 4K rendering")
        self.input.returnPressed.connect(self._send)
        send = QPushButton("Send", clicked=self._send)
        self.mic = QPushButton("🎤", clicked=self._toggle_mic, toolTip="Push to talk (toggle)")
        self.mic.setEnabled(self.voice is not None)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.mic)
        row.addWidget(send)
        lay.addWidget(self.chat, 1)
        lay.addLayout(row)
        return w

    def _build_status(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.status = QLabel(alignment=Qt.AlignCenter)
        self.status.setMinimumHeight(48)
        kill = QPushButton(f"■  HALT  ({self.runtime.settings.safety.kill_hotkey.upper()})",
                           clicked=lambda: self.runtime.kill.trip("HALT button pressed"))
        kill.setMinimumHeight(64)
        kill.setStyleSheet("QPushButton{background:#c62828;color:white;font-size:20px;font-weight:bold;"
                           "border-radius:8px} QPushButton:pressed{background:#8e0000}")
        self.monitor = QComboBox()
        try:
            from ..desktop.screen import ScreenCapture
            for i, r in enumerate(ScreenCapture.list_monitors()):
                label = "All monitors" if i == 0 else f"Monitor {i}: {r.width}x{r.height} @ ({r.left},{r.top})"
                self.monitor.addItem(label, i)
            self.monitor.setCurrentIndex(self.runtime.screen.monitor_index)
        except Exception:
            self.monitor.addItem("Monitor 1", 1)
        self.monitor.currentIndexChanged.connect(self._pick_monitor)
        self.target = QLabel(f"Target: {self.runtime.screen.describe_target()}")
        self.log = QListWidget()
        self.shot = QLabel("No screenshot yet", alignment=Qt.AlignCenter)
        self.shot.setMinimumHeight(220)
        self.shot.setStyleSheet("background:#111;color:#888")
        undo = QPushButton("↶  Undo last request's config changes", clicked=self.runtime.undo_last)
        key = QPushButton("🔑  API key", clicked=self.ask_api_key)
        lay.addWidget(self.status)
        lay.addWidget(kill)
        row = QHBoxLayout()
        row.addWidget(undo, 1)
        row.addWidget(key)
        lay.addLayout(row)
        lay.addWidget(QLabel("Agent controls:"))
        lay.addWidget(self.monitor)
        lay.addWidget(self.target)
        lay.addWidget(QLabel("Action log"))
        lay.addWidget(self.log, 1)
        lay.addWidget(QLabel("What Claude sees"))
        lay.addWidget(self.shot)
        return w

    def showEvent(self, e) -> None:
        super().showEvent(e)
        from ..desktop.win32 import exclude_from_capture
        exclude_from_capture(int(self.winId()))  # Claude never sees, or clicks, this window

    # ---- handlers -----------------------------------------------------------
    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.runtime.submit(text)

    def _pick_monitor(self, _index: int) -> None:
        self.runtime.set_monitor(self.monitor.currentData())
        self.target.setText(f"Target: {self.runtime.screen.describe_target()}")

    def ask_from_worker(self, text: str, timeout: float = 120.0) -> bool:
        """Called on the agent thread: shows a Yes/No dialog on the GUI thread and waits for it.
        No answer within `timeout` counts as No."""
        q = _Question(text)
        self.bridge.question.emit(q)
        return q.done.wait(timeout) and q.answer

    def _answer_question(self, q: _Question) -> None:
        q.answer = QMessageBox.question(self, "Retrozashi", q.text) == QMessageBox.Yes
        q.done.set()

    def ask_api_key(self) -> None:
        key, ok = QInputDialog.getText(
            self, "Anthropic API key",
            "Paste your Anthropic API key (console.anthropic.com → API keys).\n"
            "It's stored in Windows Credential Manager, not in a file.",
            QLineEdit.Password)
        if not ok or not key.strip():
            return
        from ..credentials import looks_like_api_key

        if not looks_like_api_key(key):
            QMessageBox.warning(self, "Retrozashi", "That doesn't look like an Anthropic API key (sk-ant-...).")
            return
        try:
            self.runtime.set_api_key(key)
            self._log("🔑 API key saved to Windows Credential Manager")
        except Exception as e:
            QMessageBox.critical(self, "Retrozashi", f"Couldn't save the key: {e}")

    def _toggle_mic(self) -> None:
        if self.voice:
            self.voice.toggle()

    def _on_voice_text(self, text: str) -> None:
        self.runtime.submit(text)

    def _set_status(self, state: str) -> None:
        label, color = STATUS_STYLE.get(state, (state.upper(), "#555"))
        self.status.setText(label)
        self.status.setStyleSheet(f"background:{color};color:white;font-size:18px;font-weight:bold;border-radius:6px")

    def _append_chat(self, who: str, text: str, color: str) -> None:
        self.chat.append(f'<p><b style="color:{color}">{who}</b><br>{html.escape(text).replace(chr(10), "<br>")}</p>')

    def _log(self, text: str) -> None:
        self.log.addItem(f"{time.strftime('%H:%M:%S')}  {text}")
        self.log.scrollToBottom()

    def _on_event(self, ev: AgentEvent) -> None:
        if ev.kind == EventKind.STATUS:
            self._set_status(ev.message)
        elif ev.kind == EventKind.USER:
            self._append_chat("You", ev.message, "#90caf9")
        elif ev.kind == EventKind.ASSISTANT:
            self._append_chat("Retrozashi", ev.message, "#a5d6a7")
        elif ev.kind in (EventKind.ACTION, EventKind.TOOL):
            self._log(("🖱 " if ev.kind == EventKind.ACTION else "⚙ ") + ev.message)
            self.target.setText(f"Target: {self.runtime.screen.describe_target()}")
            if ev.kind == EventKind.TOOL:
                self.profiles_panel.refresh()  # profile launched/reverted (by the panel, Claude, or a watcher)
        elif ev.kind == EventKind.SCREENSHOT:
            images = [b for b in ev.data["content"] if b["type"] == "image"]
            if not images:
                return
            data = images[-1]["source"]["data"]
            pix = QPixmap()
            pix.loadFromData(base64.b64decode(data))
            self.shot.setPixmap(pix.scaled(self.shot.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        elif ev.kind == EventKind.ERROR:
            self._log("⚠ " + ev.message)
            self._append_chat("Error", ev.message, "#ef9a9a")
        elif ev.kind == EventKind.HALTED:
            self._set_status("halted")
            self._log("🛑 " + ev.message)

    def closeEvent(self, e) -> None:
        self.runtime.shutdown()
        super().closeEvent(e)
