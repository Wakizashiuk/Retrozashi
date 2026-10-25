# Retrozashi: Technical Architecture

A native Windows app that takes voice or typed commands ("Configure PCSX2 for 4K rendering")
and carries them out. It edits config files directly when it can, and drives the GUI through
Claude Computer Use when it can't. A hotkey kill switch can halt it at any time.

## 1. Tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** | Best Anthropic SDK and Whisper ecosystem; ctypes gives raw Win32 with no compiled extensions |
| UI | **PySide6 (Qt 6)** | Native widgets, LGPL, signal/slot threading model, ships cleanly in PyInstaller |
| LLM | **Claude Opus 5.5** (`claude-opus-5-5`) with the **`computer_toolset_20260801`** toolset | Current GA computer-use toolset; native tools ride alongside it in the same request |
| Input injection | **Win32 `SendInput`** with scan codes plus `SetCursorPos` (ctypes) | Emulators read DirectInput/RawInput. Scan codes reach them; PyAutoGUI's VK-only events often don't |
| Dialog control | **Windows UI Automation** via `pywinauto` (UIA backend) | Qt emulators expose their widgets to UIA: exact, DPI-proof, no mouse movement |
| Screen capture | **mss** (BitBlt/DXGI) + Pillow | Fast multi-monitor capture and correct negative virtual-desktop coordinates |
| Process mgmt | **psutil** + `subprocess` | Find, launch and gracefully close emulators |
| Speech-to-text | **faster-whisper** (CTranslate2), local | Offline, GPU-accelerated, and an `initial_prompt` biases it towards "PCSX2", "DuckStation" etc. |
| Audio | **sounddevice** | Simple 16 kHz mono capture |
| Packaging | **PyInstaller** (one-folder) → `Retrozashi.exe` | One-folder starts in ~1 s (one-file re-extracts ~400 MB every launch) and trips antivirus less; no UPX for the same reason |
| Secrets | **keyring** → Windows Credential Manager | API key never sits in a file or env var; first-run prompt + 🔑 button |

## 2. Component map

```
┌───────────────────────────── Retrozashi.exe ─────────────────────────────┐
│                                                                           │
│  UI thread (Qt)                         Worker thread                     │
│  ┌──────────────────────┐   submit()   ┌──────────────────────────────┐   │
│  │ MainWindow           │ ───────────▶ │ AgentLoop  (agent/loop.py)   │   │
│  │  chat · status · log │              │  Claude API (streamed)       │   │
│  │  HALT button         │ ◀─ EventBus ─│   ├─ computer_* → Executor   │   │
│  │  screenshot preview  │   (Signals)  │   └─ native   → NativeTools  │   │
│  └─────────▲────────────┘              └──────┬───────────────┬───────┘   │
│            │ voice_text                       │               │           │
│  ┌─────────┴────────────┐       ┌─────────────▼────┐   ┌──────▼────────┐  │
│  │ VoiceInput           │       │ ActionExecutor   │   │ Emulator      │  │
│  │ sounddevice→whisper  │       │ SendInput/mss    │   │ Registry      │  │
│  └──────────────────────┘       └─────────▲────────┘   │ ini/yaml/CLI  │  │
│                                           │ check()    └───────────────┘  │
│  Hotkey thread ── RegisterHotKey ──▶ KillSwitch (threading.Event) ────────┤
└───────────────────────────────────────────────────────────────────────────┘
```

