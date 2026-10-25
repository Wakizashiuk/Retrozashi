"""Thin ctypes layer over the Win32 calls Retrozashi needs: DPI awareness, SendInput,
global hotkeys and capture exclusion. Importable on any OS; calls fail off Windows."""
from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Callable

from .keymap import EXTENDED_VKS

IS_WINDOWS = sys.platform == "win32"

# Tag every event we inject so low-level hooks can tell agent input from yours.
RETROZASHI_INPUT_TAG = 0x52455452  # "RETR"

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP = 0x0001, 0x0002
KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x0004, 0x0008
WHEEL_DELTA = 120
MOD_NOREPEAT = 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
WDA_EXCLUDEFROMCAPTURE = 0x11
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _user32():
    if not IS_WINDOWS:
        raise OSError("Win32 input is only available on Windows")
    return ctypes.WinDLL("user32", use_last_error=True)


def enable_dpi_awareness() -> None:
    """Call once at startup, before any window or capture exists. Without it, Windows lies
    about coordinates on scaled (125%/150%) monitors and every click lands in the wrong place."""
    if not IS_WINDOWS:
        return
    user32 = _user32()
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    if not user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)):
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = _user32().SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        # Usually UIPI: the target window runs elevated and we don't.
        raise OSError(ctypes.get_last_error(), "SendInput was blocked (is the target app running as admin?)")


def _mouse(flags: int, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, RETROZASHI_INPUT_TAG)))


def _key(vk: int, up: bool) -> INPUT:
    # Scan codes, not VKs: emulators read input through DirectInput/RawInput and ignore VK-only events.
    scan = _user32().MapVirtualKeyW(vk, 0)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0)
    return INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(0, scan, flags, 0, RETROZASHI_INPUT_TAG)))


def set_cursor_pos(x: int, y: int) -> None:
    """x, y are physical pixels in virtual-desktop space (monitors left of the primary are negative)."""
    if not _user32().SetCursorPos(int(x), int(y)):
        raise ctypes.WinError(ctypes.get_last_error())


def get_cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32().GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def mouse_button(button: str, down: bool) -> None:
    down_flag, up_flag = _BUTTON_FLAGS[button]
    _send(_mouse(down_flag if down else up_flag))


def mouse_wheel(clicks: int, horizontal: bool = False) -> None:
    """Positive clicks = up (or right when horizontal)."""
    _send(_mouse(MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, clicks * WHEEL_DELTA))


def key_down(vk: int) -> None:
    _send(_key(vk, up=False))


def key_up(vk: int) -> None:
    _send(_key(vk, up=True))


def type_unicode(text: str) -> None:
    """Layout-independent typing: each UTF-16 code unit goes in as a KEYEVENTF_UNICODE packet."""
    events: list[INPUT] = []
    raw = text.encode("utf-16-le")
    for i in range(0, len(raw), 2):
        code = int.from_bytes(raw[i:i + 2], "little")
        for up in (False, True):
            flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
            events.append(INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=KEYBDINPUT(0, code, flags, 0, RETROZASHI_INPUT_TAG))))
    if events:
        _send(*events)


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM) if IS_WINDOWS else None
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
SW_RESTORE = 9
VK_MENU = 0x12


def _dwm_attr(hwnd: int, attr: int, out) -> bool:
    return ctypes.WinDLL("dwmapi").DwmGetWindowAttribute(
        wintypes.HWND(hwnd), attr, ctypes.byref(out), ctypes.sizeof(out)) == 0


def list_windows(pids: set[int]) -> list[tuple[int, str]]:
    """Visible, titled, non-cloaked top-level windows owned by `pids`: [(hwnd, title)], z-order first."""
    user32 = _user32()
    found: list[tuple[int, str]] = []

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        cloaked = wintypes.DWORD()
        if _dwm_attr(hwnd, DWMWA_CLOAKED, cloaked) and cloaked.value:
            return True  # hidden UWP/virtual-desktop windows report visible but aren't on screen
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        found.append((int(hwnd), buf.value))
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """(left, top, width, height) in physical pixels, without the invisible resize border.
    None if the window is gone or minimized."""
    user32 = _user32()
    if not user32.IsWindow(wintypes.HWND(hwnd)) or user32.IsIconic(wintypes.HWND(hwnd)):
        return None
    r = wintypes.RECT()
    if not _dwm_attr(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, r):
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def focus_window(hwnd: int) -> bool:
    """Restore if minimized and bring to front. Windows only lets the foreground process steal
    focus, so a synthetic Alt tap first makes SetForegroundWindow succeed from the background."""
    user32 = _user32()
    h = wintypes.HWND(hwnd)
    if user32.IsIconic(h):
        user32.ShowWindow(h, SW_RESTORE)
    _send(_key(VK_MENU, up=False), _key(VK_MENU, up=True))
    return bool(user32.SetForegroundWindow(h))


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG), ("hwnd", wintypes.HWND),
                ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]


def run_elevated(exe: str, params: str, timeout_ms: int = 120_000) -> int:
    """Run one command as administrator (the user sees a UAC prompt) and return its exit code.
    Retrozashi itself never runs elevated: that would change the UIPI rules SendInput relies on."""
    info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=0x40,  # SEE_MASK_NOCLOSEPROCESS
                             lpVerb="runas", lpFile=exe, lpParameters=params, nShow=0)
    if not ctypes.WinDLL("shell32").ShellExecuteExW(ctypes.byref(info)):
        raise PermissionError("Administrator permission was declined")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.WaitForSingleObject(info.hProcess, timeout_ms)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hProcess)
    return code.value


def exclude_from_capture(hwnd: int) -> None:
    """Hide Retrozashi's own window from screenshots (Win10 2004+), so Claude never
    sees, or clicks on, its own control panel."""
    if IS_WINDOWS:
        _user32().SetWindowDisplayAffinity(wintypes.HWND(hwnd), WDA_EXCLUDEFROMCAPTURE)


class GlobalHotkey:
    """RegisterHotKey on a dedicated thread with its own message loop. Works while any
    other app (including a fullscreen emulator) has focus, and needs no admin rights."""

    def __init__(self, modifiers: int, vk: int, callback: Callable[[], None]) -> None:
        self._mods, self._vk, self._callback = modifiers | MOD_NOREPEAT, vk, callback
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._error: OSError | None = None
        self._thread = threading.Thread(target=self._run, name="GlobalHotkey", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=2)
        if self._error:
            raise self._error

    def stop(self) -> None:
        if self._thread_id is not None:
            _user32().PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)

    def _run(self) -> None:
        user32 = _user32()
        self._thread_id = ctypes.WinDLL("kernel32").GetCurrentThreadId()
        if not user32.RegisterHotKey(None, 1, self._mods, self._vk):
            self._error = OSError(ctypes.get_last_error(), "Hotkey already registered by another app")
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    self._callback()
        finally:
            user32.UnregisterHotKey(None, 1)
