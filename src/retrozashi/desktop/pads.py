"""Which game controllers are connected right now, as vendor:product IDs.

Moonlight pads arrive on the host as Vibepollo's virtual devices, whose type follows the client
pad in "Auto" mode (an Xbox 360 pad for X-input controllers, a DS4/DualSense for PlayStation ones).
Every one of them shows up as a present HID device ("HID\\VID_054C&PID_05C4..."); XInput pads
also expose an "&IG_" HID interface, so one enumeration covers both kinds.
"""
from __future__ import annotations

import ctypes
import re
import sys
from typing import Callable

# Well-known IDs, used only to describe a pad to the user; matching works on any ID.
KNOWN_PADS = {
    "045E:028E": "Xbox 360 controller (Vibepollo/ViGEm virtual pad or wired 360)",
    "054C:05C4": "DualShock 4 (Vibepollo/ViGEm virtual DS4 or a real DS4 v1)",
    "054C:09CC": "DualShock 4 v2",
    "054C:0CE6": "DualSense",
    "054C:0DF2": "DualSense Edge",
    "057E:2009": "Switch Pro Controller (or 8BitDo in Switch mode)",
    "2DC8:3106": "8BitDo controller (X-input)",
}

_HID_ID = re.compile(r"^HID\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(.*)$", re.IGNORECASE)
# Keyboard/mouse/system collections that share a VID (e.g. Microsoft) aren't game controllers.
_NOT_A_PAD = re.compile(r"&COL0?[2-9]|&MI_0[1-9]&COL", re.IGNORECASE)

CM_GETIDLIST_FILTER_ENUMERATOR, CM_GETIDLIST_FILTER_PRESENT = 0x1, 0x100


def _present_hid_ids() -> list[str]:
    cfg = ctypes.WinDLL("cfgmgr32")
    flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
    size = ctypes.c_ulong()
    if cfg.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), "HID", flags) != 0:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if cfg.CM_Get_Device_ID_ListW("HID", buf, size.value, flags) != 0:
        return []
    return [s for s in buf[:size.value].split("\x00") if s]


def pads_from_ids(device_ids: list[str]) -> list[str]:
    """Device instance IDs -> sorted unique 'VID:PID' strings of likely game controllers."""
    found = set()
    for dev in device_ids:
        m = _HID_ID.match(dev)
        if not m:
            continue
        vid, pid, rest = m.group(1).upper(), m.group(2).upper(), m.group(3)
        if _NOT_A_PAD.search(rest):
            continue
        key = f"{vid}:{pid}"
        # XInput interface, a known pad, or a pad maker (Sony, Nintendo, 8BitDo, Valve). Microsoft (045E)
        # only counts via IG_/known IDs, since its keyboards and mice are HID devices too.
        if "&IG_" in rest.upper() or key in KNOWN_PADS or vid in {"054C", "057E", "2DC8", "28DE"}:
            found.add(key)
    return sorted(found)


def connected_pads(list_ids: Callable[[], list[str]] | None = None) -> list[str]:
    """VID:PID of every game controller present now. Empty off Windows."""
    if list_ids is None:
        if sys.platform != "win32":
            return []
        list_ids = _present_hid_ids
    return pads_from_ids(list_ids())


def describe(pad: str) -> str:
    return KNOWN_PADS.get(pad, "game controller")
