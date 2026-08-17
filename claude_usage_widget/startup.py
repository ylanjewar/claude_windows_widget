"""Manage the 'start with Windows' shortcut in the per-user Startup folder.

The shortcut is created through WScript.Shell via PowerShell, which avoids a
pywin32 dependency and keeps the packaged app small.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "Claude Usage Widget.lnk"


def startup_dir() -> Path:
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_dir() / SHORTCUT_NAME


def is_enabled() -> bool:
    return shortcut_path().exists()


def _launch_target() -> tuple[str, str, str]:
    """Return (target, arguments, working_dir) for however we are running."""
    if getattr(sys, "frozen", False):
        # PyInstaller build: the .exe launches itself.
        exe = Path(sys.executable).resolve()
        return str(exe), "", str(exe.parent)

    # Running from source: prefer pythonw.exe so no console window appears.
    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.exists():
        interpreter = windowless
    project_root = Path(__file__).resolve().parent.parent
    return str(interpreter), "-m claude_usage_widget", str(project_root)


def _run_powershell(script: str) -> tuple[bool, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=flags,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "powershell failed").strip()
    return True, ""


def enable() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Autostart is only supported on Windows."
    target, arguments, workdir = _launch_target()
    try:
        startup_dir().mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Cannot create the Startup folder: {exc}"

    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}'); "
        "$s.TargetPath = '{target}'; "
        "$s.Arguments = '{args}'; "
        "$s.WorkingDirectory = '{workdir}'; "
        "$s.WindowStyle = 7; "
        "$s.Description = 'Claude usage widget'; "
        "$s.Save()"
    ).format(
        link=_ps_quote(str(shortcut_path())),
        target=_ps_quote(target),
        args=_ps_quote(arguments),
        workdir=_ps_quote(workdir),
    )
    ok, error = _run_powershell(script)
    if not ok:
        return False, error
    return (True, "") if is_enabled() else (False, "Shortcut was not created.")


def disable() -> tuple[bool, str]:
    try:
        shortcut_path().unlink(missing_ok=True)
    except OSError as exc:
        return False, f"Cannot remove the shortcut: {exc}"
    return True, ""


def _ps_quote(value: str) -> str:
    """Escape for a PowerShell single-quoted string."""
    return value.replace("'", "''")
