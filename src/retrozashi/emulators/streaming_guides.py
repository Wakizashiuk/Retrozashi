"""'streaming' guides: make one emulator stream-ready for Moonlight + Vibepollo, when the user asks.

Settings are saved globally. Values marked (verify) differ between builds: confirm with
snapshot_config -> change in the emulator's settings -> diff_config -> remember_setting.
"""

COMMON = """\
Stream-ready <EMU> for Moonlight + Vibepollo (do this only for the emulator the user chose).

Display. Vibepollo streams a virtual display at each client's resolution, so the emulator must
follow the desktop resolution:
- borderless fullscreen (not exclusive), fullscreen when a game starts;
- don't pause when the window loses focus (Vibepollo/Moonlight overlays can take focus);
- no exit confirmation dialogs (no mouse/keyboard over a couch stream);
- a controller combo to open the emulator's menu and to quit.
Internal resolution stays whatever the user asks for (upscaling guide).

Controllers (Vibepollo "Auto" virtual pad):
1. install_stream_sync once (all emulators share it; Windows asks for admin permission).
2. For each pad the user streams with: they start a Moonlight session with it (DualSense ->
   virtual DS4/DualSense, 8BitDo in X-input -> virtual Xbox 360). detect_controllers confirms.
3. Map that virtual pad in this emulator (emulator_guide <ID> controllers; pick the virtual pad as
   the device), then save_controller_set(<ID>, "<pad name>").
4. Repeat for the other pad. From then on every stream start switches to the matching set.

Settings for <EMU>:
"""

PCSX2 = COMMON.replace("<EMU>", "PCSX2").replace("<ID>", "pcsx2") + """\
- [UI] StartFullscreen = true ; [UI] ConfirmShutdown = false ; [UI] HideMouseCursor = true
- [UI] PauseOnFocusLoss = false (verify)
- Fullscreen mode: Settings -> Graphics -> Display -> Fullscreen Mode = "Borderless Fullscreen" (learn by diff)
- Pad hotkeys: Settings -> Hotkeys: bind "Open Pause Menu" to Select+Start pressed together (click the
  field, press both). The pause menu has Shut Down. Learn the key by diff; it's part of the controller set.
SDL bindings use the pad index (SDL-0), so PCSX2 keeps working whichever virtual pad type appears;
still save a set per pad so hotkeys and pressure settings follow the pad.
"""

DUCKSTATION = COMMON.replace("<EMU>", "DuckStation").replace("<ID>", "duckstation") + """\
- [Main] StartFullscreen = true ; [Main] ConfirmPowerOff = false ; [Main] PauseOnFocusLoss = false
- [Main] HideCursorInFullscreen = true (verify)
- Exclusive fullscreen off: Settings -> Display -> Fullscreen Mode = "Borderless Fullscreen" (learn by diff)
- Pad hotkeys: Settings -> Hotkeys: "Open Pause Menu" = Select+Start (press together). Learn by diff.
Like PCSX2, SDL-0 bindings survive a change of virtual pad type.
"""

DOLPHIN = COMMON.replace("<EMU>", "Dolphin").replace("<ID>", "dolphin") + """\
- "Dolphin.ini::Display" Fullscreen = True ; "Dolphin.ini::Interface" ConfirmStop = False
- "Dolphin.ini::Interface" PauseOnFocusLost = False ; "Dolphin.ini::Input" BackgroundInput = True
  (input keeps working if focus moves) (verify)
- "GFX.ini::Settings" BorderlessFullscreen = True (verify)
- Pad hotkeys: Options -> Hotkey Settings -> Device = the virtual pad, bind "Exit" / "Stop" to a
  combo (e.g. `Back & Start`). Saved in Hotkeys.ini, part of the controller set.
Dolphin binds the device by name, so the controller sets are what keep it working with Auto.
"""

RPCS3 = COMMON.replace("<EMU>", "RPCS3").replace("<ID>", "rpcs3") + """\
- Miscellaneous "Start games in fullscreen mode" = true
- Miscellaneous "Exit RPCS3 when process finishes" = true (closing the game closes RPCS3)
- Miscellaneous "Pause emulation on RPCS3 focus loss" = false (verify)
- Miscellaneous "Show shader compilation hint" / "Prevent display sleep while running games" = true (verify)
- Video "Exclusive Fullscreen Mode": "Prefer borderless fullscreen" (verify)
- The PS button opens RPCS3's home menu (quit there). Pad handlers differ per virtual pad type
  (DualSense/DS4 handler vs XInput), which is exactly what the controller sets switch.
"""

EDEN = COMMON.replace("<EMU>", "Eden").replace("<ID>", "eden") + """\
- [UI] fullscreen = true ; [UI] confirmClose = false ; [UI] pauseWhenInBackground = false (verify)
- [Renderer] fullscreen_mode = 0 (borderless) (verify) ; [System] use_docked_mode docked for TV output
- Controller hotkeys: Emulation -> Configure -> Hotkeys has a "Controller Hotkey" column; set
  "Exit Eden" (e.g. Home+Plus) and "Exit Fullscreen"/"Toggle Fullscreen". Learn the keys by diff
  ([UI] Shortcuts...).
Eden binds by device GUID, so a controller set per virtual pad is essential with Auto.
"""

RETROARCH = COMMON.replace("<EMU>", "RetroArch").replace("<ID>", "retroarch") + """\
- video_fullscreen = "true" ; video_windowed_fullscreen = "true" (borderless)
- pause_nonactive = "false" (keep running when focus moves)
- quit_press_twice = "false" ; input_menu_toggle_gamepad_combo = "2" (L3+R3) (verify)
- input_quit_gamepad_combo: a combo to quit RetroArch from the pad (verify values in Settings ->
  Input -> Hotkeys -> "Quit (Controller Combo)")
- input_joypad_driver = "sdl2" and input_autodetect_enable = "true": RetroArch's autoconfig recognises
  Vibepollo's virtual Xbox 360 and DS4/DualSense pads by name, so it adapts to Auto on its own.
"""
