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

    # -- scope fallback -------------------------------------------------------

    def test_scope_rejection_falls_back_to_the_session_token(self):
        """`claude setup-token` omits user:profile, which this endpoint needs.

        The long-lived token is valid, just not for this call, so the widget
        must retry with the session token rather than report a sign-in problem
        the user cannot fix.
        """
        from claude_usage_widget import app as app_module
        from claude_usage_widget.credentials import SOURCE_ENVIRONMENT, Token
        from claude_usage_widget.usage_api import UsageError

        poller = app_module.Poller(self.app.config)
        long_lived = Token("sk-ant-oat01-" + "x" * 95, SOURCE_ENVIRONMENT)
        attempts: list[str] = []

        def fake_load(token_value, ua=None):
            attempts.append(token_value)
            if token_value == long_lived.value:
                raise UsageError(
                    "Token lacks a scope this endpoint requires.",
                    retryable=False,
                    status=403,
                    body='{"error":{"message":"does not meet scope requirement '
                    "user:profile\"}}",
                )
            return self._snapshot(5)

        original_load = app_module.load_snapshot
        original_read = app_module.read_token
        app_module.load_snapshot = fake_load
        app_module.read_token = lambda allow_long_lived=True: (
            long_lived if allow_long_lived else Token("sk-session", "credentials file")
        )
        try:
            import claude_usage_widget.credentials as creds

            original_session = creds.session_token
            creds.session_token = lambda: Token("sk-session", "credentials file")
            try:
                results = []
                poller.succeeded.connect(lambda snap: results.append(snap))
                poller._run()
            finally:
                creds.session_token = original_session
        finally:
            app_module.load_snapshot = original_load
            app_module.read_token = original_read

        self.assertEqual(len(attempts), 2, "should retry with the session token")
        self.assertEqual(attempts[1], "sk-session")
        self.assertTrue(poller._long_lived_refused, "should not retry it every poll")
        self.assertEqual(len(results), 1, "the retry's snapshot should be delivered")

    # -- automatic sign-in refresh --------------------------------------------

    def test_expired_session_triggers_a_cli_refresh(self):
        """Claude Code owns the refresh token, so let it do the renewing."""
        from claude_usage_widget import app as app_module, startup
        from claude_usage_widget.credentials import SOURCE_FILE, Token

        poller = app_module.Poller(self.app.config)
        calls = {"refresh": 0}
        expired = [True]

        def fake_refresh(timeout=120):
            calls["refresh"] += 1
            expired[0] = False  # the CLI renewed it
            return True

        original = (
            startup.refresh_sign_in,
            app_module.expired_seconds_ago,
            app_module.read_token,
            app_module.load_snapshot,
        )
        startup.refresh_sign_in = fake_refresh
        app_module.expired_seconds_ago = lambda: 3600.0 if expired[0] else None
        app_module.read_token = lambda allow_long_lived=True: Token("tok", SOURCE_FILE)
        app_module.load_snapshot = lambda value, ua=None: self._snapshot(9)
        try:
            results = []
            poller.succeeded.connect(lambda snap: results.append(snap))
            poller._run()
        finally:
            (
                startup.refresh_sign_in,
                app_module.expired_seconds_ago,
                app_module.read_token,
                app_module.load_snapshot,
            ) = original

        self.assertEqual(calls["refresh"], 1)
        self.assertEqual(len(results), 1, "should recover without user action")

    def test_cli_refresh_is_rate_limited(self):
        """One attempt per 10 minutes, so a broken CLI is not invoked each poll."""
        from claude_usage_widget import app as app_module, startup

        poller = app_module.Poller(self.app.config)
        calls = {"n": 0}

        def counting(timeout=120):
            calls["n"] += 1
            return False

        original = startup.refresh_sign_in
        startup.refresh_sign_in = counting
        try:
            self.assertTrue(poller._try_cli_refresh() is False)
            poller._try_cli_refresh()
            poller._try_cli_refresh()
        finally:
            startup.refresh_sign_in = original
        self.assertEqual(calls["n"], 1, "later attempts should be suppressed")

    def test_auto_refresh_can_be_disabled(self):
        from claude_usage_widget import app as app_module, startup

        self.app.config.set("auto_refresh_sign_in", False)
        poller = app_module.Poller(self.app.config)
        called = {"n": 0}
        original = startup.refresh_sign_in
        startup.refresh_sign_in = lambda timeout=120: called.__setitem__("n", 1) or True
        try:
            self.assertFalse(poller._try_cli_refresh())
        finally:
            startup.refresh_sign_in = original
            self.app.config.set("auto_refresh_sign_in", True)
        self.assertEqual(called["n"], 0)

    def test_session_fallback_also_refreshes_an_expired_token(self):
        """Reaching the fallback with an expired session token must still recover.

        A scope rejection routes here, and this path previously skipped the
        refresh entirely — so setting CLAUDE_CODE_OAUTH_TOKEN turned a
        recoverable expiry into a dead end.
        """
        from claude_usage_widget import app as app_module, startup
        from claude_usage_widget.credentials import SOURCE_FILE, Token
        import claude_usage_widget.credentials as creds

        poller = app_module.Poller(self.app.config)
        expired = [True]
        refreshed = {"n": 0}

        def fake_refresh(timeout=120):
            refreshed["n"] += 1
            expired[0] = False
            return True

        original = (
            startup.refresh_sign_in,
            app_module.expired_seconds_ago,
            app_module.load_snapshot,
            creds.session_token,
        )
        startup.refresh_sign_in = fake_refresh
        app_module.expired_seconds_ago = lambda: 3600.0 if expired[0] else None
        app_module.load_snapshot = lambda value, ua=None: self._snapshot(7)
        creds.session_token = lambda: Token("sk-session", SOURCE_FILE)
        try:
            results = []
            poller.succeeded.connect(lambda snap: results.append(snap))
            poller._run_with_session_token()
        finally:
            (
                startup.refresh_sign_in,
                app_module.expired_seconds_ago,
                app_module.load_snapshot,
                creds.session_token,
            ) = original

        self.assertEqual(refreshed["n"], 1, "fallback must attempt a refresh")
        self.assertEqual(len(results), 1)

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
