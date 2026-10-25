"""RetroArch-style flat config files (retroarch.cfg, overrides .cfg, core options .opt, remaps .rmp):
one `key = "value"` per line, no sections, `#` comments. Edited in place like IniDocument."""
from __future__ import annotations

import re
from pathlib import Path

_LINE = re.compile(r'^\s*(?P<key>[A-Za-z0-9_\-.]+)\s*=\s*(?P<value>.*?)\s*$')


def unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


class CfgDocument:
    def __init__(self, text: str = "") -> None:
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.lines = text.splitlines()

    @classmethod
    def load(cls, path: Path) -> "CfgDocument":
        return cls(path.read_bytes().decode("utf-8-sig", errors="replace"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(self.newline.join(self.lines) + self.newline, encoding="utf-8", newline="")
        tmp.replace(path)

    def _index(self, key: str) -> int | None:
        for i, line in enumerate(self.lines):
            m = _LINE.match(line)
            if m and m.group("key") == key:
                return i
        return None

    def get(self, key: str) -> str | None:
        i = self._index(key)
        return None if i is None else unquote(_LINE.match(self.lines[i]).group("value"))

    def set(self, key: str, value: str) -> str | None:
        """RetroArch always quotes values; a value passed with quotes is kept as is."""
        quoted = value if len(value) >= 2 and value[0] == value[-1] == '"' else f'"{value}"'
        old = self.get(key)
        i = self._index(key)
        if i is None:
            self.lines.append(f"{key} = {quoted}")
        else:
            self.lines[i] = f"{key} = {quoted}"
        return old

    def remove(self, key: str) -> bool:
        i = self._index(key)
        if i is None:
            return False
        del self.lines[i]
        return True

    def items(self) -> dict[str, str]:
        out = {}
        for line in self.lines:
            m = _LINE.match(line)
            if m:
                out[m.group("key")] = unquote(m.group("value"))
        return out
