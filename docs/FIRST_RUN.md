# First run: controllers + upscaling for every emulator

A checklist for your first sessions with Retrozashi. Say each 🗣️ line (**Ctrl+Alt+Space** to talk,
press again to stop) or type it in the Chat tab. **Ctrl+Alt+K stops everything instantly.**

Do the [shared setup](#0-shared-setup-once) once, then the sections for the emulators you use, in any
order. Each takes about 15–30 minutes the first time and is a single sentence after that.

| Emulator | Console | Jump to |
|---|---|---|
| Eden | Switch | [Eden](#eden-switch) |
| PCSX2 | PS2 | [PCSX2](#pcsx2-ps2) |
| DuckStation | PS1 | [DuckStation](#duckstation-ps1) |
| Dolphin | GameCube / Wii | [Dolphin](#dolphin-gamecube--wii) |
| RPCS3 | PS3 | [RPCS3](#rpcs3-ps3) |
| RetroArch | Many (via cores) | [RetroArch](#retroarch-many-consoles) |

---

## 0. Shared setup (once)

- [ ] **Get the app.** Download `Retrozashi-win64` from the latest CI run's *Artifacts*, unzip it and run
      `Retrozashi\Retrozashi.exe`. Paste your Anthropic API key when asked; it's saved in Windows
      Credential Manager. SmartScreen may warn about an unsigned app: *More info → Run anyway*.
- [ ] **Controllers:** connect them *before* opening an emulator. Most emulators only detect pads at launch.
- [ ] **Steam:** close it, or turn off Steam Input for your DualSense/DS4 and 8BitDo. Otherwise you get
      doubled or missing inputs in every emulator.
- [ ] **8BitDo mode:** X-input (**Start + X**) looks like an Xbox pad and works everywhere. Switch mode
      (**Start + Y**) gives Nintendo labels and motion on gyro models, which is best for Eden and Dolphin's
      Wii games. Pick one per emulator and keep it.
- [ ] **Never run emulators as administrator.** Windows blocks simulated clicks into admin apps.
- [ ] In Retrozashi's right panel, pick the **monitor your emulators open on**.
- [ ] 🗣️ *"Which emulators can you find on my PC, and where are their config files?"*
      Anything missing: 🗣️ *"Find PCSX2 on my PC"*, or tell it the folder. If an emulator has no config
      yet, open and close it once.

**How Retrozashi works through these.** It writes config files directly when it can, which is exact
and backed up. It uses the emulator's own windows when it must, for example to map a controller.
When an option's stored value isn't known for your build, it **learns** it: it flips the option in the
emulator's settings window, sees what changed in the config, and remembers it. If an emulator is open
and Retrozashi needs to write its config, **you get a Yes/No dialog** before it closes it.

---

## Eden (Switch)

**1. Learn your build's graphics values (one time, ~10 min)**
🗣️ *"Open Eden and learn the resolution options 1x, 1.5x, 2x and 3x from the Graphics settings, plus
the FSR and Bilinear filters and SMAA, and remember each one."*
✅ Check: 🗣️ *"What Eden settings have you learned?"*

**2. Global upscaling**
🗣️ *"Set Eden to docked mode with 2x resolution and the FSR filter."*
Docked 2x = 4K; 1.5x suits 1440p screens.

**3. Per-game overrides**
🗣️ *"List my Switch games with their title IDs."*
🗣️ *"Set Tears of the Kingdom to 1x and Mario Kart 8 to 3x as per-game overrides."*
Back to global: 🗣️ *"Make Mario Kart use the global resolution again."*

**4. Controllers**
🗣️ *"Set up my DualSense as Player 1 in Eden as a Pro Controller with motion, and save it as an input
profile called DualSense."*
🗣️ *"Do the same for my 8BitDo as a profile called 8BitDo."* Switch later: *"Load the 8BitDo profile
for Player 1 in Eden."*

✅ **Test:** launch a game. Check A/B/X/Y positions, both sticks, motion, and the resolution in Eden's
status bar.

---

## PCSX2 (PS2)

**1. Global upscaling**
🗣️ *"Set PCSX2 to 4x internal resolution with the Vulkan renderer and 16x anisotropic filtering."*
3x ≈ 1080p, 4x ≈ 1440p, 6x ≈ 4K. If it doesn't know your build's renderer value, it learns it from
Settings → Graphics first.

**2. Per-game overrides**
🗣️ *"List my PS2 games with their serials."* (read from each disc image)
🗣️ *"Set Gran Turismo 4 to 6x as a per-game setting."*
The **first** per-game file for a game needs its CRC, which Retrozashi reads from PCSX2's game
Properties window; after that, the serial is enough. Widescreen: 🗣️ *"Turn on widescreen patches for
Gran Turismo 4."*

**3. Controllers**
🗣️ *"Set up my DualSense as controller 1 in PCSX2 using Automatic Mapping, with DualSense enhanced
mode on, and save it as an input profile called DualSense."*
🗣️ *"Do the same for my 8BitDo as a profile called 8BitDo."*

✅ **Test:** pressure-sensitive buttons (e.g. MGS2), both sticks, rumble. Shimmering lines or ghosting
when upscaled? 🗣️ *"This game has lines when upscaled; try the upscaling fixes for it per-game."*

---

## DuckStation (PS1)

**1. Global upscaling**
🗣️ *"Set DuckStation to 6x resolution with PGXP and true color on, using Vulkan."*
4x ≈ 1080p, 6x ≈ 1440p, 9x ≈ 4K. PGXP stops the wobbly polygons PS1 games are known for.

**2. Per-game overrides**
🗣️ *"List my PS1 games with their serials."* (read from .cue/.bin/.iso; for .chd it checks DuckStation)
🗣️ *"Set Crash Bandicoot to 9x with the widescreen hack and 16:9, as a per-game setting."*

**3. Controllers**
🗣️ *"Set up my DualSense as an Analog Controller on port 1 in DuckStation using Automatic Mapping, and
save it as an input profile called DualSense."* Same for the 8BitDo.
Some games start in digital mode: 🗣️ *"Force analog mode on reset in DuckStation."*

✅ **Test:** analog sticks in an analog game (e.g. Ape Escape), rumble, and that polygons don't wobble.

---

## Dolphin (GameCube / Wii)

**1. Global upscaling**
🗣️ *"Set Dolphin to 4x internal resolution (1440p) with 16x anisotropic filtering and Vulkan."*
3 = 1080p, 4 = 1440p, 6 = 4K.

**2. Per-game overrides**
🗣️ *"List my GameCube and Wii games with their IDs."* (read from .iso/.gcm/.wbfs; for .rvz it checks
Dolphin's game Properties)
🗣️ *"Set Super Mario Galaxy to 6x and Twilight Princess to 3x as per-game settings."*

**3. Controllers**
🗣️ *"Set up my DualSense as the GameCube controller on port 1 in Dolphin using the Default mapping, and
save it as a profile called DualSense."*
Wii: 🗣️ *"Set up Wii Remote 1 as an emulated Wii Remote on my DualSense, with the pointer on the right
stick and motion for tilt."* Same for the 8BitDo (Switch mode gives motion on gyro models).

✅ **Test:** a GameCube game (C-stick, analog triggers) and a Wii game (pointer, shake).

---

## RPCS3 (PS3)

**1. Global upscaling**
🗣️ *"Set RPCS3 to 200% resolution scale with Vulkan, 16x anisotropic filtering and FSR output scaling."*
150% ≈ 1080p, 200% ≈ 1440p, 300% ≈ 4K. Heavy games may need 100–150%.

**2. Per-game overrides**
🗣️ *"List my PS3 games with their serials."* (from RPCS3's game list)
🗣️ *"Set Demon's Souls to 300% as a per-game setting."*
RPCS3 needs a per-game config created in its own window first. Retrozashi does that (right-click
→ Create Custom Configuration), then edits it.

**3. Controllers**
🗣️ *"Set up player 1 in RPCS3 with the DualSense handler for my DualSense and save."*
8BitDo: 🗣️ *"Set up my 8BitDo as player 1 in RPCS3 with the SDL handler."*

✅ **Test:** sticks, triggers, motion (DualSense handler), and rumble.

---

## RetroArch (many consoles)

RetroArch runs a **core** per console. Settings live in three places: global settings, **core options**
(per core, where 3D upscaling lives) and **overrides/remaps** (per core or per game). Retrozashi handles
all three, and reads your **playlists** to know which core each game uses.

**1. Controllers (once for every core)**
🗣️ *"Set RetroArch's controller driver to sdl2, make sure autoconfig is on, player 1 is my DualSense, and
L3+R3 opens the menu."*
8BitDo in X-input mode: 🗣️ *"Use the xinput driver in RetroArch for my 8BitDo."* If a pad isn't
recognised, it maps it once in Settings → Input → Port 1 Controls and saves a controller profile.

**2. Global video**
🗣️ *"Set RetroArch to the Vulkan driver, fullscreen, with integer scaling for sharp pixels."*

**3. 3D upscaling per core (core options)**
Run a game on the core once first, so RetroArch writes that core's options. Then, for example:
🗣️ *"Set Beetle PSX HW's internal resolution to 4x for all PS1 games."*
🗣️ *"Set ParaLLEl-RDP upscaling to 4x in Mupen64Plus-Next."* (N64)
🗣️ *"Set Flycast to 1920x1440 internal resolution."* (Dreamcast)
If it doesn't know a core's exact values, it changes the option once in Quick Menu → Core Options and
learns them.

**4. Per-game**
🗣️ *"List my RetroArch games."* (from your playlists: label, core and ready-made IDs)
🗣️ *"Set Crash Bandicoot to 8x internal resolution, just for that game."* (a game-level `.opt`)
🗣️ *"Turn on a CRT shader just for Super Metroid."* (a shader preset saved as a game preset in the
Quick Menu)
🗣️ *"For Super Metroid, swap A and B."* (a game remap)
Games added with core "DETECT" have no core yet. Retrozashi asks which core, or loads the game once.

✅ **Test:** launch a game from RetroArch. Check the pad layout, the menu combo (L3+R3), and that 3D games
look sharp. Quick Menu → Core Options shows the resolution you set.

---

## Undo and safety (all emulators)

- **↶ Undo** (right panel) reverts every config file the last request changed, per-game files included,
  without an API call.
- 🗣️ *"Undo the last PCSX2 change"* or *"Show me Dolphin's backups"* restores from timestamped backups.
- **Ctrl+Alt+K** / **HALT** stops all mouse and keyboard control immediately.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Clicks do nothing in an emulator | It's running as admin; run it normally. |
| Controller missing from the device list | Connect it before starting the emulator; close Steam; on 8BitDo, re-enter the mode. |
| Doubled or ghost inputs | Steam Input is still on for that pad. |
| A/B (Cross/Circle) swapped | 8BitDo in Switch mode uses Nintendo labels. Ask: *"swap the face buttons for player 1 in <emulator>"*. |
| "No ID" for a game | Compressed image (.chd/.rvz/.cso). Retrozashi reads the ID from the emulator's Properties instead. |
| "diff shows no changes" | The option was cancelled instead of OK'd, or it only saves on exit. Ask it to retry. |
| Anything else | `%APPDATA%\Retrozashi\retrozashi.log` has details. |

## Send these back afterwards 🙏

Paste or attach **`%APPDATA%\Retrozashi\learned_settings.json`**. It holds the real values for your
emulator builds, so I can make them the built-in defaults.
