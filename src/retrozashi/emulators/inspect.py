"""Version-proof config discovery: flatten config files to (file, section, key) -> value, so Claude
can search every section and learn exact keys/values by diffing before and after a GUI change.

    snapshot ──▶ Claude flips one option in the emulator's own Configure window ──▶ diff
             └─ "[Renderer] resolution_setup: 2 -> 5" = what "3x" means on *this* build
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Iterable

import yaml

from .ini_store import IniDocument

FlatConfig = dict[tuple[str, str, str], str]  # (file, section, key) -> value


def read_flat(path: Path) -> FlatConfig:
    out: FlatConfig = {}
    if not path.exists():
        return out
    if path.suffix.lower() in (".yml", ".yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        def walk(node: Any, section: str, prefix: str) -> None:
            for k, v in node.items():
                key = f"{prefix}{k}"
                if isinstance(v, dict):
                    walk(v, section, f"{key}/")
                else:
                    out[(str(path), section, key)] = str(v)

        for section, node in data.items():
            if isinstance(node, dict):
                walk(node, str(section), "")
            else:
                out[(str(path), "", str(section))] = str(node)
        return out
    doc = IniDocument.load(path)
    for section in doc.sections():
        for key, value in doc.section(section).items():
            out[(str(path), section, key)] = value
    return out


def snapshot(files: Iterable[Path]) -> FlatConfig:
    out: FlatConfig = {}
    for f in files:
        out.update(read_flat(f))
    return out


def diff(before: FlatConfig, after: FlatConfig) -> list[dict[str, str | None]]:
    changes = []
    for k in sorted(set(before) | set(after)):
        if before.get(k) != after.get(k):
            file, section, key = k
            changes.append({"file": Path(file).name, "section": section, "key": key,
                            "before": before.get(k), "after": after.get(k)})
    return changes


def search(flat: FlatConfig, query: str, limit: int = 150) -> list[dict[str, str]]:
    """Case-insensitive match on section, key or value; every term must match ('player_0 button')."""
    terms = query.lower().split()
    hits = []
    for (file, section, key), value in flat.items():
        hay = f"{section} {key} {value}".lower()
        if all(t in hay for t in terms):
            hits.append({"file": Path(file).name, "section": section, "key": key, "value": value})
            if len(hits) >= limit:
                break
    return hits


SKIP_DIRS = {"windows", "$recycle.bin", "system volume information", "programdata", "node_modules",
             "__pycache__", ".git", "winsxs", "steamapps", "windowsapps", "microsoft"}


def find_files(names: Iterable[str], roots: Iterable[Path], max_depth: int = 4,
               time_budget: float = 15.0) -> list[Path]:
    """Breadth-limited search for any of `names` (case-insensitive) under `roots`."""
    wanted = {n.lower() for n in names}
    deadline = time.monotonic() + time_budget
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        stack = [(Path(root), 0)]
        while stack and time.monotonic() < deadline:
            folder, depth = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_file() and e.name.lower() in wanted:
                        real = os.path.normcase(os.path.realpath(e.path))
                        if real not in seen:
                            seen.add(real)
                            found.append(Path(e.path))
                    elif e.is_dir(follow_symlinks=False) and depth < max_depth and e.name.lower() not in SKIP_DIRS:
                        stack.append((Path(e.path), depth + 1))
                except OSError:
                    continue
    return found


def default_search_roots() -> list[Path]:
    """Where people usually unzip emulators: user folders, Program Files, and emulator/game
    folders at the root of every drive."""
    home = Path.home()
    roots = [home / d for d in ("Desktop", "Downloads", "Documents", "Games", "Emulators")]
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "APPDATA"):
        if os.environ.get(var):
            base = Path(os.environ[var])
            roots.append(base / "Programs" if var == "LOCALAPPDATA" else base)
    if os.name == "nt":
        for letter in "CDEFGHIJ":
            drive = Path(f"{letter}:\\")
            try:
                tops = [p for p in drive.iterdir() if p.is_dir()]
            except OSError:
                continue
            roots += [p for p in tops if any(w in p.name.lower() for w in ("emu", "game", "rom", "switch", "eden"))]
    return [r for r in roots if r.exists()]
