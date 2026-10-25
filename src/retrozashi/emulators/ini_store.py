"""A formatting-preserving INI editor.

configparser lowercases keys, chokes on '%' and drops comments/ordering on write, and
emulators are picky (PCSX2 keys are case-sensitive, Qt INIs use `key\\default=`). This
edits lines in place and only touches the exact lines it changes.
"""
from __future__ import annotations

import re
from pathlib import Path

from .backups import backup_file

_SECTION = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")
_KEYVAL = re.compile(r"^(?P<key>[^=;#\[][^=]*?)\s*=\s?(?P<value>.*)$")


class IniDocument:
    def __init__(self, text: str = "") -> None:
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.lines: list[str] = text.splitlines()

    @classmethod
    def load(cls, path: Path) -> "IniDocument":
        # read_bytes, not read_text: universal-newline decoding would hide the file's CRLFs
        return cls(path.read_bytes().decode("utf-8-sig", errors="replace"))

    def save(self, path: Path, backup: bool = False) -> Path | None:
        """Writes atomically. Returns the backup path, if one was made."""
        bak = backup_file(path) if backup and path.exists() else None
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.newline.join(self.lines) + self.newline, encoding="utf-8", newline="")
        tmp.replace(path)
        return bak

    def _find(self, section: str, key: str) -> tuple[int | None, int | None]:
        """(index of key line, index after last line of section) — either may be None."""
        current = None
        sec_end = None
        for i, line in enumerate(self.lines):
            m = _SECTION.match(line)
            if m:
                if current == section:
                    return None, sec_end
                current = m.group("name").strip()
                if current == section:
                    sec_end = i + 1
                continue
            if current == section:
                if line.strip():
                    sec_end = i + 1
                kv = _KEYVAL.match(line)
                if kv and kv.group("key").strip() == key:
                    return i, sec_end
        return None, sec_end

    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        idx, _ = self._find(section, key)
        if idx is None:
            return default
        return _KEYVAL.match(self.lines[idx]).group("value").strip()

    def set(self, section: str, key: str, value: str) -> str | None:
        """Sets key and returns the previous value (None if it was absent)."""
        idx, sec_end = self._find(section, key)
        line = f"{key} = {value}"
        if idx is not None:
            old = _KEYVAL.match(self.lines[idx]).group("value").strip()
            # keep the file's own spacing style around '='
            sep = " = " if " = " in self.lines[idx] else "="
            self.lines[idx] = f"{key}{sep}{value}"
            return old
        if sec_end is None:
            if self.lines and self.lines[-1].strip():
                self.lines.append("")
            self.lines += [f"[{section}]", line]
        else:
            self.lines.insert(sec_end, line)
        return None

    def remove(self, section: str, key: str) -> bool:
        idx, _ = self._find(section, key)
        if idx is None:
            return False
        del self.lines[idx]
        return True

    def get_all(self, section: str, key: str) -> list[str]:
        """Every value of a repeated key (PCSX2/DuckStation list game folders this way)."""
        out, current = [], None
        for line in self.lines:
            m = _SECTION.match(line)
            if m:
                current = m.group("name").strip()
            elif current == section and (kv := _KEYVAL.match(line)) and kv.group("key").strip() == key:
                out.append(kv.group("value").strip())
        return out

    def section(self, section: str) -> dict[str, str]:
        out: dict[str, str] = {}
        current = None
        for line in self.lines:
            m = _SECTION.match(line)
            if m:
                current = m.group("name").strip()
                continue
            if current == section and (kv := _KEYVAL.match(line)):
                out[kv.group("key").strip()] = kv.group("value").strip()
        return out

    def sections(self) -> list[str]:
        return [m.group("name").strip() for line in self.lines if (m := _SECTION.match(line))]
