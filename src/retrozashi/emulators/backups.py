"""Config backups: every write is preceded by a copy named `<file>.<timestamp>.bak` next to it.

Timestamps carry microseconds so several writes within one second (common: "set 4K, Vulkan and
VSync") each keep their own pre-change snapshot, and sort chronologically as plain strings.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

SUFFIX = ".bak"


def backup_file(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    bak = path.with_name(f"{path.name}.{stamp}{SUFFIX}")
    shutil.copy2(path, bak)
    return bak


def original_of(bak: Path) -> Path:
    """`PCSX2.ini.20261025-101500-123456.bak` -> `PCSX2.ini`."""
    return bak.with_name(bak.name[: -len(SUFFIX)].rsplit(".", 1)[0])


def backups_for(path: Path) -> list[Path]:
    """Newest first."""
    if not path.parent.exists():
        return []
    found = [p for p in path.parent.glob(f"{path.name}.*{SUFFIX}") if original_of(p) == path]
    return sorted(found, key=lambda p: p.name, reverse=True)


def restore(bak: Path) -> Path | None:
    """Copy `bak` over its original, first snapshotting the current file so the restore is undoable too.
    Returns that snapshot (None if the original didn't exist)."""
    target = original_of(bak)
    snapshot = backup_file(target) if target.exists() else None
    shutil.copy2(bak, target)
    return snapshot
