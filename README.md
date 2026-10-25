# Retrozashi 🎮

Talk to your emulators. Retrozashi is a Windows desktop agent that configures and controls
PCSX2, DuckStation, RPCS3, Dolphin, Eden and RetroArch from voice or text. It edits config files
directly when it can, operates dialogs through Windows UI Automation next, and falls back to
Claude Computer Use (screenshots + mouse/keyboard) for everything else.

> "Configure PCSX2 for 4K rendering" → reads `PCSX2.ini`, sets `upscale_multiplier = 6`, keeps a `.bak`, re-reads the file to verify.
> "Set up my controller for DuckStation" → opens DuckStation's controller settings and maps your pad, checking each step with screenshots.

> "Set Eden to 3x upscaling, but only 1.5x for Tears of the Kingdom" → global + per-game override files, values learned for your Eden build.
> "Set up my DualSense in Eden" → Eden's Controls screen through UI Automation: Input Device → DualSense, Pro Controller, motion on.

> "Play GT4" → launches your **GT4 profile**: applies its settings (4K, Vulkan…), boots the game, and puts your normal settings back when PCSX2 closes. Profiles are made in the 🎮 Profiles tab.

> Streaming with **Moonlight + Vibepollo**? Retrozashi makes each emulator stream-ready on request and switches controller mappings automatically at every stream start to match Vibepollo's virtual pad.

**Kill switch: `Ctrl+Alt+K`** halts all automated mouse and keyboard input immediately, even over fullscreen games.

## Get the .exe

Every CI run on GitHub builds **`Retrozashi-win64.zip`**: open the run's summary page and download it
from *Artifacts*. Unzip it anywhere and run `Retrozashi\Retrozashi.exe`. On first launch it asks
for your Anthropic API key and stores it in **Windows Credential Manager**. The first voice command
downloads the Whisper model (~250 MB, once).

Build it yourself:
```powershell
.\scripts\build.ps1            # -> dist\Retrozashi\Retrozashi.exe + dist\Retrozashi-win64.zip
.\scripts\build.ps1 -NoVoice   # smaller build without speech-to-text
```
Logs: `%APPDATA%\Retrozashi\retrozashi.log`. Check a bundle with `Retrozashi.exe --self-test report.json`.

## Run from source (Windows, Python 3.11+)

```powershell
git clone <this repo>; cd Retrozashi
py -m venv .venv; .venv\Scripts\activate
pip install -e ".[voice,uia,dev]"
python -m retrozashi   # asks for your API key on first run (or set $env:ANTHROPIC_API_KEY)
```

Optional: copy `settings.example.toml` to `%APPDATA%\Retrozashi\settings.toml` and adjust it.

## Docs
- **[First run](docs/FIRST_RUN.md)**: step-by-step checklists for controllers, upscaling and per-game settings in Eden, PCSX2, DuckStation, Dolphin, RPCS3 and RetroArch
- [Architecture](docs/ARCHITECTURE.md): stack, API wiring, context loop, safety model
- [Roadmap](docs/ROADMAP.md): what's built and what's next

## Tests
```
pytest
```
The logic tests (scaling, key mapping, INI editing, the agent loop with a fake Claude client) run on any OS.
Win32 smoke tests (real `SendInput` structs, capture, UIA) run on Windows. CI runs both on `windows-latest` and `ubuntu-latest`.
