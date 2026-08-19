"""Widget and notification tests. Skipped automatically when PySide6 is absent."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Keep the test run out of the real config location.
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="claude-usage-tests-")

try:
    from PySide6.QtWidgets import QMenu  # noqa: F401

    HAVE_QT = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_QT = False

NOW = datetime.now(timezone.utc)


def iso(**delta) -> str:
    return (NOW + timedelta(**delta)).isoformat().replace("+00:00", "Z")


@unittest.skipUnless(HAVE_QT, "PySide6 is not installed")
class WidgetAppTests(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        from claude_usage_widget import app as app_module

        cls.app_module = app_module
        cls.app = app_module.WidgetApp([sys.argv[0]])

    @classmethod
    def tearDownClass(cls):
        if cls.app is not None:
            cls.app.widget.close()

    def setUp(self):
        self.app.config.set("notified", {})

    # -- colour ---------------------------------------------------------------

    def test_bar_colour_thresholds(self):
        """Only the bar fill changes colour: blue, then yellow at 70, red at 85."""
        colour = self.app.widget.fill_colour
        self.assertEqual(colour(0).name(), "#2f80f5")
        self.assertEqual(colour(69.9).name(), "#2f80f5")
        self.assertEqual(colour(70).name(), "#e0a32e")
        self.assertEqual(colour(84.9).name(), "#e0a32e")
        self.assertEqual(colour(85).name(), "#e5484d")
        self.assertEqual(colour(100).name(), "#e5484d")

    def test_panel_chrome_is_not_recoloured(self):
        from claude_usage_widget import widget as widget_module

        self.assertEqual(widget_module.PANEL_BG.name(), "#202023")

    # -- notifications --------------------------------------------------------

    def _snapshot(self, five_hour_percent: float):
        from claude_usage_widget.usage_api import parse_usage

        return parse_usage(
            {
                "five_hour": {"utilization": five_hour_percent, "resets_at": iso(hours=1)},
                "seven_day": {"utilization": 10, "resets_at": iso(days=2)},
            }
        )

    def _capture(self):
        toasts: list[tuple[str, int]] = []
        self.app._notify = lambda row, threshold: toasts.append((row.key, threshold))
        return toasts

    def test_notifies_once_at_highest_crossed_threshold(self):
        toasts = self._capture()
        self.app._check_notifications(self._snapshot(82))
        self.assertEqual(toasts, [("five_hour", 80)])

    def test_does_not_repeat_on_subsequent_polls(self):
        toasts = self._capture()
        snapshot = self._snapshot(82)
        self.app._check_notifications(snapshot)
        toasts.clear()
        self.app._check_notifications(snapshot)
        self.assertEqual(toasts, [])

    def test_crossing_second_threshold_notifies_again(self):
        toasts = self._capture()
        self.app._check_notifications(self._snapshot(72))
        self.assertEqual(toasts, [("five_hour", 70)])
        toasts.clear()
        self.app._check_notifications(self._snapshot(81))
        self.assertEqual(toasts, [("five_hour", 80)])

    def test_window_reset_clears_state_and_rearms(self):
        toasts = self._capture()
        self.app._check_notifications(self._snapshot(82))
        toasts.clear()
        self.app._check_notifications(self._snapshot(3))  # window rolled over
        self.assertEqual(self.app.config.get("notified").get("five_hour"), [])
        self.app._check_notifications(self._snapshot(82))
        self.assertEqual(toasts, [("five_hour", 80)])

    def test_below_threshold_is_silent(self):
        toasts = self._capture()
        self.app._check_notifications(self._snapshot(69))
        self.assertEqual(toasts, [])

    # -- menu and layout ------------------------------------------------------

    def test_menu_contains_core_actions(self):
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        self.app._populate_menu(menu)
        labels = [action.text() for action in menu.actions()]
        for expected in ("Refresh now", "Always on top", "Start with Windows", "Quit"):
            self.assertIn(expected, labels)

    def test_height_grows_with_row_count(self):
        from claude_usage_widget.widget import UsageWidget

        widget = UsageWidget(70, 85)
        widget.set_snapshot(self._snapshot(10))
        two_rows = widget.height()
        from claude_usage_widget.usage_api import parse_usage

        widget.set_snapshot(
            parse_usage(
                {
                    "five_hour": {"utilization": 1, "resets_at": iso(hours=1)},
                    "seven_day": {"utilization": 2, "resets_at": iso(days=1)},
                    "seven_day_opus": {"utilization": 3, "resets_at": iso(days=1)},
                }
            )
        )
        self.assertGreater(widget.height(), two_rows)
        widget.close()

    def test_long_error_message_is_not_clipped(self):
        from claude_usage_widget.widget import UsageWidget

        widget = UsageWidget(70, 85)
        short = "Short."
        widget.set_error(short)
        short_height = widget.height()
        widget.set_error(short * 40)
        self.assertGreater(widget.height(), short_height)
        widget.close()

    # -- failure handling -----------------------------------------------------

    def test_throttling_backs_off_exponentially(self):
        self.app.current_interval = self.app.base_interval
        self.app._on_failure("Rate limited.", "throttle")
        first = self.app.current_interval
        self.app._on_failure("Rate limited.", "throttle")
        self.assertEqual(first, self.app.base_interval * 2)
        self.assertEqual(self.app.current_interval, self.app.base_interval * 4)

    def test_backoff_is_capped(self):
        self.app.current_interval = self.app_module.MAX_BACKOFF_SECONDS
        self.app._on_failure("Rate limited.", "throttle")
        self.assertEqual(
            self.app.current_interval, self.app_module.MAX_BACKOFF_SECONDS
        )

    def test_auth_failure_keeps_normal_cadence(self):
        """Signing in fixes it, so do not disappear for half an hour."""
        self.app.current_interval = self.app_module.MAX_BACKOFF_SECONDS
        self.app._on_failure("Sign-in expired.", "auth")
        self.assertEqual(self.app.current_interval, self.app.base_interval)

    def test_success_resets_the_interval(self):
        self.app.current_interval = self.app_module.MAX_BACKOFF_SECONDS
        self.app._on_success(self._snapshot(5))
        self.assertEqual(self.app.current_interval, self.app.base_interval)

    def test_bars_survive_a_failure(self):
        self.app._on_success(self._snapshot(12))
        self.app._on_failure("Rate limited.", "throttle")
        self.assertIsNotNone(self.app.widget.state.snapshot)
        self.assertTrue(self.app.widget.state.status)

    # -- packaging ------------------------------------------------------------

    def test_entry_point_runs_without_a_parent_package(self):
        """PyInstaller executes its entry script as a top-level `__main__`.

        The package's own __main__.py uses relative imports, which cannot
        resolve in that context — main.py exists to import absolutely instead.
        Running it here with a non-"__main__" run_name exercises the imports
        without launching the GUI.
        """
        import runpy

        root = Path(__file__).resolve().parent.parent
        namespace = runpy.run_path(str(root / "main.py"), run_name="frozen_entry")
        self.assertTrue(callable(namespace.get("main")))

    def test_menu_reports_the_build(self):
        """A packaged build is frozen at its commit; pulling source won't change it.

        Showing the build in the menu turns "am I running a stale exe?" into
        something visible rather than something to infer from error wording.
        """
        from PySide6.QtWidgets import QMenu

        from claude_usage_widget import __version__

        menu = QMenu()
        self.app._populate_menu(menu)
        labels = [action.text() for action in menu.actions()]
        self.assertTrue(
            any(__version__ in label for label in labels),
            f"no version entry in {labels}",
        )

    def test_build_info_distinguishes_source_from_packaged(self):
        from claude_usage_widget import build_info

        self.assertIn("source", build_info())

    # -- icon -----------------------------------------------------------------

    def test_write_ico_produces_a_valid_icon_file(self):
        """The build passes this file to PyInstaller, so it must be real.

        A silent failure here previously produced a build that died much later
        with a confusing "icon input file not found".
        """
        import struct

        from claude_usage_widget.icon import write_ico

        path = os.path.join(tempfile.mkdtemp(prefix="claude-icon-"), "app.ico")
        write_ico(path)

        data = Path(path).read_bytes()
        self.assertGreater(len(data), 500)
        self.assertEqual(struct.unpack("<HHH", data[:6]), (0, 1, 1))
        *_, bpp, length, offset = struct.unpack("<BBBBHHII", data[6:22])
        self.assertEqual(bpp, 32)
        self.assertEqual(data[offset : offset + 8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(offset + length, len(data), "declared length must match")

    def test_tray_icon_renders_at_every_size(self):
        from claude_usage_widget.icon import make_pixmap

        for size in (16, 32, 256):
            pixmap = make_pixmap(size)
            self.assertFalse(pixmap.isNull())
            self.assertEqual(pixmap.width(), size)

    def test_position_round_trips_through_config(self):
        from PySide6.QtCore import QPoint

        self.app._save_position(QPoint(321, 123))
        self.assertEqual(self.app.config.get("position"), [321, 123])


if __name__ == "__main__":
    unittest.main()
