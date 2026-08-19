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
    detail = "packaged" if getattr(sys, "frozen", False) else "source"
    try:
        from ._build_info import BUILD_COMMIT  # type: ignore[import-not-found]
    except ImportError:
        BUILD_COMMIT = ""
    if BUILD_COMMIT:
        detail = f"{detail}, {BUILD_COMMIT}"
    return f"{__version__} ({detail})"
