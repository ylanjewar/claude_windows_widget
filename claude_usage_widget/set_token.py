"""Store a long-lived token safely, without a fragile copy-paste.

    python -m claude_usage_widget.set_token

Pasting a ~100-character token into a terminal is easy to get wrong: a wrapped
line copies as a fragment, and `setx` accepts the fragment without complaint.
This validates the value before storing it, writes it to ``HKCU\\Environment``,
and broadcasts the change so new processes see it.
"""

from __future__ import annotations

import re
import sys

from .credentials import ENV_TOKEN_VAR, MIN_PLAUSIBLE_TOKEN_LENGTH

TOKEN_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")


def extract(text: str) -> str | None:
    """Pull a token out of pasted text, tolerating surrounding noise."""
    # Terminals wrap long values; joining the lines recovers the whole token.
    joined = "".join(text.split())
    match = TOKEN_PATTERN.search(joined)
    return match.group(0) if match else None


def store(token: str) -> tuple[bool, str]:
    """Write to the user environment and tell running processes about it."""
    if sys.platform != "win32":
        return False, "Only supported on Windows."
    import ctypes
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, ENV_TOKEN_VAR, 0, winreg.REG_SZ, token)
    except OSError as exc:
        return False, f"Could not write to the registry: {exc}"

    # WM_SETTINGCHANGE tells Explorer and other listeners to re-read the
    # environment. Already-running processes still keep their old copy, which
    # is why the widget reads the registry directly.
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None
        )
    except Exception:  # noqa: BLE001 - cosmetic; the value is already stored
        pass
    return True, ""


def main() -> int:
    print("Run `claude setup-token` first, then paste the token it prints.")
    print("Paste it here and press Enter (input is not echoed back):\n")
    try:
        pasted = input("> ")
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        return 1

    token = extract(pasted)
    if token is None:
        print(
            f"\nThat does not contain a token. Expected something starting with "
            f"'sk-ant-' and around 100 characters.",
            file=sys.stderr,
        )
        return 1

    if len(token) < MIN_PLAUSIBLE_TOKEN_LENGTH:
        print(
            f"\nThat token is only {len(token)} characters, which means it was "
            "truncated — a wrapped terminal line copies as a fragment. Select "
            "the whole value and try again.",
            file=sys.stderr,
        )
        return 1

    ok, error = store(token)
    if not ok:
        print(f"\n{error}", file=sys.stderr)
        return 1

    print(f"\nStored {ENV_TOKEN_VAR} ({len(token)} characters, ...{token[-6:]}).")
    print("The widget picks it up on its next poll — no restart needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
