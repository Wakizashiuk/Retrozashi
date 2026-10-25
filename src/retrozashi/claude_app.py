"""Register Retrozashi with the Claude desktop app (claude_desktop_config.json -> mcpServers)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .emulators import backups

SERVER_NAME = "retrozashi"


def config_candidates() -> list[Path]:
    """Installer build, then the Microsoft Store build (which keeps its data in a package folder)."""
    out = []
    if os.environ.get("APPDATA"):
        out.append(Path(os.environ["APPDATA"]) / "Claude" / "claude_desktop_config.json")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for pkg in sorted(Path(local, "Packages").glob("Claude_*")) if Path(local, "Packages").is_dir() else []:
            out.append(pkg / "LocalCache" / "Roaming" / "Claude" / "claude_desktop_config.json")
    return out


def server_entry() -> dict[str, object]:
    if getattr(sys, "frozen", False):
        return {"command": sys.executable, "args": ["--mcp"]}
    return {"command": sys.executable, "args": ["-m", "retrozashi", "--mcp"]}


def install(paths: list[Path] | None = None, entry: dict[str, object] | None = None) -> list[dict[str, object]]:
    """Add/update our server in every Claude app config found (creating the standard one if none
    exists). Other servers and settings are kept; each existing file is backed up first."""
    paths = paths if paths is not None else config_candidates()
    existing = [p for p in paths if p.parent.is_dir()]
    targets = existing or paths[:1]
    entry = entry or server_entry()
    results = []
    for path in targets:
        data: dict = {}
        if path.exists():
            text = path.read_text(encoding="utf-8-sig").strip()
            data = json.loads(text) if text else {}
            backups.backup_file(path)
        servers = data.setdefault("mcpServers", {})
        updated = SERVER_NAME in servers
        servers[SERVER_NAME] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        results.append({"config": str(path), "updated_existing": updated, "other_servers": len(servers) - 1})
    return results
