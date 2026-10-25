"""Translate Claude's xdotool-style key names ("ctrl+s", "Return", "alt+Tab") into Windows virtual-key codes."""
from __future__ import annotations

VK: dict[str, int] = {
    # modifiers
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "super": 0x5B, "win": 0x5B,
    "cmd": 0x5B, "meta": 0x5B,
    "shift_l": 0xA0, "shift_r": 0xA1, "control_l": 0xA2, "control_r": 0xA3, "alt_l": 0xA4, "alt_r": 0xA5,
    "super_l": 0x5B, "super_r": 0x5C,
    # editing / navigation
    "return": 0x0D, "enter": 0x0D, "kp_enter": 0x0D, "tab": 0x09, "escape": 0x1B, "esc": 0x1B,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "space": 0x20,
    "home": 0x24, "end": 0x23, "page_up": 0x21, "prior": 0x21, "page_down": 0x22, "next": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "caps_lock": 0x14, "print": 0x2C, "pause": 0x13, "menu": 0x5D, "scroll_lock": 0x91,
    # punctuation (US layout)
    "minus": 0xBD, "equal": 0xBB, "comma": 0xBC, "period": 0xBE, "slash": 0xBF, "semicolon": 0xBA,
    "apostrophe": 0xDE, "grave": 0xC0, "bracketleft": 0xDB, "bracketright": 0xDD, "backslash": 0xDC,
    # media
    "audiovolumeup": 0xAF, "audiovolumedown": 0xAE, "audiomute": 0xAD,
}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})           # F1=0x70 .. F24=0x87
VK.update({chr(c): c - 32 for c in range(ord("a"), ord("z") + 1)})  # 'a' -> 0x41
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({f"kp_{d}": 0x60 + d for d in range(10)})

# Keys that need KEYEVENTF_EXTENDEDKEY when sent as scan codes.
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D,
                0xA3, 0xA5, 0x6F, 0x90, 0x2C}

MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5C}


def parse_combo(combo: str) -> list[int]:
    """'ctrl+shift+Escape' -> [0x11, 0x10, 0x1B]. Raises ValueError for unknown keys."""
    vks: list[int] = []
    for part in combo.split("+"):
        name = part.strip().lower().replace("-", "_")
        if not name:
            raise ValueError(f"Empty key in combo {combo!r}")
        if name not in VK:
            raise ValueError(f"Unknown key {part!r} in combo {combo!r}")
        vks.append(VK[name])
    return vks


def parse_hotkey(combo: str) -> tuple[int, int]:
    """'ctrl+alt+k' -> (RegisterHotKey modifier flags, vk)."""
    mod_flags = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8, "super": 0x8}
    parts = [p.strip().lower() for p in combo.split("+")]
    *mods, key = parts
    flags = 0
    for m in mods:
        if m not in mod_flags:
            raise ValueError(f"Unknown modifier {m!r} in hotkey {combo!r}")
        flags |= mod_flags[m]
    return flags, parse_combo(key)[0]
