"""User settings, loaded from %APPDATA%\\Retrozashi\\settings.toml (or ~/.retrozashi on other OSes)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) / "Retrozashi" if base else Path.home() / ".retrozashi"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class AgentSettings:
    model: str = "claude-opus-5-5"
    # Opus 5.5 defaults to "medium"; set explicitly so behaviour doesn't drift between models.
    effort: str = "medium"
    max_tokens: int = 32000
    max_iterations: int = 40
    # Server-side refusal fallback (beta). Auto-disabled for the session if the API rejects it.
    refusal_fallback: bool = True


@dataclass
class SafetySettings:
    # Global hotkey that halts all automated input. Format: modifiers+key, e.g. "ctrl+alt+k".
    kill_hotkey: str = "ctrl+alt+k"
    # Require a confirmation click in the UI before the agent writes any config file.
    confirm_config_writes: bool = False
    # Ask (Yes/No dialog) before the agent closes a running emulator to write its config.
    confirm_close_emulator: bool = True
    # Seconds to pause between batched computer actions so you can see what is happening.
    action_delay: float = 0.15
    # Before a screenshot that follows an action, wait up to this long for the screen to stop changing.
    settle_timeout: float = 1.5


@dataclass
class VoiceSettings:
    enabled: bool = True
    push_to_talk_hotkey: str = "ctrl+alt+space"
    whisper_model: str = "small.en"  # tiny.en / base.en / small.en / medium.en / large-v3
    device: str = "auto"  # "cuda", "cpu" or "auto"
    # Bias the decoder towards emulator jargon it would otherwise mishear.
    initial_prompt: str = (
        "PCSX2, DuckStation, RPCS3, Dolphin, Eden, Vulkan, DirectX, upscaling, 4K, "
        "internal resolution, DualShock, DualSense, Xbox controller, BIOS, ISO."
    )


@dataclass
class Settings:
    # Which monitor the agent sees and controls (1-based, mss numbering). 0 = all monitors stitched.
    target_monitor: int = 1
    agent: AgentSettings = field(default_factory=AgentSettings)
    safety: SafetySettings = field(default_factory=SafetySettings)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    # Optional overrides: emulator id -> path to its .exe.
    emulator_paths: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or app_data_dir() / "settings.toml"
        if not path.exists():
            return cls()
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        return cls(
            target_monitor=raw.get("target_monitor", 1),
            agent=AgentSettings(**raw.get("agent", {})),
            safety=SafetySettings(**raw.get("safety", {})),
            voice=VoiceSettings(**raw.get("voice", {})),
            emulator_paths=raw.get("emulator_paths", {}),
        )
