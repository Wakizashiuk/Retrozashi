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
- **Window targeting.** Capture just the emulator window (`GetWindowRect` + `SetForegroundWindow`) instead of the whole monitor, for higher detail at lower cost.
- **UI Automation fast path.** Add a `uia_click(name)` native tool via `pywinauto`/`comtypes` UIA, so Qt
  dialogs (PCSX2, DuckStation, Eden are Qt apps) can be operated by control name, not pixels.
- **Settle detection.** After each action, wait until two consecutive frames match (or 1.5 s) before
  the next screenshot. Fewer "clicked before it rendered" mistakes.
- **Confirmation mode.** `confirm_config_writes = true` shows a diff dialog before any write.
- **Undo.** A `restore_backup(emulator)` tool and a UI button for the latest `.bak`.
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
- `pyinstaller --noconfirm --windowed --name Retrozashi --collect-all faster_whisper src/retrozashi/__main__.py`
- Store the API key in Windows Credential Manager (`keyring`) instead of an env var.
- Code-sign the exe (optional) to avoid SmartScreen prompts.
- Auto-update check against GitHub Releases.

## Known risks
| Risk | Mitigation |
|---|---|
| Emulator config keys change between releases | Agent reads the live file first; adapters are hints; GUI fallback |
| Exclusive-fullscreen games block BitBlt capture | Prefer borderless; DXGI duplication capture as an upgrade |
| UIPI blocks input to elevated apps | Run emulators un-elevated; the error says so |
| Cost of screenshot-heavy loops | Native tools first, 1080p-class frames, prompt caching, iteration cap |
