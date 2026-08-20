"""Manage the 'start with Windows' shortcut in the per-user Startup folder.

The shortcut is created through WScript.Shell via PowerShell, which avoids a
pywin32 dependency and keeps the packaged app small.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .usage_api import cli_candidates

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


def open_claude_cli() -> tuple[bool, str]:
    """Open Claude Code in a terminal so it refreshes the OAuth token.

    Claude Code renews the token on startup. This widget only ever reads the
    credentials file — it deliberately never refreshes the token itself, since
    refresh tokens rotate and consuming one here would invalidate the CLI's own
    session. Launching the CLI is the safe way to get a fresh token.
    """
    if os.name != "nt":
        return False, "Only supported on Windows."

    candidates = cli_candidates()
    if not candidates:
        return False, (
            "Could not find the Claude Code CLI. Install it, then run `claude` "
            "once to refresh your sign-in."
        )
    try:
        subprocess.Popen(["cmd", "/c", "start", "Claude Code", candidates[0]])
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not launch Claude Code: {exc}"
    return True, ""


def refresh_sign_in(timeout: int = 120) -> bool:
    """Ask Claude Code to renew its own token, and report whether it did.

    Running the official CLI is the safe way to refresh: it owns the refresh
    token, so rotation stays consistent and the widget never writes credentials
    itself. `claude update` starts the CLI far enough to renew an expired access
    token without opening an interactive session or spending any usage.

    Success is confirmed by re-reading the stored expiry rather than trusting
    the exit code, since the command succeeds for reasons unrelated to auth.
    """
    from .credentials import token_expiry_ms
    from .log import get_logger

    log = get_logger()
    candidates = cli_candidates()
    if not candidates:
        log.warning("refresh_sign_in: no Claude Code CLI found")
        return False

    before = token_expiry_ms()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    executable = candidates[0]
    use_shell = os.name == "nt" and executable.lower().endswith((".cmd", ".bat", ".ps1"))
    command = f'"{executable}" update' if use_shell else [executable, "update"]
    log.info(
        "refresh_sign_in: running `%s update` (expiry before: %s)", executable, before
    )
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=flags,
            shell=use_shell,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("refresh_sign_in: failed to run the CLI: %s", exc)
        return False

    tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-400:]
    log.info(
        "refresh_sign_in: exit=%s output tail: %s", proc.returncode, tail or "(none)"
    )

    after = token_expiry_ms()
    log.info("refresh_sign_in: expiry after: %s (before: %s)", after, before)
    if after is None:
        return False
    return before is None or after > before


def _icon_path() -> str:
    """The generated app icon, if a build produced one."""
    candidate = Path(__file__).resolve().parent.parent / "app.ico"
    return str(candidate) if candidate.exists() else ""


def _shortcut_script(link_expression: str) -> str:
    """PowerShell that writes a shortcut, with `link_expression` naming the path.

    Callers pass either a quoted literal path or an expression that resolves one,
    which lets the Desktop location come from the shell rather than a guess —
    OneDrive redirects it, so %USERPROFILE%\\Desktop is not reliable.
    """
    target, arguments, workdir = _launch_target()
    lines = [
        f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({link_expression});",
        f"$s.TargetPath = '{_ps_quote(target)}';",
        f"$s.Arguments = '{_ps_quote(arguments)}';",
        f"$s.WorkingDirectory = '{_ps_quote(workdir)}';",
        "$s.WindowStyle = 7;",
        "$s.Description = 'Claude usage widget';",
    ]
    icon = _icon_path()
    if icon:
        lines.append(f"$s.IconLocation = '{_ps_quote(icon)}';")
    lines.append("$s.Save()")
    return " ".join(lines)


def create_desktop_shortcut() -> tuple[bool, str]:
    """Put a no-console launcher on the Desktop."""
    if os.name != "nt":
        return False, "Desktop shortcuts are only supported on Windows."
    script = (
        "$d = [Environment]::GetFolderPath('Desktop'); "
        + _shortcut_script(f"(Join-Path $d '{SHORTCUT_NAME}')")
    )
    ok, error = _run_powershell(script)
    return (True, "") if ok else (False, error)


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
    try:
        startup_dir().mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Cannot create the Startup folder: {exc}"

    ok, error = _run_powershell(
        _shortcut_script(f"'{_ps_quote(str(shortcut_path()))}'")
    )
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