| Module | Responsibility |
|---|---|
| `core/runtime.py` | Composition root; single background worker queue |
| `core/events.py` | `EventBus` + typed `AgentEvent`s that drive the status panel |
| `core/safety.py` | `KillSwitch`: global hotkey, `check()`, interruptible `wait()`, release-held-input hooks |
| `agent/loop.py` | The Claude sampling loop, tool routing, batch semantics |
| `agent/tools.py` | Native tools: `list_emulators`, `read_emulator_config`, `set_emulator_setting`, `launch_emulator`, `close_emulator`, `ui_list_controls`, `ui_act` |
| `desktop/uia.py` | UI Automation controller, scoped to registered emulator processes |
| `desktop/screen.py` | Monitor selection, capture, downscale to API limits, coordinate mapping |
| `desktop/executor.py` | Maps the 17 toolset members to Win32 input |
| `desktop/win32.py` | ctypes: DPI awareness, `SendInput`, `RegisterHotKey`, `SetWindowDisplayAffinity` |
| `emulators/` | Adapter framework + PCSX2, DuckStation, Dolphin, RPCS3, Eden |
| `voice/stt.py` | Push-to-talk toggle, faster-whisper transcription |

## 3. Claude API architecture

One `POST /v1/messages` per step, **streamed**, with both tool families declared together:

```python
client.messages.stream(
    model="claude-opus-5-5",
    max_tokens=32000,
    system=SYSTEM_PROMPT,
    tools=[
        {"type": "computer_toolset_20260801"},          # 17 GUI members, no name / display size
        *NATIVE_TOOLS,                                   # strict JSON-schema tools for configs/processes
    ],
    messages=history,                                    # append-only
    thinking={"type": "adaptive"},
    output_config={"effort": "medium"},                  # Opus 5.5 default; raise to "high" for hard GUIs
    cache_control={"type": "ephemeral"},                 # reuse the system+tools+history prefix each step
    extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
    extra_body={"fallbacks": "default"},                 # refusal → fallback model, same call
)
```

Key protocol rules the loop implements (`agent/loop.py`):

- **Action = block `name`.** A computer call looks like `{"type":"tool_use","name":"left_click","toolset_name":"computer","input":{"coordinate":[x,y]}}`.
  Native tools have no `toolset_name`.
- **Batches.** A single turn can hold several `tool_use` blocks. They run in order, and *every* block gets
  exactly one `tool_result` in **one** user message. Each computer result echoes `"toolset_name": "computer"`.
  After a failure, the remaining computer blocks get `is_error` + `"Not executed: an earlier computer action in this turn failed."`.
- **Images.** Only `screenshot` and `zoom` results carry an image; everything else returns `"OK"`.
- **History is append-only.** `response.content` goes back unchanged, thinking blocks included,
  because Opus 5.5's preserved-thinking check rejects edited history.
- **Stop reasons.** `refusal` and `max_tokens` are surfaced in the UI. No `tool_use` blocks means done.
- **Kill switch.** The stream is iterated so a halt closes the connection mid-response. A halt during a
  batch still answers every block (`"Not executed: the user pressed the kill switch."`), so a follow-up
  "continue" works.

### Coordinate pipeline (multi-monitor)

1. `enable_dpi_awareness()` (Per-Monitor V2) runs **before** Qt starts, so every API works in physical pixels.
2. `ScreenCapture` grabs the **selected monitor** only (UI dropdown). Monitors left of or above the
   primary have negative origins.
3. The frame is downscaled so the long edge is ≤1920 (configurable, API max 2576) and
   `ceil(w/28)·ceil(h/28) ≤ 4784` visual tokens. A 4K panel becomes 1920×1080.
4. Claude answers in image pixels → `Frame.to_screen()` divides by the scale, adds the monitor
   origin, and clamps inside that monitor. It can never click on a different screen.
5. `zoom` crops at native resolution for detail, but coordinates stay in full-screenshot space.
6. **Window targeting** (`focus_emulator_window`). Claude can narrow capture to one emulator window,
   found by process id through `EnumWindows` and brought to the front. A 1600×900 settings dialog is
   then sent at full detail instead of as a small part of a downscaled 4K monitor. Clicks are clamped
   inside the window.
   Bounds come from `DWMWA_EXTENDED_FRAME_BOUNDS`, which excludes the invisible resize border.
   Before every click the executor checks that the window hasn't moved or resized since the last
   screenshot. If it has, or if no screenshot of the current target exists yet, the action is refused
   with "take a new screenshot". A closed or minimized window drops capture back to the monitor.
