"""Read plan usage from Anthropic's OAuth usage endpoint.

The endpoint is undocumented and its field names are not guaranteed to be
stable, so parsing here is deliberately forgiving: known keys are mapped to the
rows shown in Claude Code's /usage panel, and anything else that looks like a
usage window is still rendered rather than dropped. Run

    python -m claude_usage_widget.probe

to dump the raw response if the rows ever stop lining up.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
FALLBACK_CLI_VERSION = "2.0.0"
REQUEST_TIMEOUT = 20


class UsageError(Exception):
    """A fetch failed. `retryable` marks transient failures worth backing off on."""

    def __init__(
        self, message: str, *, retryable: bool = True, status: int = 0, body: str = ""
    ):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        # Raw response text, kept for the probe rather than shown in the widget.
        self.body = body


@dataclass
class UsageRow:
    key: str
    label: str
    percent: float
    detail: str = ""
    resets_at: str | None = None


@dataclass
class UsageSnapshot:
    rows: list[UsageRow] = field(default_factory=list)
    plan: str | None = None
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# User-Agent
#
# The endpoint puts requests without a claude-code User-Agent into a much more
# aggressive rate-limit bucket, so send a real-looking one.
# ---------------------------------------------------------------------------

_cached_user_agent: str | None = None


def cli_candidates() -> list[str]:
    """Places the Claude Code launcher turns up on Windows, PATH first."""
    found: list[str] = []
    for name in ("claude", "claude.cmd", "claude.exe", "claude.ps1"):
        resolved = shutil.which(name)
        if resolved and resolved not in found:
            found.append(resolved)

    home = Path.home()
    extra = [
        home / ".local" / "bin" / "claude.exe",
        home / ".local" / "bin" / "claude",
        home / ".claude" / "local" / "claude.exe",
    ]
    for var, tail in (
        ("APPDATA", ("npm", "claude.cmd")),
        ("LOCALAPPDATA", ("Programs", "claude", "claude.exe")),
        ("ProgramFiles", ("Claude", "claude.exe")),
    ):
        base = os.environ.get(var)
        if base:
            extra.append(Path(base).joinpath(*tail))

    for path in extra:
        text = str(path)
        if text not in found and path.exists():
            found.append(text)
    return found


def _run_version(executable: str) -> str | None:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    # .cmd and .ps1 launchers need a shell; a real executable does not.
    use_shell = os.name == "nt" and executable.lower().endswith((".cmd", ".bat", ".ps1"))
    command = f'"{executable}" --version' if use_shell else [executable, "--version"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=flags,
            shell=use_shell,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+\.\d+\.\d+", (proc.stdout or "") + (proc.stderr or ""))
    return match.group(0) if match else None


def detect_cli_version() -> str | None:
    """Version of the installed Claude Code CLI, or None if it cannot be found."""
    for candidate in cli_candidates():
        version = _run_version(candidate)
        if version:
            return version
    return None


def user_agent(override: str | None = None) -> str:
    global _cached_user_agent
    if override:
        return override
    if _cached_user_agent is None:
        _cached_user_agent = f"claude-code/{detect_cli_version() or FALLBACK_CLI_VERSION}"
    return _cached_user_agent


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_usage(token: str, ua_override: str | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": OAUTH_BETA,
            "User-Agent": user_agent(ua_override),
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:1000]
        except Exception:
            pass
        if exc.code in (401, 403):
            raise UsageError(
                "Token rejected. Run `claude` and sign in again with /login.",
                retryable=False,
                status=exc.code,
                body=body,
            ) from exc
        if exc.code == 429:
            raise UsageError(
                "Rate limited by the usage endpoint.", status=429, body=body
            ) from exc
        raise UsageError(
            f"HTTP {exc.code} from usage endpoint. {body}".strip(),
            status=exc.code,
            body=body,
        ) from exc
    except urllib.error.URLError as exc:
        raise UsageError(f"Network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise UsageError("Usage request timed out.") from exc

    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise UsageError("Usage endpoint returned a non-JSON response.") from exc
    if not isinstance(data, dict):
        raise UsageError("Usage endpoint returned an unexpected shape.")
    return data


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# The authoritative source is the "limits" array, which carries an explicit
# kind and scope per row. These map kinds to the labels the /usage panel uses;
# a scoped kind builds its label from scope.model.display_name instead.
LIMIT_KIND_LABELS = {
    "session": "5-hour limit",
    "five_hour": "5-hour limit",
    "weekly_all": "Weekly · all models",
    "seven_day": "Weekly · all models",
}

# Fallback for responses without a "limits" array. Each entry lists the
# response keys seen in the wild for that row; the first match wins.
WINDOW_SPECS: list[tuple[str, str, tuple[str, ...]]] = [
    ("five_hour", "5-hour limit", ("five_hour", "fiveHour", "5_hour", "session", "current_session")),
    ("seven_day", "Weekly · all models", ("seven_day", "sevenDay", "7_day", "weekly", "week")),
    (
        "seven_day_premium",
        "Weekly · Fable",
        (
            "seven_day_opus",
            "sevenDayOpus",
            "seven_day_fable",
            "sevenDayFable",
            "seven_day_premium",
            "weekly_opus",
            "opus",
        ),
    ),
]

UTILIZATION_KEYS = ("utilization", "used_percent", "usedPercent", "percent", "percentage", "usage")
RESET_KEYS = ("resets_at", "resetsAt", "reset_at", "resetAt", "expires_at", "next_reset")


def _as_percent(value: object) -> float | None:
    """Normalise a utilization value to 0-100, tolerating 0-1 fractions."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number < 0:
        return None
    # A float strictly between 0 and 1 is a fraction; an int 0 or 1 is a percent.
    if isinstance(value, float) and 0 < number < 1:
        number *= 100
    return min(number, 999.0)


