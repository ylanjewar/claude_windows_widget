"""Application wiring: polling, tray icon, menu and threshold notifications."""

from __future__ import annotations

import sys
import threading

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import startup
from . import build_info
from .config import DISPLAY_NAME, Config
from .credentials import (
    ENV_TOKEN_VAR,
    CredentialError,
    expired_seconds_ago,
    read_token,
)
from .icon import make_icon
from .usage_api import UsageError, UsageSnapshot, load_snapshot

MAX_BACKOFF_SECONDS = 1800
# Hysteresis so a value hovering on a threshold cannot toast repeatedly.
NOTIFY_CLEAR_MARGIN = 2


class Poller(QObject):
    """Runs the blocking fetch off the UI thread; signals are auto-queued back."""


    succeeded = Signal(object)
    # (message, kind) where kind is one of auth / throttle / network / fatal.
    failed = Signal(str, str)
    # The long-lived token was refused over scopes; the session token is in use.
    scope_rejected = Signal()

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config
        self._busy = False
        self._lock = threading.Lock()
        # Set once the endpoint refuses the long-lived token over scopes, so we
        # stop spending a request on it every poll.
        self._long_lived_refused = False

    def start_fetch(self) -> bool:
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(target=self._run, daemon=True, name="usage-fetch").start()
        return True

    def _run(self) -> None:
        token = None
        try:
            token = read_token(allow_long_lived=not self._long_lived_refused)
            stale_for = expired_seconds_ago() if not token.is_long_lived else None
            if stale_for is not None:
                hours = stale_for / 3600
                ago = f"{hours:.0f} hr" if hours >= 1 else f"{stale_for / 60:.0f} min"
                # Naming what was checked turns "why is it still expired?"
                # into a fact the user can act on without running the probe.
                raise UsageError(
                    f"Sign-in expired {ago} ago, and no {ENV_TOKEN_VAR} found "
                    "in the environment or registry. Right-click → Open Claude Code.",
                    retryable=False,
                    status=401,
                )
            snapshot = load_snapshot(token.value, self._config.get("user_agent"))
        except CredentialError as exc:
            # Recoverable by signing in, so treat it like an auth failure.
            self.failed.emit(str(exc), "auth")
        except UsageError as exc:
            if exc.status in (401, 403):
                kind = "auth"
                scope_problem = "scope" in (exc.body or "").lower()
                if token is not None and token.is_long_lived and scope_problem:
                    # `claude setup-token` issues a token without user:profile,
                    # which this endpoint requires. The token is valid, just not
                    # for this call — fall back to the session token instead of
                    # reporting a sign-in problem the user cannot fix.
                    self._long_lived_refused = True
                    self.scope_rejected.emit()
                    self._run_with_session_token()
                    return
                if token is not None and token.is_long_lived:
                    self.failed.emit(
                        f"{ENV_TOKEN_VAR} was rejected. Regenerate it with "
                        "`claude setup-token`.",
                        kind,
                    )
                    return
            elif exc.status == 429:
                kind = "throttle"
            elif exc.retryable:
                kind = "network"
            else:
                kind = "fatal"
            self.failed.emit(str(exc), kind)
        except Exception as exc:  # noqa: BLE001 - never let the poller die silently
            self.failed.emit(f"Unexpected error: {exc}", "network")
        else:
            self.succeeded.emit(snapshot)
        finally:
            with self._lock:
                self._busy = False

    def _run_with_session_token(self) -> None:
        """Retry immediately using the credentials file token."""
        try:
            from .credentials import session_token

            snapshot = load_snapshot(
                session_token().value, self._config.get("user_agent")
            )
        except CredentialError as exc:
            self.failed.emit(str(exc), "auth")
        except UsageError as exc:
            kind = "auth" if exc.status in (401, 403) else "network"
            self.failed.emit(str(exc), kind)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc}", "network")
        else:
            self.succeeded.emit(snapshot)


