"""Dump the raw usage response, so field-name drift is easy to diagnose.

    python -m claude_usage_widget.probe

Prints the JSON exactly as returned, then how this widget interpreted it. The
access token itself is never printed.
"""

from __future__ import annotations

import json
import sys

from .credentials import CredentialError, credentials_path, read_access_token
from .usage_api import UsageError, fetch_usage, format_reset, parse_usage, user_agent


def main() -> int:
    print(f"credentials: {credentials_path()}")
    try:
        token = read_access_token()
    except CredentialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"token:       ...{token[-6:]} (last 6 chars)")
    print(f"user-agent:  {user_agent()}\n")

    try:
        raw = fetch_usage(token)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print("--- raw response ---")
    print(json.dumps(raw, indent=2, sort_keys=True))

    print("\n--- parsed rows ---")
    try:
        snapshot = parse_usage(raw)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"plan: {snapshot.plan or '(not reported)'}")
    for row in snapshot.rows:
        detail = row.detail or format_reset(row.resets_at)
        print(f"  {row.label:<24} {row.percent:6.2f}%  {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