def _extract_window(node: object) -> tuple[float, str | None] | None:
    """Pull (percent, resets_at) out of a usage-window object."""
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        percent = _as_percent(node)
        return (percent, None) if percent is not None else None
    if not isinstance(node, dict):
        return None
    for key in UTILIZATION_KEYS:
        if key in node:
            percent = _as_percent(node[key])
            if percent is None:
                continue
            resets = None
            for reset_key in RESET_KEYS:
                candidate = node.get(reset_key)
                if isinstance(candidate, (str, int, float)):
                    resets = candidate
                    break
            return percent, resets
    return None


def _scope_name(scope: object) -> str | None:
    """Name a scoped limit, e.g. the model a weekly cap applies to."""
    if not isinstance(scope, dict):
        return None
    model = scope.get("model")
    if isinstance(model, dict):
        for key in ("display_name", "displayName", "name", "id"):
            value = model.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("surface", "name", "display_name"):
        value = scope.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _rows_from_limits(limits: object) -> list[UsageRow]:
    """Build rows from the structured `limits` array."""
    if not isinstance(limits, list):
        return []
    rows: list[UsageRow] = []
    for item in limits:
        if not isinstance(item, dict):
            continue
        value = item.get("percent")
        if value is None:
            value = item.get("utilization")
        percent = _as_percent(value)
        if percent is None:
            continue

        kind = str(item.get("kind") or item.get("group") or "limit")
        scope_name = _scope_name(item.get("scope"))
        label = LIMIT_KIND_LABELS.get(kind)
        key = kind
        if label is None:
            if scope_name and kind.startswith("weekly"):
                label = f"Weekly · {scope_name}"
            elif scope_name:
                label = f"{_prettify(kind)} · {scope_name}"
            else:
                label = _prettify(kind)
        if scope_name:
            # Keep notification bookkeeping stable when several scoped limits
            # share a kind.
            key = f"{kind}:{scope_name.lower()}"

        resets = item.get("resets_at") or item.get("resetsAt")
        rows.append(
            UsageRow(
                key=key,
                label=label,
                percent=percent,
                resets_at=str(resets) if resets else None,
            )
        )
    return rows


