# Retrozashi 🎮

Talk to your emulators. Retrozashi is a Windows desktop agent that configures and controls
PCSX2, DuckStation, RPCS3, Dolphin and Eden from voice or text. It edits config files
directly when it can, and drives the GUI with Claude Computer Use when it can't.

> "Configure PCSX2 for 4K rendering" → reads `PCSX2.ini`, sets `upscale_multiplier = 6`, keeps a `.bak`, re-reads the file to verify.
> "Set up my controller for DuckStation" → opens DuckStation's controller settings and maps your pad, checking each step with screenshots.

**Kill switch: `Ctrl+Alt+K`** halts all automated mouse and keyboard input immediately, even over fullscreen games.

## Quick start (Windows, Python 3.11+)

```powershell
git clone <this repo>; cd Retrozashi
py -m venv .venv; .venv\Scripts\activate
pip install -e ".[voice,dev]"
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python -m retrozashi
```

Optional: copy `settings.example.toml` to `%APPDATA%\Retrozashi\settings.toml` and adjust it.

## Docs
- [Architecture](docs/ARCHITECTURE.md): stack, API wiring, context loop, safety model
- [Roadmap](docs/ROADMAP.md): what's built and what's next

## Tests
```
pytest
```
The logic tests (scaling, key mapping, INI editing, the agent loop with a fake Claude client) run on any OS.
