"""Register Retrozashi with the Claude desktop app (claude_desktop_config.json -> mcpServers)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

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


def is_remote_path(path: Path) -> bool:
    """UNC path or a mapped network drive (e.g. a NAS on Z:)."""
    text = str(path)
    if text.startswith(("\\\\", "//")):
        return True
    if sys.platform != "win32" or len(text) < 2 or text[1] != ":":
        return False
    import ctypes

    DRIVE_REMOTE = 4
    return ctypes.WinDLL("kernel32").GetDriveTypeW(text[:2] + "\\") == DRIVE_REMOTE


def local_app_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Programs" / "Retrozashi"


def ensure_local_copy(exe: Path | None = None, dest: Path | None = None,
                      remote: Callable[[Path], bool] = is_remote_path) -> Path:
    """The Claude app starts Retrozashi silently in the background. From a network drive Windows can
    stall that start on an unseen security prompt, and a 160 MB folder loads slowly, so copy the app
    folder to this PC first and return the exe Claude should run."""
    exe = Path(exe or sys.executable)
    if not remote(exe):
        return exe
    dest = dest or local_app_dir()
    shutil.copytree(exe.parent, dest, dirs_exist_ok=True)
    return dest / exe.name


def install(paths: list[Path] | None = None, entry: dict[str, object] | None = None) -> list[dict[str, object]]:
    """Add/update our server in every Claude app config found (creating the standard one if none
    exists). Other servers and settings are kept; each existing file is backed up first."""
    paths = paths if paths is not None else config_candidates()
    existing = [p for p in paths if p.parent.is_dir()]
    targets = existing or paths[:1]
    if entry is None:
        entry = server_entry()
        if getattr(sys, "frozen", False):
            entry["command"] = str(ensure_local_copy())
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


# ---- connection check ("Check Claude app" button, --doctor) -----------------------------
OK, WARN, FAIL = "ok", "warn", "fail"
ICONS = {OK: "✅", WARN: "⚠️", FAIL: "❌"}


@dataclass
class Finding:
    level: str
    text: str
    fix: str | None = None  # "register": install() repairs it


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    log_path: Path | None = None
    log_tail: list[str] = field(default_factory=list)

    def add(self, level: str, text: str, fix: str | None = None) -> None:
        self.findings.append(Finding(level, text, fix))

    @property
    def worst(self) -> str:
        levels = {f.level for f in self.findings}
        return FAIL if FAIL in levels else WARN if WARN in levels else OK

    @property
    def fixable(self) -> bool:
        return any(f.fix == "register" for f in self.findings)

    def summary(self) -> str:
        return "\n".join(f"{ICONS[f.level]} {f.text}" for f in self.findings)

    def text(self) -> str:
        out = [f"Retrozashi → Claude app connection check ({time.strftime('%Y-%m-%d %H:%M')})", "", self.summary()]
        if self.log_path:
            out += ["", f"Last lines of {self.log_path}:", *self.log_tail]
        return "\n".join(out)


def handshake(entry: dict[str, object], timeout: float = 45.0) -> tuple[int, float]:
    """Start the server exactly as the Claude app does and list its tools: (tool count, seconds)."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "doctor"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    start = time.monotonic()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run([str(entry["command"]), *map(str, entry.get("args") or [])],
                          input="".join(json.dumps(m) + "\n" for m in msgs), capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout, creationflags=flags)
    replies = {r.get("id"): r for r in map(json.loads, filter(str.strip, proc.stdout.splitlines()))}
    if 2 not in replies or "result" not in replies[2]:
        raise RuntimeError((proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()[-500:])
    return len(replies[2]["result"]["tools"]), time.monotonic() - start


DESKTOP_APP_DIRS = ("anthropicclaude", "\\programs\\claude\\", "\\windowsapps\\claude_")  # installer, Store builds


def _claude_start_times() -> list[float]:
    """Start times of the Claude desktop app's processes. Claude Code (the CLI) is also claude.exe,
    so match on the desktop app's install folder."""
    import psutil

    times = []
    for proc in psutil.process_iter(["name", "exe", "create_time"]):
        exe = (proc.info.get("exe") or "").lower()
        if (proc.info.get("name") or "").lower() == "claude.exe" and any(d in exe for d in DESKTOP_APP_DIRS):
            times.append(proc.info["create_time"])
    return times


def _tail(path: Path, n: int, must_contain: str | None = None) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if must_contain:
        lines = [line for line in lines if must_contain in line.lower()]
    return [line if len(line) <= 400 else line[:400] + " …" for line in lines[-n:]]


def _is_error_line(line: str) -> bool:
    low = line.lower()
    if "message from server" in low or "message from client" in low:
        return False  # protocol traffic: tool descriptions mention errors too
    return any(w in low for w in ("error", "enoent", "failed", "timed out", "timeout"))


def _check_start(report: Report, entry: dict[str, object],
                 run_handshake: Callable[[dict[str, object]], tuple[int, float]] | None) -> None:
    if run_handshake is None:
        return
    try:
        tools, secs = run_handshake(entry)
    except Exception as e:  # timeout, crash, blocked by antivirus ...
        report.add(FAIL, f"Retrozashi didn't start the way the Claude app starts it: {type(e).__name__}: {e}")
    else:
        if secs > 20:
            report.add(WARN, f"Retrozashi starts but slowly ({secs:.0f} s); the Claude app may give up "
                             f"waiting. Antivirus scanning or a slow drive are the usual causes.")
        else:
            report.add(OK, f"Retrozashi starts and answers like it should ({tools} tools, {secs:.1f} s).")


def diagnose(paths: list[Path] | None = None, entry: dict[str, object] | None = None,
             run_handshake: Callable[[dict[str, object]], tuple[int, float]] | None = handshake,
             claude_started: Callable[[], list[float]] = _claude_start_times,
             remote: Callable[[Path], bool] = is_remote_path) -> Report:
    """Everything that stops the Claude app from seeing Retrozashi, in plain words."""
    report = Report()
    paths = paths if paths is not None else config_candidates()
    entry = entry or server_entry()
    dirs = [p for p in paths if p.parent.is_dir()]
    if not dirs:
        report.add(FAIL, "The Claude desktop app isn't installed for this Windows user (no Claude settings "
                         "folder). Install it from claude.ai/download, open it once and sign in.")
        _check_start(report, entry, run_handshake)
        return report

    registered_at = 0.0
    from_network = remote(Path(str(entry["command"])))
    local_copy_registered = False
    for cfg in dirs:
        if not cfg.exists():
            report.add(FAIL, f"Retrozashi isn't added to the Claude app yet ({cfg}).", "register")
            continue
        try:
            text = cfg.read_text(encoding="utf-8-sig").strip()
            data = json.loads(text) if text else {}
        except (OSError, ValueError) as e:
            report.add(FAIL, f"The Claude app's settings file is broken, so Claude ignores every tool server: "
                             f"{cfg}: {e}. Fix or delete that file, then press Fix it.")
            continue
        mine = (data.get("mcpServers") or {}).get(SERVER_NAME)
        if not isinstance(mine, dict):
            report.add(FAIL, f"Retrozashi isn't added to the Claude app yet ({cfg}).", "register")
            continue
        command = Path(str(mine.get("command", "")))
        if not command.is_file():
            report.add(FAIL, f"The Claude app points at {command}, which no longer exists "
                             f"(was the Retrozashi folder moved or deleted?).", "register")
            continue
        if from_network and not remote(command):
            local_copy_registered = True  # opened from the NAS, but Claude runs the copy on this PC: right
            report.add(OK, f"Retrozashi is added to the Claude app, using the copy on this PC ({command}).")
        elif str(command) != str(entry["command"]):
            report.add(WARN, f"The Claude app runs a different copy of Retrozashi: {command}. "
                             f"Press Fix it to use this one ({entry['command']}).", "register")
        else:
            report.add(OK, f"Retrozashi is added to the Claude app ({cfg}).")
        registered_at = max(registered_at, cfg.stat().st_mtime)

    _check_start(report, entry, run_handshake)
    if from_network and not local_copy_registered:
        report.add(WARN, "Retrozashi is running from a network drive. Press Fix it to copy it to this PC "
                         "(Windows can silently block programs started from a network drive).", "register")

    try:
        started = claude_started()
    except Exception:
        started = []
    if not started:
        report.add(WARN, "The Claude app isn't running. Open it, start a new chat and ask "
                         "\"Using Retrozashi, list my emulators\".")
    elif registered_at and min(started) < registered_at:
        report.add(WARN, "The Claude app was already open when Retrozashi was added, so it hasn't loaded it. "
                         "Quit it fully (right-click its icon by the clock → Quit, not just the X) and open it again.")
    elif registered_at:
        report.add(OK, "The Claude app is running and started after Retrozashi was added.")
    else:
        report.add(OK, "The Claude app is running.")

    for cfg in dirs:
        logs = cfg.parent / "logs"
        own = logs / f"mcp-server-{SERVER_NAME}.log"
        if own.is_file():
            report.log_path, report.log_tail = own, _tail(own, 20)
        elif (logs / "mcp.log").is_file():
            tail = _tail(logs / "mcp.log", 20, SERVER_NAME)
            if tail:
                report.log_path, report.log_tail = logs / "mcp.log", tail
        if report.log_path:
            errors = [line for line in report.log_tail if _is_error_line(line)]
            if errors:
                report.add(FAIL, f"The Claude app's log for Retrozashi reports a problem: {errors[-1].strip()[:300]}")
            break
    else:
        if started and registered_at and min(started) > registered_at:
            report.add(WARN, "The Claude app hasn't tried to start Retrozashi yet. Update the Claude app "
                             "(Help → Check for updates) and make sure you're using the desktop app, not the website.")
    return report
