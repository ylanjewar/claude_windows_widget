"""Locate and read the OAuth access token Claude Code stores on disk.

On Windows and Linux the token lives in a plaintext JSON file; macOS uses the
Keychain instead, which this module reports as unsupported rather than guessing.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


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


def read_access_token() -> str:
    """Return the current OAuth access token, or raise CredentialError."""
    path = credentials_path()
    if not path.exists():
        if sys.platform == "darwin":
            raise CredentialError(
                "On macOS the token is in the Keychain, not a file. "
                "This widget targets Windows."
            )
        raise CredentialError(
            f"No credentials at {path}. Run `claude` and sign in with /login."
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
            return int(value)
    return None
