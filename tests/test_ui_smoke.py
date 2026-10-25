"""Builds the real Qt window and profile editor offscreen. Skips where Qt can't load (e.g. no libEGL);
runs on the Windows CI runner, catching widget wiring mistakes the logic tests can't."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)


@pytest.fixture(scope="session")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def dispose(widget, app):
    """Delete Qt widgets while the QApplication is alive, never later from the garbage collector."""
    widget.close()
    widget.deleteLater()
    app.processEvents()


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from retrozashi import config
    from retrozashi.config import Settings
    from retrozashi.core.runtime import Runtime

    monkeypatch.setattr(config, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr("retrozashi.core.runtime.app_data_dir", lambda: tmp_path)
    return Runtime(Settings())


def test_main_window_builds(app, runtime):
    from retrozashi.ui.main_window import MainWindow

    window = MainWindow(runtime, None)
    window._set_status("acting")
    assert window.status.text() == "CONTROLLING INPUT"
    assert window.profiles_panel.list.count() == 0
    assert runtime.native.confirm is not None
    assert any(b.text() == "Use in Claude app" for b in window.findChildren(QtWidgets.QPushButton))
    dispose(window, app)


def test_profile_editor_round_trip(app, runtime):
    from retrozashi.profiles import Profile, Setting
    from retrozashi.ui.profiles_panel import ProfileEditor, ProfilesPanel

    original = Profile("GT4", "pcsx2", r"D:\gt4.iso", True, [Setting("EmuCore/GS", "upscale_multiplier", "6")])
    editor = ProfileEditor(runtime.registry, original)
    assert editor.known.count() > 0                       # PCSX2 known settings offered
    editor.known.setCurrentIndex(0)
    editor._add_known()
    editor.table.item(1, 2).setText("14")
    editor._accept()
    saved = editor.result_profile
    assert saved.name == "GT4" and len(saved.settings) == 2 and saved.settings[1].value == "14"

    runtime.profiles.save(saved)
    panel = ProfilesPanel(runtime.profiles, runtime.registry)
    assert panel.list.count() == 1 and "GT4" in panel.list.item(0).text()
    dispose(editor, app)
    dispose(panel, app)
