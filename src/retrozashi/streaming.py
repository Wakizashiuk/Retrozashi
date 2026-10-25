"""Vibepollo (Sunshine-family host) integration: run `Retrozashi --sync-controllers` at every stream start.

Vibepollo keeps its settings in config/sunshine.conf under its install folder. Global prep commands
are one line holding a JSON list, e.g.
    global_prep_cmd = [{"do":"C:\\Tools\\x.exe","undo":"","elevated":false}]
We add (or update) our entry and keep every other line and command untouched, with a backup first.
The prep command must not block the stream: `--sync-controllers` starts a detached background
worker and returns at once; the worker waits for Vibepollo's virtual pad to appear, then syncs.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from .emulators import backups

HOST_FOLDERS = ("Vibepollo", "Apollo", "Sunshine")
MARKER = "--sync-controllers"
_PREP = re.compile(r"^\s*global_prep_cmd\s*=\s*(?P<value>.*?)\s*$")


def find_host_config(extra_roots: list[Path] | None = None) -> Path | None:
    """config/sunshine.conf of an installed Vibepollo (preferred), Apollo or Sunshine."""
    roots = list(extra_roots or [])
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]))
    for name in HOST_FOLDERS:
        for root in roots:
            conf = root / name / "config" / "sunshine.conf"
            if conf.exists():
                return conf
    return None


def sync_command() -> str:
    """The command line Vibepollo should run: the frozen exe, or this Python when run from source."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {MARKER}'
    return f'"{sys.executable}" -m retrozashi {MARKER}'


def install_prep_command(conf: Path, command: str) -> dict[str, object]:
    """Add/update our global prep command. Raises PermissionError when the file needs admin rights."""
    text = conf.read_bytes().decode("utf-8-sig", errors="replace") if conf.exists() else ""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    entry = {"do": command, "undo": "", "elevated": False}
    idx = next((i for i, line in enumerate(lines) if _PREP.match(line)), None)
    cmds: list[dict] = []
    if idx is not None:
        raw = _PREP.match(lines[idx]).group("value")
        if raw:
            try:
                cmds = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"Can't read Vibepollo's global_prep_cmd ({e}); add the command in its Web UI instead") from e
    replaced = False
    for i, c in enumerate(cmds):
        if MARKER in str(c.get("do", "")):
            cmds[i] = {**c, **entry}
            replaced = True
    if not replaced:
        cmds.append(entry)
    new_line = f"global_prep_cmd = {json.dumps(cmds, separators=(',', ':'))}"
    if idx is None:
        lines.append(new_line)
    else:
        lines[idx] = new_line
    bak = backups.backup_file(conf) if conf.exists() else None
    conf.write_bytes((newline.join(lines) + newline).encode("utf-8"))
    return {"config": str(conf), "command": command, "updated_existing": replaced,
            "other_prep_commands": len(cmds) - 1, "backup": str(bak) if bak else None,
            "next": "Restart Vibepollo (tray icon or Web UI -> Troubleshooting -> Restart) to load it."}


def start_background_sync(wait: int = 45) -> None:
    """Called by Vibepollo's prep command: spawn a detached worker and return immediately."""
    args = [sys.executable] + ([] if getattr(sys, "frozen", False) else ["-m", "retrozashi"])
    args += ["--sync-controllers-worker", str(wait)]
    flags = 0x00000008 | 0x00000200 | 0x08000000 if sys.platform == "win32" else 0  # DETACHED, NEW_GROUP, NO_WINDOW
    subprocess.Popen(args, creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sync_worker(sets, detect, wait: int, log=print, poll: float = 1.0, clock=time.monotonic, sleep=time.sleep):
    """Poll the connected pads for `wait` seconds and sync whenever they change, so the virtual pad
    Vibepollo creates a moment after the stream starts is picked up."""
    deadline = clock() + wait
    last: list[str] | None = None
    results = []
    while True:
        pads = detect()
        if pads != last and set(pads) & sets.known_pads():
            results = sets.sync(pads)
            log(f"Controller sync for pads {pads}: {results}")
            last = pads
        if clock() >= deadline:
            return results
        sleep(poll)