def _find_plan(data: dict[str, Any]) -> str | None:
    for key in ("plan", "plan_name", "planName", "subscription", "subscription_type", "tier"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for inner in ("name", "display_name", "displayName", "type"):
                nested = value.get(inner)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return None


def _money(value: object) -> float | None:
    """Accept dollars as a number/string, or an object carrying cents."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace("$", "").replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    if isinstance(value, dict):
        for key in ("amount", "value", "dollars"):
            if key in value:
                return _money(value[key])
        for key in ("cents", "amount_cents", "amountCents"):
            if key in value:
                cents = _money(value[key])
                return cents / 100 if cents is not None else None
    return None


def _minor_amount(node: object) -> float | None:
    """Decode {'amount_minor': 1388, 'exponent': 2} as 13.88.

    Getting this wrong inflates the figure by 100x, so the exponent is only
    defaulted when the key is genuinely absent.
    """
    if isinstance(node, dict) and "amount_minor" in node:
        amount = node.get("amount_minor")
        if isinstance(amount, bool) or not isinstance(amount, (int, float)):
            return None
        exponent = node.get("exponent")
        if not isinstance(exponent, int) or isinstance(exponent, bool):
            exponent = 2
        return float(amount) / (10**exponent)
    return _money(node)


def _credits_from_spend(data: dict[str, Any]) -> UsageRow | None:
    """The `spend` object, which is what the /usage panel's credits row shows."""
    spend = data.get("spend")
    if not isinstance(spend, dict) or spend.get("enabled") is False:
        return None

    used = _minor_amount(spend.get("used"))
    limit = _minor_amount(spend.get("limit"))
    if limit is None:
        cap = spend.get("cap")
        if isinstance(cap, dict):
            limit = _minor_amount(cap.get("credits")) or _minor_amount(cap.get("money"))
    if used is None or limit is None or limit <= 0:
        return None

    # Prefer the server's own percentage so the widget agrees with the panel.
    percent = _as_percent(spend.get("percent"))
    if percent is None:
        percent = used / limit * 100
    return UsageRow(
        key="usage_credits",
        label="Usage credits",
        percent=min(percent, 100.0),
        detail=f"${used:,.2f} of ${limit:,.2f}",
    )


def _credits_from_extra_usage(data: dict[str, Any]) -> UsageRow | None:
    """Fallback: `extra_usage`, whose amounts are also in minor units."""
    extra = data.get("extra_usage")
    if not isinstance(extra, dict) or extra.get("is_enabled") is False:
        return None
    places = extra.get("decimal_places")
    if not isinstance(places, int) or isinstance(places, bool):
        places = 2
    divisor = 10**places

    used = extra.get("used_credits")
    limit = extra.get("monthly_limit")
    if not isinstance(used, (int, float)) or not isinstance(limit, (int, float)):
        return None
    if isinstance(used, bool) or isinstance(limit, bool) or limit <= 0:
        return None

    used_value = float(used) / divisor
    limit_value = float(limit) / divisor
    percent = _as_percent(extra.get("utilization"))
    if percent is None:
        percent = used_value / limit_value * 100
    return UsageRow(
        key="usage_credits",
        label="Usage credits",
        percent=min(percent, 100.0),
        detail=f"${used_value:,.2f} of ${limit_value:,.2f}",
    )


def _extract_credits(data: dict[str, Any]) -> UsageRow | None:
    """Build the 'Usage credits · $X of $Y' row if the payload carries one."""
    node: Any = None
    for key in ("usage_credits", "usageCredits", "credits", "extra_usage", "extraUsage", "balance"):
        if isinstance(data.get(key), (dict, int, float)):
            node = data[key]
            break
    if node is None:
        return None
    if not isinstance(node, dict):
        return None

    used = None
    for key in ("used", "spent", "consumed", "amount_used", "usedAmount"):
        if key in node:
            used = _money(node[key])
            break
    limit = None
    for key in ("limit", "total", "cap", "monthly_limit", "monthlyLimit", "allowance"):
        if key in node:
            limit = _money(node[key])
            break
    remaining = None
    for key in ("remaining", "available", "balance"):
        if key in node:
            remaining = _money(node[key])
            break

    if used is None and remaining is not None and limit is not None:
        used = limit - remaining
    if limit is None and used is not None and remaining is not None:
        limit = used + remaining
    if used is None or limit is None or limit <= 0:
        return None

    percent = max(0.0, min(used / limit * 100, 100.0))
    return UsageRow(
        key="usage_credits",
        label="Usage credits",
        percent=percent,
        detail=f"${used:,.2f} of ${limit:,.2f}",
    )


def _prettify(key: str) -> str:
    return key.replace("_", " ").replace("-", " ").strip().capitalize()


# Credits live in their own objects and must not be swept up as usage windows.
NON_WINDOW_KEYS = frozenset({"spend", "extra_usage", "extraUsage", "usage_credits", "limits"})


def parse_usage(data: dict[str, Any]) -> UsageSnapshot:
    snapshot = UsageSnapshot(raw=data)
    snapshot.plan = _find_plan(data)

    # Preferred path: the structured array, which names each limit's kind and
    # scope. Weekly per-model caps appear only here — the matching top-level
    # keys (seven_day_opus and friends) are null.
    snapshot.rows.extend(_rows_from_limits(data.get("limits")))

    if not snapshot.rows:
        # Some responses nest everything under a wrapper key.
        scope: dict[str, Any] = data
        for wrapper in ("usage", "rate_limits", "data"):
            inner = data.get(wrapper)
            if isinstance(inner, dict) and any(
                _extract_window(v)
                for v in inner.values()
                if isinstance(v, (dict, int, float))
            ):
                scope = inner
                break

        claimed: set[str] = set()
        for key, label, aliases in WINDOW_SPECS:
            for alias in aliases:
                if alias not in scope:
                    continue
                window = _extract_window(scope[alias])
                if window is None:
                    continue
                percent, resets = window
                claimed.add(alias)
                snapshot.rows.append(
                    UsageRow(
                        key=key,
                        label=label,
                        percent=percent,
                        resets_at=str(resets) if resets is not None else None,
                    )
                )
                break

        # Anything else window-shaped still gets a bar, so a renamed field
        # degrades to a slightly odd label instead of vanishing. Placeholder
        # objects for unreleased features are all-null with 0% and no reset —
        # those are noise, not limits.
        for key, value in scope.items():
            if key in claimed or key in NON_WINDOW_KEYS or not isinstance(value, dict):
                continue
            window = _extract_window(value)
            if window is None:
                continue
            percent, resets = window
            if resets is None and percent == 0:
                continue
            snapshot.rows.append(
                UsageRow(
                    key=key,
                    label=_prettify(key),
                    percent=percent,
                    resets_at=str(resets) if resets is not None else None,
                )
            )

    credits = (
        _credits_from_spend(data)
        or _credits_from_extra_usage(data)
        or _extract_credits(data)
    )
    if credits is not None:
        snapshot.rows.append(credits)

    if not snapshot.rows:
        raise UsageError(
            "Usage response had no recognisable limits. "
            "Run `python -m claude_usage_widget.probe` to inspect it.",
            retryable=False,
        )
    return snapshot


# ---------------------------------------------------------------------------
# Reset-time formatting
# ---------------------------------------------------------------------------


def parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    # Epoch seconds or milliseconds.
    if re.fullmatch(r"\d{9,14}", text):
        number = int(text)
        if number > 10**12:
            number //= 1000
        try:
            return datetime.fromtimestamp(number, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_reset(value: str | None, now: datetime | None = None) -> str:
    """Match the /usage panel: relative under a day, weekday + clock beyond."""
    moment = parse_timestamp(value)
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    delta = (moment - current).total_seconds()
    if delta <= 0:
        return "Resets now"
    if delta < 86400:
        hours, minutes = divmod(int(delta) // 60, 60)
        if hours:
            return f"Resets in {hours} hr {minutes} min"
        return f"Resets in {minutes} min"
    local = moment.astimezone()
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"Resets {local.strftime('%a')} {clock}"


def load_snapshot(token: str, ua_override: str | None = None) -> UsageSnapshot:
    snapshot = parse_usage(fetch_usage(token, ua_override))
    if not snapshot.plan:
        from .credentials import plan_label

        snapshot.plan = plan_label()
    for row in snapshot.rows:
        if not row.detail and row.resets_at:
            row.detail = format_reset(row.resets_at)
    return snapshot


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    from .credentials import read_access_token

    try:
        result = load_snapshot(read_access_token())
    except Exception as error:  # noqa: BLE001
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
    for row in result.rows:
        print(f"{row.label:<24} {row.percent:5.1f}%  {row.detail}")
