"""User settings, persisted to %APPDATA%\\ClaudeUsageWidget\\config.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "ClaudeUsageWidget"
DISPLAY_NAME = "Claude Usage"

DEFAULTS: dict[str, Any] = {
    # How often to poll the usage endpoint, in seconds. The endpoint rate-limits
    # hard below ~60s; 300 keeps a wide margin.
    "poll_seconds": 300,
    # Bar fill colour thresholds, in percent.
    "warn_percent": 70,
    "critical_percent": 85,
    # Toast notification thresholds, in percent.
    "notify_at": [70, 80],
    "notifications_enabled": True,
    # Run `claude update` to let the CLI renew an expired sign-in, rather than
    # asking the user to open Claude Code themselves.
    "auto_refresh_sign_in": True,
    # Heartbeat: also refresh while the token is still valid but close to
    # expiry, so the widget never visibly enters the expired state. Minutes of
    # remaining validity below which to act; 0 disables the heartbeat.
    "heartbeat_margin_minutes": 45,
    "opacity": 0.96,
    # Saved window position as [x, y]; None means "centre-right of the screen".
    "position": None,
    # Overrides the User-Agent sent to the usage endpoint. None = autodetect
    # from the installed Claude Code CLI.
    "user_agent": None,
    # Thresholds already announced, so a restart does not re-toast. Maps a row
    # key to the list of thresholds fired since that window last reset.
    "notified": {},
}


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return Path(base) / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


class Config:
    """A thin dict wrapper that writes through to disk on change."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(config_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in DEFAULTS:
                    self._data[key] = value

    def save(self) -> None:
        try:
            config_dir().mkdir(parents=True, exist_ok=True)
            tmp = config_path().with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8"
            )
            os.replace(tmp, config_path())
        except OSError:
            # A read-only profile should not take the widget down.
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        if self._data.get(key) != value:
            self._data[key] = value
            self.save()

    @property
    def notify_thresholds(self) -> list[int]:
        raw = self.get("notify_at") or []
        out = sorted({int(v) for v in raw if isinstance(v, (int, float))})
        return out