7. Retrozashi's own window is hidden from capture (`WDA_EXCLUDEFROMCAPTURE`), so Claude never sees or clicks it.

### Three control tiers

Claude picks the most precise tool that can do the job, in this order:

| Tier | Tools | Best for | Verification |
|---|---|---|---|
| 1. Config | `read_emulator_config` / `set_emulator_setting` | Anything with a known key; emulator closed | Re-read the file |
| 2. UI Automation | `ui_list_controls` → `ui_act(id, action, value)` | Settings dialogs in Qt emulators: tabs, combo boxes, check boxes, text fields | `ui_act` returns the control's new state |
| 3. Pixels | `computer_toolset_20260801` | Unnamed or custom-drawn controls, in-game menus, "press a button to bind" prompts | Screenshot |

**Example.** "Turn on VSync in PCSX2" while it's running: `ui_list_controls("pcsx2", "Settings")` returns
`{"id":"e7","type":"CheckBox","name":"Enable VSync"}`, then `ui_act("e7","toggle_on")` returns `{"toggled":1}`.
That's done with two tool calls and no screenshots.

UIA safety: the controller only lists windows whose **process belongs to a registered emulator**, so
Claude can't reach any other app this way. Element ids reset on every listing, disabled controls are
refused, and `kill.check()` runs before every action. Invoke/Toggle/Select/Value patterns don't move
the mouse; only controls with none of those patterns fall back to a real click.

## 4. The context loop: Capture → Claude → Execute → Verify

```
 user (voice/text)
      │
      ▼
 ┌─ append user turn ─────────────────────────────────────────────┐
 │  1. CALL      stream request (tools + history)                 │
 │  2. DECIDE    config tools → UI Automation → pixels            │
 │               (most precise tier that can do the job)          │
 │  3. EXECUTE   executor/native tools (kill.check() before each) │
 │  4. OBSERVE   tool_results (+ screenshots) appended            │
 │  5. VERIFY    Claude re-reads config / takes a screenshot      │
 │               and confirms the state before saying "done"      │
 └──── loop until no tool_use, refusal, halt, or max_iterations ──┘
```

Verification is driven by the system prompt and by the loop itself. Claude has to see the
result of each action (a re-read or a screenshot) before it can claim success.

## 5. Emulator integration framework

`EmulatorAdapter` (in `emulators/base.py`) gives each app the same interface:

