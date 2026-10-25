"""Read game IDs straight from disc images, since file names rarely contain them.

- PS1/PS2 (.iso, .bin, .img, .cue): the serial comes from SYSTEM.CNF in the ISO9660 root,
  e.g. `BOOT2 = cdrom0:\\SLUS_209.84;1` -> SLUS-20984. Works for 2048-byte sectors and raw
  2352-byte (Mode 1 / Mode 2) dumps.
- GameCube/Wii (.iso, .gcm, .wbfs): the 6-character ID at the start of the disc header.
Compressed formats (.chd, .cso, .rvz, ...) return None; the emulator's GUI shows the ID instead.
"""
from __future__ import annotations

import re
from pathlib import Path

PS_SERIAL = re.compile(r"([A-Z]{4})[-_ ]?(\d{3})\.?(\d{2})")
SYNC = b"\x00" + b"\xff" * 10 + b"\x00"


def normalize_ps_serial(text: str) -> str | None:
    """'SLUS_209.84' / 'slus-20984' / 'SLUS20984' -> 'SLUS-20984'."""
    m = PS_SERIAL.search(text.upper())
    return f"{m.group(1)}-{m.group(2)}{m.group(3)}" if m else None


class _Disc:
    def __init__(self, path: Path) -> None:
        self.f = path.open("rb")
        head = self.f.read(16)
        if head[:12] == SYNC:  # raw sectors: user data follows a 16- (Mode 1) or 24-byte (Mode 2) header
            self.sector, self.offset = 2352, 16 if head[15] == 1 else 24
        else:
            self.sector, self.offset = 2048, 0

    def read(self, lba: int, size: int) -> bytes:
        out = bytearray()
        while len(out) < size:
            self.f.seek(lba * self.sector + self.offset)
            chunk = self.f.read(min(2048, size - len(out)))
            if not chunk:  # past the end of a short or non-ISO file
                break
            out += chunk
            lba += 1
        return bytes(out)

    def close(self) -> None:
        self.f.close()


def _iso_file(disc: _Disc, name: str) -> bytes | None:
    pvd = disc.read(16, 2048)
    if pvd[1:6] != b"CD001":
        return None
    root = pvd[156:190]
    lba, size = int.from_bytes(root[2:6], "little"), int.from_bytes(root[10:14], "little")
    directory = disc.read(lba, min(size, 64 * 2048))
    pos = 0
    while pos < len(directory):
        length = directory[pos]
        if length == 0:  # records never straddle sectors: skip to the next one
            pos = (pos // 2048 + 1) * 2048
            continue
        rec = directory[pos:pos + length]
        ident = rec[33:33 + rec[32]].decode("ascii", "replace").split(";")[0]
        if ident.upper() == name:
            return disc.read(int.from_bytes(rec[2:6], "little"), min(int.from_bytes(rec[10:14], "little"), 4096))
        pos += length
    return None


def _cue_target(cue: Path) -> Path | None:
    m = re.search(r'FILE\s+"([^"]+)"', cue.read_text(errors="replace"))
    return cue.parent / m.group(1) if m else None


def read_ps_serial(path: Path) -> str | None:
    if path.suffix.lower() == ".cue":
        target = _cue_target(path)
        return read_ps_serial(target) if target and target.exists() else None
    if path.suffix.lower() not in (".iso", ".bin", ".img"):
        return None
    try:
        disc = _Disc(path)
        try:
            cnf = _iso_file(disc, "SYSTEM.CNF")
        finally:
            disc.close()
    except OSError:
        return None
    if not cnf:
        return None
    m = re.search(rb"BOOT2?\s*=\s*cdrom0?:\\?([A-Za-z]{4}[_-]\d{3}\.?\d{2})", cnf)
    return normalize_ps_serial(m.group(1).decode()) if m else None


def read_gc_wii_id(path: Path) -> str | None:
    offsets = {".iso": 0, ".gcm": 0, ".wbfs": 0x200}
    offset = offsets.get(path.suffix.lower())
    if offset is None:
        return None
    try:
        with path.open("rb") as f:
            f.seek(offset)
            gid = f.read(6).decode("ascii")
    except (OSError, UnicodeDecodeError):
        return None
    return gid if re.fullmatch(r"[A-Z0-9]{6}", gid) else None
