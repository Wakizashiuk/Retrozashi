"""Profiles tab: list, launch, create, edit, rename and delete per-game profiles."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..emulators.registry import EmulatorRegistry
from ..profiles import Profile, ProfileManager, Setting

GAME_FILTER = "Games (*.iso *.bin *.cue *.chd *.cso *.gz *.rvz *.wbfs *.gcm *.nsp *.xci *.elf *.pbp);;All files (*)"


class ProfileEditor(QDialog):
    def __init__(self, registry: EmulatorRegistry, profile: Profile | None = None, parent=None) -> None:
        super().__init__(parent)
        self.registry = registry
        self.setWindowTitle("Edit profile" if profile else "New profile")
        self.resize(640, 520)

        self.name = QLineEdit(placeholderText="e.g. Gran Turismo 4")
        self.emulator = QComboBox()
        for emu_id in registry.ids():
            self.emulator.addItem(registry.get(emu_id).display_name, emu_id)
        self.emulator.currentIndexChanged.connect(self._fill_known)
        self.game = QLineEdit(placeholderText="Optional: game file to boot")
        browse = QPushButton("Browse…", clicked=self._browse)
        game_row = QHBoxLayout()
        game_row.addWidget(self.game, 1)
        game_row.addWidget(browse)
        self.fullscreen = QCheckBox("Start fullscreen", checked=True)
        self.notes = QLineEdit(placeholderText="Optional notes")

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Section", "Key", "Value"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.known = QComboBox()
        add_known = QPushButton("Add", clicked=self._add_known)
        add_row = QPushButton("Add blank row", clicked=lambda: self._add_row("", "", ""))
        remove_row = QPushButton("Remove selected", clicked=self._remove_rows)
        settings_row = QHBoxLayout()
        settings_row.addWidget(QLabel("Known setting:"))
        settings_row.addWidget(self.known, 1)
        settings_row.addWidget(add_known)
        rows_row = QHBoxLayout()
        rows_row.addWidget(add_row)
        rows_row.addWidget(remove_row)
        rows_row.addStretch(1)

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Emulator", self.emulator)
        form.addRow("Game", game_row)
        form.addRow("", self.fullscreen)
        form.addRow("Notes", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(QLabel("Settings applied at launch and reverted when the emulator closes:"))
        lay.addLayout(settings_row)
        lay.addWidget(self.table, 1)
        lay.addLayout(rows_row)
        lay.addWidget(buttons)

        if profile:
            self.name.setText(profile.name)
            self.emulator.setCurrentIndex(max(0, self.emulator.findData(profile.emulator)))
            self.game.setText(profile.game_path)
            self.fullscreen.setChecked(profile.fullscreen)
            self.notes.setText(profile.notes)
            for s in profile.settings:
                self._add_row(s.section, s.key, s.value)
        self._fill_known()
        self.result_profile: Profile | None = None

    def _fill_known(self) -> None:
        self.known.clear()
        adapter = self.registry.get(self.emulator.currentData())
        for s in adapter.KNOWN_SETTINGS:
            self.known.addItem(f"[{s.section}] {s.key}", (s.section, s.key, s.meaning))
            self.known.setItemData(self.known.count() - 1, s.meaning, Qt.ToolTipRole)

    def _add_known(self) -> None:
        data = self.known.currentData()
        if data:
            section, key, meaning = data
            self._add_row(section, key, "", tooltip=meaning)

    def _add_row(self, section: str, key: str, value: str, tooltip: str = "") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, text in enumerate((section, key, value)):
            item = QTableWidgetItem(text)
            if tooltip:
                item.setToolTip(tooltip)
            self.table.setItem(row, col, item)
        if not value:
            self.table.setCurrentCell(row, 2)
            self.table.editItem(self.table.item(row, 2))

    def _remove_rows(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose game", self.game.text(), GAME_FILTER)
        if path:
            self.game.setText(path.replace("/", "\\"))

    def settings(self) -> list[Setting]:
        out = []
        for row in range(self.table.rowCount()):
            cells = [(self.table.item(row, c).text().strip() if self.table.item(row, c) else "") for c in range(3)]
            if any(cells):
                out.append(Setting(*cells))
        return out

    def _accept(self) -> None:
        settings = self.settings()
        incomplete = [s for s in settings if not (s.section and s.key and s.value)]
        if incomplete:
            QMessageBox.warning(self, "Retrozashi", "Every setting needs a section, a key and a value.")
            return
        self.result_profile = Profile(self.name.text().strip(), self.emulator.currentData(),
                                      self.game.text().strip(), self.fullscreen.isChecked(), settings,
                                      self.notes.text().strip())
        self.accept()


class ProfilesPanel(QWidget):
    def __init__(self, manager: ProfileManager, registry: EmulatorRegistry, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.registry = registry
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _: self._launch())
        self.launch_btn = QPushButton("▶  Launch", clicked=self._launch)
        self.launch_btn.setStyleSheet("font-weight:bold")
        buttons = QHBoxLayout()
        for b in (self.launch_btn, QPushButton("New…", clicked=self._new),
                  QPushButton("Edit…", clicked=self._edit), QPushButton("Delete", clicked=self._delete)):
            buttons.addWidget(b)
        hint = QLabel("A profile's settings are applied when you launch it here (or ask Retrozashi to), "
                      "and put back automatically when the emulator closes.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#888")
        lay = QVBoxLayout(self)
        lay.addWidget(self.list, 1)
        lay.addLayout(buttons)
        lay.addWidget(hint)
        self.refresh()

    def refresh(self) -> None:
        selected = self._selected_name()
        active = self.manager.active()
        self.list.clear()
        for p in self.manager.list():
            running = active.get(p.emulator) == p.name
            item = QListWidgetItem(("🟢 " if running else "") + f"{p.name}  —  {p.summary()}")
            item.setData(Qt.UserRole, p.name)
            if p.notes:
                item.setToolTip(p.notes)
            self.list.addItem(item)
            if p.name == selected:
                self.list.setCurrentItem(item)

    def _selected_name(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _launch(self) -> None:
        name = self._selected_name()
        if not name:
            return
        try:
            self.manager.launch(name)
        except Exception as e:
            QMessageBox.warning(self, "Retrozashi", str(e))
        self.refresh()

    def _new(self) -> None:
        self._open_editor(None)

    def _edit(self) -> None:
        name = self._selected_name()
        if name:
            self._open_editor(self.manager.get(name))

    def _open_editor(self, profile: Profile | None) -> None:
        dialog = ProfileEditor(self.registry, profile, self)
        while dialog.exec() == QDialog.Accepted and dialog.result_profile:
            try:
                self.manager.save(dialog.result_profile, replacing=profile.name if profile else None)
                break
            except (ValueError, KeyError) as e:
                QMessageBox.warning(self, "Retrozashi", str(e))  # keep the dialog's edits and let them fix it
        self.refresh()

    def _delete(self) -> None:
        name = self._selected_name()
        if name and QMessageBox.question(self, "Retrozashi", f"Delete profile '{name}'?") == QMessageBox.Yes:
            self.manager.delete(name)
            self.refresh()
