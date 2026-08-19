"""Find the OAuth access token to authenticate usage requests with.

Three sources, in priority order:

1. ``CLAUDE_CODE_OAUTH_TOKEN`` in this process's environment — a long-lived
   token from ``claude setup-token``, valid for about a year. Anthropic provides
   it for headless and CI use, which is effectively what this widget is.
2. The same variable read from ``HKCU\\Environment``, where ``setx`` persists
   it. Running processes never receive the update, so without this a freshly set
   token would appear not to work until the widget was restarted.
3. ``.credentials.json`` — the session token Claude Code writes when you log in.
   It lasts roughly eight hours and is only renewed while the CLI is running, so
   the widget goes stale overnight if nothing else refreshes it.

This module only ever *reads*. It never refreshes a token and never writes to
the credentials file: refresh tokens rotate on use, so consuming one here would
invalidate Claude Code's own stored copy and force the user to log in again.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ENV_TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"

SOURCE_ENVIRONMENT = "environment"
SOURCE_REGISTRY = "user environment (registry)"
SOURCE_FILE = "credentials file"

LONG_LIVED_SOURCES = (SOURCE_ENVIRONMENT, SOURCE_REGISTRY)

# Real tokens run to roughly 100 characters. Anything much shorter has been
# truncated in transit — a partial copy out of a wrapped terminal line, say —
# and sending it produces a 401 that looks like an expiry problem instead of
# the configuration problem it is.
MIN_PLAUSIBLE_TOKEN_LENGTH = 40


@dataclass(frozen=True)
class Token:
    value: str
    source: str

    @property
    def is_long_lived(self) -> bool:
        return self.source in LONG_LIVED_SOURCES


class CredentialError(Exception):
    """Raised when no usable access token can be read."""


def claude_config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude"


def credentials_path() -> Path:
    return claude_config_dir() / ".credentials.json"


def _find_key(node: object, target: str) -> object | None:
    """Depth-first search for a key, so a reshuffled file still works."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == target:
                return value
        for value in node.values():
            found = _find_key(value, target)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_key(item, target)
            if found is not None:
                return found
    return None


def _token_from_registry() -> str | None:
    """Read the persisted user environment variable straight from the registry.

    ``setx`` writes to ``HKCU\\Environment`` and broadcasts a change, but no
    already-running process ever picks it up — not the console it was typed in,
    and not this widget if it started first. Reading the registry means setx
    takes effect on the next poll instead of requiring a restart, and a widget
    launched at login sees a token set afterwards.
    """
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:  # pragma: no cover - Windows only
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value, _ = winreg.QueryValueEx(key, ENV_TOKEN_VAR)
    except OSError:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _validate_long_lived(value: str, source: str) -> Token:
    if len(value) < MIN_PLAUSIBLE_TOKEN_LENGTH:
        raise CredentialError(
            f"{ENV_TOKEN_VAR} looks truncated — {len(value)} characters, "
            "expected about 100. Re-run `claude setup-token` and store the "
            "whole value: python -m claude_usage_widget.set_token"
        )
    return Token(value, source)


def long_lived_token() -> Token | None:
    """The setup-token, from this process's environment or the registry.

    A malformed value raises rather than falling through to the credentials
    file: the user set the variable deliberately, so silently ignoring it would
    hide the mistake behind an unrelated expiry message.
    """
    from_env = os.environ.get(ENV_TOKEN_VAR, "").strip()
    if from_env:
        return _validate_long_lived(from_env, SOURCE_ENVIRONMENT)
    persisted = _token_from_registry()
    if persisted:
        return _validate_long_lived(persisted, SOURCE_REGISTRY)
    return None


def session_token() -> Token:
    """The Claude Code session token, ignoring any long-lived one."""
    return Token(_read_token_from_file(), SOURCE_FILE)


def read_token(allow_long_lived: bool = True) -> Token:
    """Return the token to use, preferring the long-lived one.

    `allow_long_lived=False` skips it, for when the endpoint has already
    refused it over scopes.
    """
    if allow_long_lived:
        found = long_lived_token()
        if found is not None:
            return found
    return session_token()


def read_access_token() -> str:
    """Return the current OAuth access token, or raise CredentialError."""
    return read_token().value


def _read_token_from_file() -> str:
    path = credentials_path()
    if not path.exists():
        if sys.platform == "darwin":
            raise CredentialError(
                "On macOS the token is in the Keychain, not a file. "
                "This widget targets Windows."
            )
        raise CredentialError(
            f"No credentials at {path}. Run `claude` and sign in with /login, "
            f"or set {ENV_TOKEN_VAR} from `claude setup-token`."
        )

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CredentialError(f"Cannot read {path.name}: {exc}") from exc
    except ValueError as exc:
        raise CredentialError(f"{path.name} is not valid JSON: {exc}") from exc

    token = _find_key(data, "accessToken") or _find_key(data, "access_token")
    if not isinstance(token, str) or not token.strip():
        raise CredentialError(
            "No accessToken in the credentials file. Re-run /login in Claude Code."
        )
    return token.strip()


def token_expiry_ms() -> int | None:
    """Best-effort read of the token's expiry, as epoch milliseconds."""
    try:
        data = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key in ("expiresAt", "expires_at"):
        value = _find_key(data, key)
        if isinstance(value, (int, float)):
            number = int(value)
            # Some writers use seconds rather than milliseconds.
            return number if number > 10**12 else number * 1000
    return None


def plan_label() -> str | None:
    """Human-readable plan name, e.g. "Max (5x)".

    The usage response does not name the plan, but the credentials file records
    the rate-limit tier it was issued for.
    """
    try:
        data = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    tier = _find_key(data, "rateLimitTier")
    if isinstance(tier, str) and tier.strip():
        text = re.sub(r"^(default_)?claude_", "", tier.strip().lower())
        multiplier = re.fullmatch(r"([a-z]+)_(\d+x)", text)
        if multiplier:
            return f"{multiplier.group(1).capitalize()} ({multiplier.group(2)})"
        return text.replace("_", " ").title()

    subscription = _find_key(data, "subscriptionType")
    if isinstance(subscription, str) and subscription.strip():
        return subscription.strip().capitalize()
    return None


def expired_seconds_ago() -> float | None:
    """Seconds since the token expired, or None if it is valid or unknown.

    Claude Code refreshes the token whenever you use it, so an expired token
    means the CLI simply has not run in a while. Checking first avoids spending
    a request — and rate-limit budget — on a call that is certain to 401.
    """
    try:
        if long_lived_token() is not None:
            # A setup-token carries no local expiry, and is good for about a
            # year. Nothing to pre-check; a rejection surfaces as a 401 instead.
            return None
    except CredentialError:
        # Malformed token: read_token() reports it, so do not also claim the
        # session token expired.
        return None
    expiry = token_expiry_ms()
    if expiry is None:
        return None
    delta = time.time() - expiry / 1000
    return delta if delta > 0 else None