class WidgetApp(QObject):
    def __init__(self, argv: list[str]) -> None:
        super().__init__()
        self.qt = QApplication(argv)
        self.qt.setApplicationName(DISPLAY_NAME)
        self.qt.setQuitOnLastWindowClosed(False)

        self.config = Config()
        self.icon = make_icon()
        self.qt.setWindowIcon(self.icon)

        from .widget import UsageWidget  # imported late so QApplication exists first

        self.widget = UsageWidget(
            int(self.config.get("warn_percent")),
            int(self.config.get("critical_percent")),
        )
        self.widget.setWindowOpacity(float(self.config.get("opacity")))
        self.widget.restore_position(self.config.get("position"))
        self.widget.refresh_requested.connect(self.refresh_now)
        self.widget.menu_requested.connect(self._show_menu)
        self.widget.position_changed.connect(self._save_position)

        self.poller = Poller(self.config)
        self.poller.succeeded.connect(self._on_success)
        self.poller.failed.connect(self._on_failure)
        self.poller.scope_rejected.connect(self._on_scope_rejected)

        self.base_interval = max(60, int(self.config.get("poll_seconds")))
        self.current_interval = self.base_interval

        self.poll_timer = QTimer(self)
        self.poll_timer.setSingleShot(True)
        self.poll_timer.timeout.connect(self.refresh_now)

        # Refreshes the "resets in …" strings between polls.
        self.tick_timer = QTimer(self)
        self.tick_timer.timeout.connect(self._retick)
        self.tick_timer.start(30_000)

        self.context_menu = QMenu()
        self.tray_menu = QMenu()
        self.tray_menu.aboutToShow.connect(lambda: self._populate_menu(self.tray_menu))

        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setToolTip(DISPLAY_NAME)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self.last_snapshot: UsageSnapshot | None = None
        # True while the failure is an expired or rejected sign-in, which the
        # user resolves by running Claude Code rather than by waiting.
        self.needs_signin = False

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> int:
        self.widget.show()
        self.widget.raise_()
        self.refresh_now()
        return self.qt.exec()

    def refresh_now(self) -> None:
        self.poll_timer.stop()
        if self.poller.start_fetch():
            if self.last_snapshot is None:
                self.widget.set_status("Loading…")

    def _schedule(self, seconds: int) -> None:
        self.current_interval = seconds
        self.poll_timer.start(seconds * 1000)

    # -- poll results --------------------------------------------------------

    def _on_success(self, snapshot: UsageSnapshot) -> None:
        self.needs_signin = False
        self.last_snapshot = snapshot
        self.widget.set_snapshot(snapshot)
        self.widget.set_status("")
        self._update_tooltip(snapshot)
        self._check_notifications(snapshot)
        self._schedule(self.base_interval)

    def _on_failure(self, message: str, kind: str) -> None:
        self.needs_signin = kind == "auth"
        if kind in ("throttle", "network"):
            # The usage endpoint rate-limits hard, so back off rather than hammer it.
            self._schedule(min(self.current_interval * 2, MAX_BACKOFF_SECONDS))
        elif kind == "auth":
            # Resolved by the user running Claude Code, so keep checking at the
            # normal cadence instead of backing off into a long sleep.
            self._schedule(self.base_interval)
        else:
            self._schedule(MAX_BACKOFF_SECONDS)

        minutes = max(1, self.current_interval // 60)
        suffix = "" if kind == "fatal" else f" Retrying in {minutes} min."
        self.widget.set_error(f"{message}{suffix}")
        self.tray.setToolTip(f"{DISPLAY_NAME} — {message}")

    def _on_scope_rejected(self) -> None:
        """The long-lived token lacks a scope, so the session token is in use."""
        self.tray.showMessage(
            DISPLAY_NAME,
            f"{ENV_TOKEN_VAR} lacks the user:profile scope this endpoint needs. "
            "Using the Claude Code session token instead.",
            QSystemTrayIcon.Information,
            10_000,
        )

    def _retick(self) -> None:
        """Recompute reset countdowns without re-fetching."""
        if self.last_snapshot is None:
            return
        from .usage_api import format_reset

        changed = False
        for row in self.last_snapshot.rows:
            if row.resets_at:
                fresh = format_reset(row.resets_at)
                if fresh != row.detail:
                    row.detail = fresh
                    changed = True
        if changed:
            self.widget.update()

    def _update_tooltip(self, snapshot: UsageSnapshot) -> None:
        lines = [f"{row.label}: {row.percent:.0f}%" for row in snapshot.rows]
        self.tray.setToolTip("\n".join([DISPLAY_NAME, *lines]))

    # -- notifications -------------------------------------------------------

    def _check_notifications(self, snapshot: UsageSnapshot) -> None:
        thresholds = self.config.notify_thresholds
        if not thresholds:
            return
        state = dict(self.config.get("notified") or {})
        enabled = bool(self.config.get("notifications_enabled"))
        dirty = False

        for row in snapshot.rows:
            previously = {int(v) for v in state.get(row.key, [])}
            # Drop thresholds the value has fallen back below — that is a window reset.
            active = {t for t in previously if row.percent >= t - NOTIFY_CLEAR_MARGIN}
            crossed = [t for t in thresholds if row.percent >= t and t not in active]
            if crossed:
                highest = max(crossed)
                active.update(crossed)
                if enabled:
                    self._notify(row, highest)
            if active != previously:
                state[row.key] = sorted(active)
                dirty = True

        if dirty:
            self.config.set("notified", state)

    def _notify(self, row, threshold: int) -> None:
        if not QSystemTrayIcon.supportsMessages():
            return
        level = (
            QSystemTrayIcon.Critical
            if row.percent >= float(self.config.get("critical_percent"))
            else QSystemTrayIcon.Warning
        )
        detail = f" · {row.detail}" if row.detail else ""
        self.tray.showMessage(
            f"Claude usage past {threshold}%",
            f"{row.label} at {row.percent:.0f}%{detail}",
            level,
            10_000,
        )

    # -- menu ----------------------------------------------------------------

    def _populate_menu(self, menu: QMenu) -> QMenu:
        menu.clear()

        refresh = QAction("Refresh now", menu)
        refresh.triggered.connect(self.refresh_now)
        menu.addAction(refresh)

        # Surfaced only when it is the actual fix, to keep the menu short.
        if self.needs_signin:
            signin = QAction("Open Claude Code to refresh sign-in", menu)
            signin.triggered.connect(self._open_claude_cli)
            menu.addAction(signin)

        menu.addSeparator()

        on_top = QAction("Always on top", menu)
        on_top.setCheckable(True)
        on_top.setChecked(bool(self.widget.windowFlags() & Qt.WindowStaysOnTopHint))
        on_top.toggled.connect(self._set_always_on_top)
        menu.addAction(on_top)

        autostart = QAction("Start with Windows", menu)
        autostart.setCheckable(True)
        autostart.setChecked(startup.is_enabled())
        autostart.toggled.connect(self._set_autostart)
        menu.addAction(autostart)

        desktop = QAction("Create desktop shortcut", menu)
        desktop.triggered.connect(self._create_desktop_shortcut)
        menu.addAction(desktop)

        notify = QAction("Threshold notifications", menu)
        notify.setCheckable(True)
        notify.setChecked(bool(self.config.get("notifications_enabled")))
        notify.toggled.connect(lambda on: self.config.set("notifications_enabled", on))
        menu.addAction(notify)

        opacity_menu = menu.addMenu("Opacity")
        group = QActionGroup(opacity_menu)
        group.setExclusive(True)
        current = float(self.config.get("opacity"))
        for label, value in (("100%", 1.0), ("95%", 0.96), ("80%", 0.8), ("65%", 0.65)):
            action = QAction(label, opacity_menu)
            action.setCheckable(True)
            action.setChecked(abs(current - value) < 0.02)
            action.triggered.connect(lambda _checked, v=value: self._set_opacity(v))
            group.addAction(action)
            opacity_menu.addAction(action)

        menu.addSeparator()
        visibility = QAction("Hide widget" if self.widget.isVisible() else "Show widget", menu)
        visibility.triggered.connect(self._toggle_visibility)
        menu.addAction(visibility)

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.qt.quit)
        menu.addAction(quit_action)

        menu.addSeparator()
        version = QAction(f"Version {build_info()}", menu)
        version.setEnabled(False)
        menu.addAction(version)
        return menu

    def _show_menu(self, position: QPoint) -> None:
        self._populate_menu(self.context_menu)
        self.context_menu.exec(position)

    def _set_always_on_top(self, enabled: bool) -> None:
        self.widget.setWindowFlag(Qt.WindowStaysOnTopHint, enabled)
        self.widget.show()  # re-applying window flags requires a re-show

    def _set_autostart(self, enabled: bool) -> None:
        ok, error = startup.enable() if enabled else startup.disable()
        if not ok:
            self.tray.showMessage(
                "Could not change autostart", error, QSystemTrayIcon.Warning, 8000
            )

    def _open_claude_cli(self) -> None:
        ok, error = startup.open_claude_cli()
        if not ok:
            self.tray.showMessage(
                "Could not open Claude Code", error, QSystemTrayIcon.Warning, 8000
            )
            return
        # Give the CLI a moment to start and refresh the token before retrying.
        QTimer.singleShot(15_000, self.refresh_now)

    def _create_desktop_shortcut(self) -> None:
        ok, error = startup.create_desktop_shortcut()
        if ok:
            self.tray.showMessage(
                DISPLAY_NAME,
                "Shortcut created on your Desktop. It launches without a console window.",
                QSystemTrayIcon.Information,
                6000,
            )
        else:
            self.tray.showMessage(
                "Could not create the shortcut", error, QSystemTrayIcon.Warning, 8000
            )

    def _set_opacity(self, value: float) -> None:
        self.widget.setWindowOpacity(value)
        self.config.set("opacity", value)

    def _toggle_visibility(self) -> None:
        if self.widget.isVisible():
            self.widget.hide()
        else:
            self.widget.show()
            self.widget.raise_()

    def _on_tray_activated(self, reason) -> None:
        # Right-click is handled by the tray's own context menu.
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._toggle_visibility()

    def _save_position(self, point: QPoint) -> None:
        self.config.set("position", [point.x(), point.y()])


def main(argv: list[str] | None = None) -> int:
    return WidgetApp(list(argv if argv is not None else sys.argv)).run()
