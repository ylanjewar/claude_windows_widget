"""Always-on-top desktop widget showing Claude plan usage on Windows 11."""

from __future__ import annotations

import sys

__version__ = "1.1.0"


def build_info() -> str:
    """Human-readable build identity, e.g. "1.1.0 (packaged, a1b2c3d)".

    A packaged executable is frozen at the commit it was built from, so pulling
    new source does not change it. Stating the commit makes a stale build
    obvious instead of something to deduce from which wording an error uses.
    """
    if getattr(sys, "frozen", False):
        detail = "packaged"
        try:
            from ._build_info import BUILD_COMMIT  # type: ignore[import-not-found]

            if BUILD_COMMIT:
                detail = f"packaged, {BUILD_COMMIT}"
        except ImportError:
            pass
        return f"{__version__} ({detail})"

    # Running from source: a leftover _build_info.py from an old build would
    # report a stale commit, so ask git instead.
    import subprocess
    from pathlib import Path

    detail = "source"
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parent.parent,
        )
        commit = proc.stdout.strip()
        if proc.returncode == 0 and commit:
            detail = f"source, {commit}"
    except (OSError, subprocess.SubprocessError):
        pass
    return f"{__version__} ({detail})"
