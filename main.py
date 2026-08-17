"""Entry point for packaged builds.

PyInstaller executes its entry script as a top-level module named `__main__`,
with no parent package, so the relative imports in
`claude_usage_widget/__main__.py` cannot resolve. This module uses an absolute
import instead, which works both frozen and unfrozen.

`python -m claude_usage_widget` keeps working through the package's own
`__main__.py`; that path imports the module inside the package, where relative
imports are fine.
"""

from __future__ import annotations

import sys

from claude_usage_widget.app import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
