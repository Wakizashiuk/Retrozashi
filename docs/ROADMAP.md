# Retrozashi Roadmap

Each phase ends with something you can run and use.

## Phase 0: Foundations ✅ (this scaffold)
- [x] Project layout, `pyproject.toml`, settings file
- [x] Win32 layer: DPI awareness, `SendInput` (scan codes), global hotkeys, capture exclusion
- [x] Multi-monitor capture + scaling to API limits + coordinate mapping
- [x] Executor for all 17 `computer_toolset_20260801` members
- [x] Kill switch (hotkey, button, per-action gate, stream abort, held-input release)
- [x] Agent loop with batch semantics, refusal fallback, prompt caching
- [x] Adapters: PCSX2, DuckStation, Dolphin, RPCS3, Eden (config read/write, launch, close)
- [x] PySide6 window: chat, status, action log, screenshot preview, monitor picker
- [x] faster-whisper push-to-talk
- [x] Unit tests for scaling, keymap, INI editing and the agent loop (fake client)

## Phase 1: First real run on Windows (≈1 weekend)
1. `pip install -e .[voice,dev]`, set `ANTHROPIC_API_KEY`, run `python -m retrozashi`.
2. Point `emulator_paths` at your installs in `settings.toml`; verify `list_emulators` finds every config.
3. **Validate each adapter's `KNOWN_SETTINGS`** against your versions: change a setting in the GUI,
   diff the config, and correct enum values (PCSX2 `Renderer`, Eden `resolution_setup`).
4. Test the kill switch over a fullscreen emulator (exclusive fullscreen and borderless).
5. Calibrate: does a click at a known coordinate land correctly on each monitor at 100%/150% scaling?

## Phase 2: Reliability (1–2 weeks)
- [x] **Window targeting.** `focus_emulator_window` captures just the emulator window: higher detail, clicks confined, stale-frame guard.
- **Occluded-window capture.** Use Windows.Graphics.Capture (or `PrintWindow`) so a covered window still screenshots correctly.
- [x] **UI Automation tier.** `ui_list_controls` / `ui_act` operate Qt dialogs by control name, scoped to emulator processes.
- **UIA on real builds.** Check which PCSX2/DuckStation/Eden/Dolphin controls expose useful names; add
  `automation_id` hints to adapters for the key dialogs (graphics, controllers).
- [x] **Settle detection.** Before a screenshot that follows an action, poll 1/16-scale thumbnails until two match
  (≤1.5 s). If the screen never settles (a running game), Claude gets a "still changing" note.
- **Confirmation mode.** `confirm_config_writes = true` shows a diff dialog before any write.
- [x] **Undo.** `list_config_backups` / `restore_config_backup` tools, plus an Undo button that reverts every file
  the last request changed (no API call). Backups use microsecond timestamps; restores are undoable too.
- Long sessions: enable server-side compaction (`compact-2026-01-12` beta) when history gets large.

## Phase 3: Voice & UX polish (1 week)
- Hold-to-talk using a low-level keyboard hook (key-up detection) alongside the toggle.
- Spoken replies via Windows SAPI / `pyttsx3` (optional).
- Streaming text deltas to the chat, and progress notes via `thinking.display: "updates"`.
- System tray mode: hide the window, keep the hotkeys, show toast notifications.

## Phase 4: Library & profiles (2 weeks)
- Game library scan (paths from each emulator's config), with per-game profiles:
  "4K + widescreen patches for Gran Turismo 4" saved as a set of key/value writes.
- Per-game config files (RPCS3 `custom_configs`, PCSX2 `gamesettings/*.ini`, Dolphin `GameSettings`).
- Controller profiles: detect connected pads via SDL/XInput and generate bindings per emulator.

## Phase 5: Ship it
- [x] PyInstaller one-folder build (`packaging/Retrozashi.spec`, `scripts/build.ps1`), self-tested and zipped by CI.
- [x] API key in Windows Credential Manager (`keyring`), with a first-run prompt and a 🔑 button.
- [x] Windowed-exe hardening: log file, crash dialog, stdout/stderr redirect.
- App icon + version resource in the exe.
- Code-sign the exe (optional) to avoid SmartScreen prompts.
- Auto-update check against GitHub Releases.

## Known risks
| Risk | Mitigation |
|---|---|
| Emulator config keys change between releases | Agent reads the live file first; adapters are hints; GUI fallback |
| Exclusive-fullscreen games block BitBlt capture | Prefer borderless; DXGI duplication capture as an upgrade |
| UIPI blocks input to elevated apps | Run emulators un-elevated; the error says so |
| Cost of screenshot-heavy loops | Native tools first, 1080p-class frames, prompt caching, iteration cap |
