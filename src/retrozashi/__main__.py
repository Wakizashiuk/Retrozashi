"""Entry point: `python -m retrozashi` or the packaged Retrozashi.exe."""
from __future__ import annotations

import sys


def main() -> int:
    from .desktop.win32 import enable_dpi_awareness

    enable_dpi_awareness()  # must run before Qt creates any window

    from PySide6.QtWidgets import QApplication

    from .config import Settings
    from .core.events import EventKind
    from .core.runtime import Runtime
    from .ui.main_window import MainWindow

    settings = Settings.load()
    app = QApplication(sys.argv)
    runtime = Runtime(settings)

    voice = None
    if settings.voice.enabled:
        try:
            import faster_whisper, sounddevice  # noqa: F401  (fail fast if the extra isn't installed)
            from .voice.stt import VoiceInput
            voice = VoiceInput(settings.voice, on_text=lambda t: None)
        except ImportError:
            runtime.bus.publish(EventKind.ERROR, "Voice disabled: pip install retrozashi[voice]")

    window = MainWindow(runtime, voice)
    if voice:
        voice.on_text = window.bridge.voice_text.emit
        voice.on_state = window.bridge.voice_state.emit
        try:
            from .desktop.keymap import parse_hotkey
            from .desktop.win32 import IS_WINDOWS, GlobalHotkey
            if IS_WINDOWS:
                mods, vk = parse_hotkey(settings.voice.push_to_talk_hotkey)
                GlobalHotkey(mods, vk, voice.toggle).start()
        except OSError as e:
            runtime.bus.publish(EventKind.ERROR, f"Push-to-talk hotkey unavailable: {e}")

    runtime.start()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