| Capability | Method | Notes |
|---|---|---|
| Locate | `find_exe()`, `config_candidates()` | Override > running process > default dirs; portable mode first |
| Read | `read_settings(section)` | INI via the formatting-preserving `IniDocument`; YAML for RPCS3 |
| Write | `set_setting(section, key, value)` | Refuses while the emulator runs (it'd overwrite on exit); timestamped `.bak` + atomic replace |
| Run | `launch(game, fullscreen)`, `close()` | Emulator-specific CLI args |
| Teach | `KNOWN_SETTINGS`, `notes` | Surfaced to Claude through `list_emulators` |

**Fallback rule:** if an adapter has no key for a request, or the config doesn't exist yet,
Claude uses the GUI. For enum-heavy apps like Eden, the recommended pattern is to change the option
once via the GUI, re-read the file to learn the value, and use config writes from then on.

To add an app, subclass `IniEmulator` (or `EmulatorAdapter`), fill in `process_names`,
`config_candidates()` and `KNOWN_SETTINGS`, then `registry.register(MyAdapter())`.

### Editing any section, on any build: search, learn-by-diff, guides

Adapters' `KNOWN_SETTINGS` are hints. The tools that make *every* section editable are generic:

| Tool | What it does |
|---|---|
| `read_emulator_config(search=…)` | Finds keys and values matching all the words, across every section, plus extra files such as Eden's saved input profiles |
| `set_emulator_setting` | Writes any section and key, with a backup first |
| `snapshot_config` → GUI change → `diff_config` | Shows exactly which keys and values the emulator wrote, so an enum like "3x resolution" is learned for *this* build |
| `remember_setting` | Saves the learned mapping to `learned_settings.json`; `list_emulators` serves it back so next time it's a direct write |
| `emulator_guide` | Step-by-step GUI playbooks (Eden: `controllers`, `upscaling`, `per_game`) |
| `locate_emulator` | Searches the user folders, Program Files and emulator/game folders on each drive for the exe, and remembers it |
| `close_emulator` | Shows the user a Yes/No dialog first (`confirm_close_emulator`), enforced in code |

### Per-game overrides and game IDs (all emulators)

`for_game(game_id)` returns a *view* of an adapter whose config is that game's override file, so
reads, writes, backups, Undo, search and diffs all work per game without special cases.

| Emulator | Override file | Game ID | How `list_games` gets it |
|---|---|---|---|
| PCSX2 | `gamesettings/<SERIAL>_<CRC>.ini` | `SLUS-20984_E2C8A0D8` | Serial from the disc's SYSTEM.CNF. The CRC comes from PCSX2's Properties once; after that the serial alone resolves |
| DuckStation | `gamesettings/<SERIAL>.ini` | `SCUS-94163` | SYSTEM.CNF in .iso, raw 2352-byte .bin, or via .cue |
| Dolphin | `User/GameSettings/<ID>.ini` (sections `Video_Settings`, `Core`…) | `GM4E01` | 6 bytes of the disc header (.iso/.gcm; .wbfs at 0x200) |
| RPCS3 | `config/custom_configs/config_<SERIAL>.yml` (created in the GUI first) | `BLUS30443` | RPCS3's own `games.yml` |
| Eden | `config/custom/<TitleID>.ini` (+ `\use_global=false`) | `0100…` (16 hex) | Title ID in the file name |
| RetroArch | `config/<Core>/<Game>.cfg` (override), `.opt` (core options), `remaps/<Core>/<Game>.rmp` | `Beetle PSX HW/Crash Bandicoot (USA)` + ` [options]`/` [remap]`; core-wide: `Beetle PSX HW` | Playlists (`.lpl` JSON) + `info/*.info` corename |

`discs.py` reads ISO9660 directly (2048-byte and raw Mode 1/Mode 2 sectors) to find SYSTEM.CNF.
Compressed images (.chd, .cso, .rvz…) return no ID, and the per_game guide says where the emulator
shows it instead. Saved input profiles (PCSX2/DuckStation `inputprofiles/`, Dolphin `Profiles/GCPad|Wiimote`,
RPCS3 `input_configs/`, Eden `input/`) are covered by search and diffs.

### RetroArch

Everything is flat `key = "value"` files (`cfg_store.py` keeps quoting, comments and CRLF). The section
argument is ignored. Three per-game file kinds come from one `game_id` grammar: overrides
(partial `retroarch.cfg`), **core options** (`.opt`, where 3D cores keep internal resolution) and remaps.
Paths like `:\config` are relative to the RetroArch folder. `list_games` maps each playlist entry's core
DLL to its library name through `info/*.info`, falling back to the playlist's default core for `DETECT`.
Launching passes `-L <core>` from the playlist. Menus are drawn by RetroArch, not Windows controls,
so config writes come first and computer use is the fallback.

### Eden (Switch)

- **Config:** `user/config/qt-config.ini` next to `eden.exe` (portable) or `%APPDATA%\eden\config`.
  Every write also sets `key\default=false`, which Qt-settings needs, or Eden ignores the value.
- **Per-game overrides:** `game_id=<16-hex title ID>` targets `config/custom/<TitleID>.ini`, adding
  `key\use_global=false`. The value `global` removes an override. `list_games` reads title IDs from file
  names in Eden's configured game folders.
- **Upscaling:** `[Renderer] resolution_setup / scaling_filter / anti_aliasing …`. The known enum
  values are yuzu-lineage hints, so the agent learns the real ones by diff on first use.
- **Controllers:** bindings embed the device GUID (`"engine:sdl,port:0,guid:…,button:1"`), so mapping
  goes through Eden's Controls screen via UI Automation (Input Device → auto-map, Controller Type →
  Pro Controller). The guide covers Steam Input conflicts and 8BitDo modes. Diffs also cover
  `config/input/*.ini` profiles.

