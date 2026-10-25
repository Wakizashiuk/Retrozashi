"""Entry point: `python -m retrozashi` or the packaged Retrozashi.exe.

    Retrozashi.exe                      normal start
    Retrozashi.exe --self-test out.json check the bundle (imports, Qt, capture, UIA, voice, keyring) and exit
    Retrozashi.exe --sync-controllers   Vibepollo prep command: start a background controller sync, return
    Retrozashi.exe --install-stream-sync <sunshine.conf> <command>   (elevated helper) add the prep command
"""
from __future__ import annotations

import json
import logging
import sys
import traceback

log = logging.getLogger("retrozashi")


def _setup_logging() -> str:
    from .config import app_data_dir

    path = app_data_dir() / "retrozashi.log"
    # A windowed (no-console) exe has sys.stdout/stderr = None; anything that prints, such as the
    # Hugging Face progress bar during the Whisper model download, would crash. Send it to the log.
    stream = open(path, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    logging.basicConfig(level=logging.INFO, stream=stream,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return str(path)


def _install_excepthook(log_path: str) -> None:
    def hook(exc_type, exc, tb):
        log.error("Unhandled exception", exc_info=(exc_type, exc, tb))
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            if QApplication.instance():
                QMessageBox.critical(None, "Retrozashi",
                                     f"{exc_type.__name__}: {exc}\n\nDetails were written to:\n{log_path}")
        except Exception:
            pass

    sys.excepthook = hook


def self_test(out_path: str) -> int:
    """Exercise every bundled dependency without showing UI. Used by CI on the frozen exe."""
    results: dict[str, str] = {}

    def check(name, fn):
        try:
            results[name] = "ok: " + str(fn() or "")
        except Exception as e:
            results[name] = f"FAIL: {type(e).__name__}: {e}"

    def qt():
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv[:1])
        return app.platformName()

    def capture():
        from .desktop.screen import ScreenCapture
        f = ScreenCapture(1).capture()
        return f"{f.width}x{f.height}"

    def win32():
        import ctypes
        from .desktop.win32 import INPUT, IS_WINDOWS
        return f"windows={IS_WINDOWS} sizeof(INPUT)={ctypes.sizeof(INPUT)}"

    def uia():
        from .desktop.uia import PywinautoBackend, uia_available
        assert uia_available(), "pywinauto not importable"
        PywinautoBackend()

    def voice():
        import ctranslate2
        import faster_whisper  # noqa: F401  (imports av / onnxruntime too)
        import sounddevice  # noqa: F401
        return f"ctranslate2 {ctranslate2.__version__}"

    def pads():
        from .desktop.pads import connected_pads
        return f"{len(connected_pads())} controller(s) connected"

    def credentials():
        import keyring
        return type(keyring.get_keyring()).__name__

    def agent():
        import anthropic
        from .agent.tools import NativeTools
        from .emulators.registry import EmulatorRegistry
        names = [t["name"] for t in NativeTools(EmulatorRegistry()).definitions]
        return f"anthropic {anthropic.__version__}, {len(names)} native tools"

    for name, fn in [("qt", qt), ("win32", win32), ("capture", capture), ("uia", uia),
                     ("voice", voice), ("pads", pads), ("credentials", credentials), ("agent", agent)]:
        check(name, fn)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    return 0 if all(v.startswith("ok") for v in results.values()) else 1


def headless(argv: list[str]) -> int | None:
    """Command-line modes that never open a window. None = not a headless invocation."""
    if len(argv) >= 3 and argv[1] == "--self-test":
        return self_test(argv[2])
    if len(argv) >= 2 and argv[1] == "--sync-controllers":
        from .streaming import start_background_sync
        start_background_sync()
        return 0  # return at once: Vibepollo waits for prep commands before starting the stream
    if len(argv) >= 2 and argv[1] == "--sync-controllers-worker":
        _setup_logging()
        from .config import Settings, app_data_dir
        from .controller_sets import ControllerSets
        from .desktop.pads import connected_pads
        from .emulators.registry import EmulatorRegistry
        from .streaming import sync_worker

        data = app_data_dir()
        registry = EmulatorRegistry(Settings.load().emulator_paths, remembered_paths=data / "emulator_paths.json")
        sets = ControllerSets(registry, data / "controller_sets.json")
        sync_worker(sets, connected_pads, int(argv[2]) if len(argv) > 2 else 45, log=log.info)
        return 0
    if len(argv) >= 4 and argv[1] == "--install-stream-sync":
        from pathlib import Path
        from .streaming import install_prep_command
        try:
            install_prep_command(Path(argv[2]), argv[3])
            return 0
        except Exception:
            return 1
    return None


def main() -> int:
    code = headless(sys.argv)
    if code is not None:
        return code

    log_path = _setup_logging()
    _install_excepthook(log_path)
    log.info("Starting Retrozashi")

    from .desktop.win32 import enable_dpi_awareness

    enable_dpi_awareness()  # must run before Qt creates any window

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .config import Settings
    from .core.events import EventKind
    from .core.runtime import Runtime
    from .credentials import get_api_key
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
        except OSError as e:  # sounddevice: PortAudio missing or no input device
            runtime.bus.publish(EventKind.ERROR, f"Voice disabled: {e}")

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
    if get_api_key() is None:
        QTimer.singleShot(0, window.ask_api_key)  # first run: ask once the window is up
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
