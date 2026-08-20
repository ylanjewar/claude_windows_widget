"""Diagnose the usage endpoint end to end.

    python -m claude_usage_widget.probe

Prints the credentials layout, the exact request, and the raw response, then
how this widget interpreted it. Secrets are redacted: tokens are shown only by
their last six characters, and any string longer than 24 characters in the
credentials file is reduced to its type and length. The output is safe to paste
into an issue.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from . import build_info
from .credentials import (
    ENV_TOKEN_VAR,
    CredentialError,
    credentials_path,
    expired_seconds_ago,
    read_token,
    token_expiry_ms,
)
from .usage_api import (
    OAUTH_BETA,
    USAGE_URL,
    UsageError,
    detect_cli_version,
    fetch_usage,
    format_reset,
    parse_usage,
    user_agent,
)

SAFE_STRING_LENGTH = 24


def redact(node: object) -> object:
    """Keep the shape, drop the secrets."""
    if isinstance(node, dict):
        return {key: redact(value) for key, value in node.items()}
    if isinstance(node, list):
        return [redact(item) for item in node]
    if isinstance(node, str) and len(node) > SAFE_STRING_LENGTH:
        return f"<str len={len(node)}>"
    return node


def describe_expiry() -> str:
    expiry = token_expiry_ms()
    if expiry is None:
        return "not recorded in the credentials file"
    moment = datetime.fromtimestamp(expiry / 1000, tz=timezone.utc)
    stale = expired_seconds_ago()
    when = moment.astimezone().strftime("%Y-%m-%d %H:%M %Z")
    if stale is None:
        remaining = (moment - datetime.now(timezone.utc)).total_seconds()
        return f"{when} (valid for another {remaining / 3600:.1f} hr)"
    return f"{when} (EXPIRED {stale / 3600:.1f} hr ago — run `claude` to refresh)"


def main() -> int:
    from .log import log_path

    print(f"=== widget ===\nversion:   {build_info()}")
    print(f"log:       {log_path()}\n")
    print("=== credentials ===")
    path = credentials_path()
    print(f"path:      {path}")
    print(f"exists:    {path.exists()}")
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            print("structure:")
            print(json.dumps(redact(raw), indent=2, sort_keys=True))
        except (OSError, ValueError) as exc:
            print(f"structure: unreadable ({exc})")

    try:
        token = read_token()
    except CredentialError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    print(f"source:    {token.source}")
    print(f"token:     ...{token.value[-6:]} (last 6 chars, length {len(token.value)})")
    if token.is_long_lived:
        print(f"expiry:    not tracked locally; {ENV_TOKEN_VAR} lasts about a year")
    else:
        print(f"expiry:    {describe_expiry()}")
        print(
            f"tip:       set {ENV_TOKEN_VAR} from `claude setup-token` so the "
            "widget stops expiring every few hours"
        )

    print("\n=== request ===")
    detected = detect_cli_version()
    print(f"CLI found: {detected or 'no — falling back to a default version'}")
    agent = user_agent()
    print(f"GET {USAGE_URL}")
    print("  Authorization: Bearer <redacted>")
    print(f"  anthropic-beta: {OAUTH_BETA}")
    print(f"  User-Agent: {agent}")

    print("\n=== response ===")
    try:
        raw = fetch_usage(token.value)
    except UsageError as exc:
        print(f"status:  HTTP {exc.status or '(no response)'}")
        print(f"message: {exc}")
        if exc.body:
            print("body:")
            print(exc.body)
        return 1
    print("status:  HTTP 200")
    print(json.dumps(raw, indent=2, sort_keys=True))

    print("\n=== parsed ===")
    try:
        snapshot = parse_usage(raw)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"plan: {snapshot.plan or '(not reported)'}")
    for row in snapshot.rows:
        detail = row.detail or format_reset(row.resets_at)
        print(f"  {row.label:<24} {row.percent:6.2f}%  {detail}")

    print("\n=== recent widget log ===")
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()
        for line in lines[-25:]:
            print(f"  {line}")
    except OSError:
        print("  (no log yet — the widget writes it as it runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
