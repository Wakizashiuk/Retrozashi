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
| Packaging | **PyInstaller** (one-folder) → `Retrozashi.exe` | One-folder starts faster than one-file and keeps the Whisper model cacheable |

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
6. Retrozashi's own window is hidden from capture (`WDA_EXCLUDEFROMCAPTURE`), so Claude never sees or clicks it.

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

## 6. Safety and control

| Layer | Mechanism |
|---|---|
| Global kill hotkey | `RegisterHotKey` (default **Ctrl+Alt+K**) on its own message-loop thread. Works over fullscreen games, no admin needed |
| On-screen HALT | Big red button, always visible |
| Per-action gate | `kill.check()` before every `SendInput`. Typing is chunked; `wait`/`hold_key` use interruptible waits |
| Mid-response abort | Streaming loop checks the flag each event |
| Stuck-input cleanup | On trip, held keys and mouse buttons are released |
| Scope | Single target monitor, clamped coordinates, own window hidden from capture |
| Reversibility | Every config write leaves a `.bak` |
| Prompt guardrails | System prompt forbids browsers, email, banking, deletion and system settings |
| Iteration cap | `max_iterations` (default 40) bounds cost and runaway loops |

**Tip:** run the emulators **un-elevated**. Windows UIPI silently blocks `SendInput` into admin
windows; the executor surfaces that as an error.

## 7. Voice

- Push-to-talk **toggle** on a global hotkey (default Ctrl+Alt+Space) or the 🎤 button. Recording auto-stops at 20 s.
- 16 kHz mono → `faster-whisper` with VAD filter, `beam_size=5`, and an `initial_prompt` listing
  emulator names so "PCSX2" doesn't come out as "PC SX two".
- The transcript is submitted exactly like typed text. The system prompt tells Claude to tolerate transcription slips.
- `small.en` on GPU (float16) or CPU (int8) is the default; switch to `large-v3` for accents or noisy rooms.