### Claude desktop app mode (`mcp_server.py`, `claude_app.py`)

`Retrozashi.exe --mcp` is a stdio MCP server, so users on a Claude Pro/Max plan need no API key. The Claude
desktop app runs the agent loop, and Retrozashi only executes tools.
- **Protocol:** a dependency-free JSON-RPC 2.0 implementation (`initialize` with version negotiation,
  `tools/list`, `tools/call`, `ping`; notifications ignored). The system prompt is sent as the server
  `instructions`.
- **Tools:** every native tool (config, per-game, guides, profiles, controller sets, Vibepollo, UIA), plus
  computer tools (`screenshot`, `zoom`, `click`, `drag`, `scroll`, `type_text`, `press_keys`…) mapped onto
  the same `ActionExecutor`. Clamping, the stale-frame guard and the Ctrl+Alt+K kill switch all apply.
  After a halt, computer tools refuse until Claude calls `resume_control` with the user's consent, and
  Claude Desktop asks the user to approve tool calls.
- **Closing emulators:** asks through a native Windows Yes/No box, since there's no Qt in this mode.
- **Windowed exe:** `sys.stdin`/`stdout` are `None` even when the Claude app passes pipes, so the inherited
  OS handles are opened directly, and stray prints are diverted to stderr. CI checks this with a real
  handshake against the packaged exe.
- **Install:** `claude_app.install()` merges `mcpServers.retrozashi` into `claude_desktop_config.json`
  (installer and Microsoft Store builds), keeping other servers and making a backup.

### Streaming: Moonlight + Vibepollo (`controller_sets.py`, `streaming.py`, `desktop/pads.py`)

The user plays through Moonlight with Vibepollo, a Sunshine-family host, and uses Retrozashi only
during setup. Two consequences follow:

- **Display:** Vibepollo streams a virtual display at each client's resolution, so emulators are made
  *stream-ready* per emulator on request (`emulator_guide <id> streaming`): borderless fullscreen on launch,
  no pause on focus loss, no exit confirmations, and pad hotkeys for menu and quit. Saved globally.
- **Controllers:** emulators see Vibepollo's virtual pad, whose type follows the client pad in Auto mode
  (Xbox 360 for X-input pads, DS4/DualSense for PlayStation pads). Eden (GUID), Dolphin (device name) and
  RPCS3 (handler) bind to one device type.
  - Each adapter declares its `controller_scope()`, the keys or whole files that make up the mapping.
  - `save_controller_set` captures that scope, tied to the VID:PIDs connected now.
  - `ControllerSets.sync(pads)` restores the best-matching set per emulator. Keys outside the set are
    removed, everything else is untouched, and backups are made first.
- **Stream start:** `install_stream_sync` adds `Retrozashi.exe --sync-controllers` to Vibepollo's
  `global_prep_cmd` in `config/sunshine.conf`. A JSON-aware edit keeps other prep commands, and a backup
  is made. Program Files needs admin, so a one-off elevated helper (`--install-stream-sync`, UAC prompt)
  does just that edit, and the app itself never runs elevated. The prep command spawns a detached worker
  and returns at once, so the stream isn't delayed. The worker polls the pads (cfgmgr32 present HID
  devices) for ~45 s and syncs whenever a known pad appears, because Vibepollo creates the virtual pad
  only after the stream starts.

### Per-game profiles (`profiles.py`, `ui/profiles_panel.py`)

A profile is `{name, emulator, game_path, fullscreen, settings[section,key,value], notes}`, stored in
`%APPDATA%\Retrozashi\profiles.json` and created in the **Profiles** tab. The editor offers each
adapter's known settings. Launching one (double-click, ▶ Launch, or "play GT4" via `launch_profile`)
works like this:

1. Refuse if the emulator is already running, since it would overwrite the change and there'd be nothing clean to revert to.
2. `adapter.apply_settings()`: one backup per file, and **each key's previous value is recorded**.
   Keys that didn't exist before are recorded as absent.
3. Persist the pending revert to `pending_reverts.json`, then launch the emulator with the game.
4. A watcher thread waits for the launched process and every other instance to exit, plus 1 s for
   the config flush. Then `adapter.revert_changes()` puts back **only those keys**, newest first, and
   removes the ones that were absent. Anything you changed in the emulator's menus during play is kept.
5. If the launch fails, the profile is reverted immediately. If Retrozashi closes or crashes mid-game,
   `recover()` on the next start reverts it, or resumes watching if the emulator is still running.

## 6. Safety and control

| Layer | Mechanism |
|---|---|
| Global kill hotkey | `RegisterHotKey` (default **Ctrl+Alt+K**) on its own message-loop thread. Works over fullscreen games, no admin needed |
| On-screen HALT | Big red button, always visible |
| Per-action gate | `kill.check()` before every `SendInput`. Typing is chunked; `wait`/`hold_key` use interruptible waits |
| Mid-response abort | Streaming loop checks the flag each event |
| Stuck-input cleanup | On trip, held keys and mouse buttons are released |
| Scope | Single target monitor, clamped coordinates, own window hidden from capture |
| Reversibility | Every config write leaves a `<file>.<timestamp>.bak` first. **Undo** button reverts the last request's changes; Claude can list/restore backups (only the emulator's own) |
| Settle detection | Screenshots after an action wait (≤1.5 s) until the screen stops changing, so Claude doesn't judge a half-drawn dialog |
| Prompt guardrails | System prompt forbids browsers, email, banking, deletion and system settings |
| Iteration cap | `max_iterations` (default 40) bounds cost and runaway loops |

**Tip:** run the emulators **un-elevated**. Windows UIPI silently blocks `SendInput` into admin
windows; the executor surfaces that as an error.

## 7. Packaging

- `packaging/Retrozashi.spec` bundles the optional extras when they're installed: faster-whisper's
  VAD model, the native DLLs for ctranslate2, onnxruntime, PyAV and PortAudio, and pywinauto/comtypes
  submodules. Unused Qt modules (WebEngine, QML, Multimedia…) are excluded.
- The exe is windowed (`console=False`) and never elevated (`uac_admin=False`). Elevation would change
  the UIPI rules that `SendInput` depends on.
- With no console, `sys.stdout`/`sys.stderr` are `None`. They're redirected to
  `%APPDATA%\Retrozashi\retrozashi.log`, so libraries that print (such as the Hugging Face download
  progress bar) can't crash the app. Unhandled exceptions are logged and shown in a dialog.
- `Retrozashi.exe --self-test report.json` checks Qt, Win32, capture, UIA, voice, keyring and the agent
  inside the frozen bundle. CI runs it on every build before uploading `Retrozashi-win64.zip`.

## 8. Voice

- Push-to-talk **toggle** on a global hotkey (default Ctrl+Alt+Space) or the 🎤 button. Recording auto-stops at 20 s.
- 16 kHz mono → `faster-whisper` with VAD filter, `beam_size=5`, and an `initial_prompt` listing
  emulator names so "PCSX2" doesn't come out as "PC SX two".
- The transcript is submitted exactly like typed text. The system prompt tells Claude to tolerate transcription slips.
- `small.en` on GPU (float16) or CPU (int8) is the default; switch to `large-v3` for accents or noisy rooms.
