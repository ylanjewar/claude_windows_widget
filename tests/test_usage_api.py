"""Parser tests. These are Qt-free so they run anywhere: python -m unittest discover."""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_usage_widget import credentials  # noqa: E402
from claude_usage_widget.usage_api import (  # noqa: E402
    UsageError,
    _prettify,
    format_reset,
    parse_timestamp,
    parse_usage,
)

NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


def iso(**delta) -> str:
    return (NOW + timedelta(**delta)).isoformat().replace("+00:00", "Z")


# Mirrors the panel in Claude Code: three windows plus a credits balance.
TYPICAL = {
    "plan": "Max (20x)",
    "five_hour": {"utilization": 4, "resets_at": iso(hours=4, minutes=44)},
    "seven_day": {"utilization": 20, "resets_at": iso(days=3)},
    "seven_day_opus": {"utilization": 24, "resets_at": iso(days=3)},
    "usage_credits": {"used": 13.88, "limit": 50.0},
}


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "usage_response.json").read_text("utf-8")
)


class RealResponseTests(unittest.TestCase):
    """Against a captured live response — the shape that actually ships."""

    def setUp(self):
        self.rows = parse_usage(FIXTURE).rows

    def test_rows_match_the_usage_panel(self):
        self.assertEqual(
            [row.label for row in self.rows],
            ["5-hour limit", "Weekly · all models", "Weekly · Fable", "Usage credits"],
        )

    def test_percentages(self):
        self.assertEqual([round(row.percent, 2) for row in self.rows], [8, 72, 91, 12])

    def test_scoped_weekly_limit_comes_from_the_limits_array(self):
        """seven_day_opus is null; the Fable cap only exists in `limits`."""
        self.assertIsNone(FIXTURE["seven_day_opus"])
        fable = self.rows[2]
        self.assertEqual(fable.label, "Weekly · Fable")
        self.assertEqual(fable.key, "weekly_scoped:fable")

    def test_credits_are_minor_units(self):
        """amount_minor 1234 with exponent 2 is $12.34, not $1,234."""
        self.assertEqual(self.rows[3].detail, "$12.34 of $100.00")

    def test_placeholder_objects_are_not_rendered(self):
        """nimbus_quill is all-null at 0% — noise, not a limit."""
        labels = [row.label for row in self.rows]
        self.assertNotIn("Nimbus quill", labels)
        for null_key in ("amber_ladder", "cinder_cove", "tangelo", "seven_day_sonnet"):
            self.assertNotIn(_prettify(null_key), labels)

    def test_reset_strings(self):
        for row in self.rows[:3]:
            self.assertTrue(format_reset(row.resets_at))

    def test_credits_row_is_last(self):
        self.assertEqual(self.rows[-1].key, "usage_credits")


class LimitsArrayTests(unittest.TestCase):
    def test_unknown_scoped_kind_still_labels_sensibly(self):
        payload = {
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "percent": 40,
                    "resets_at": iso(days=1),
                    "scope": {"model": {"display_name": "Sonnet"}},
                }
            ]
        }
        row = parse_usage(payload).rows[0]
        self.assertEqual(row.label, "Weekly · Sonnet")
        self.assertEqual(row.key, "weekly_scoped:sonnet")

    def test_two_scoped_limits_get_distinct_keys(self):
        """Distinct keys keep notification bookkeeping from colliding."""
        payload = {
            "limits": [
                {
                    "kind": "weekly_scoped",
                    "percent": 40,
                    "scope": {"model": {"display_name": "Fable"}},
                },
                {
                    "kind": "weekly_scoped",
                    "percent": 90,
                    "scope": {"model": {"display_name": "Sonnet"}},
                },
            ]
        }
        keys = [row.key for row in parse_usage(payload).rows]
        self.assertEqual(len(set(keys)), 2)

    def test_unknown_unscoped_kind_is_prettified(self):
        payload = {"limits": [{"kind": "monthly_all", "percent": 5}]}
        self.assertEqual(parse_usage(payload).rows[0].label, "Monthly all")

    def test_limits_array_wins_over_top_level_keys(self):
        payload = {
            "limits": [{"kind": "session", "percent": 44, "resets_at": iso(hours=1)}],
            "five_hour": {"utilization": 99, "resets_at": iso(hours=1)},
        }
        rows = parse_usage(payload).rows
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].percent, 44)

    def test_falls_back_when_limits_is_empty(self):
        payload = {"limits": [], "five_hour": {"utilization": 9, "resets_at": iso(hours=1)}}
        rows = parse_usage(payload).rows
        self.assertEqual(rows[0].label, "5-hour limit")
        self.assertAlmostEqual(rows[0].percent, 9)


class CreditsTests(unittest.TestCase):
    def test_spend_is_preferred_over_extra_usage(self):
        row = parse_usage(FIXTURE).rows[-1]
        self.assertEqual(row.percent, 12)  # spend.percent, matching the panel

    def test_extra_usage_fallback_also_scales_minor_units(self):
        payload = {
            "five_hour": {"utilization": 5, "resets_at": iso(hours=1)},
            "extra_usage": {
                "is_enabled": True,
                "decimal_places": 2,
                "used_credits": 1234.0,
                "monthly_limit": 10000,
                "utilization": 12.34,
            },
        }
        row = parse_usage(payload).rows[-1]
        self.assertEqual(row.detail, "$12.34 of $100.00")

    def test_disabled_spend_is_skipped(self):
        payload = dict(FIXTURE)
        payload["spend"] = dict(FIXTURE["spend"], enabled=False)
        payload["extra_usage"] = None
        self.assertNotIn("Usage credits", [r.label for r in parse_usage(payload).rows])

    def test_cap_is_used_when_limit_is_absent(self):
        payload = {
            "limits": [{"kind": "session", "percent": 1}],
            "spend": {
                "enabled": True,
                "used": {"amount_minor": 250, "exponent": 2},
                "cap": {"credits": {"amount_minor": 5000, "exponent": 2}},
            },
        }
        self.assertEqual(parse_usage(payload).rows[-1].detail, "$2.50 of $50.00")

    def test_zero_exponent_is_respected(self):
        payload = {
            "limits": [{"kind": "session", "percent": 1}],
            "spend": {
                "enabled": True,
                "used": {"amount_minor": 7, "exponent": 0},
                "limit": {"amount_minor": 50, "exponent": 0},
            },
        }
        self.assertEqual(parse_usage(payload).rows[-1].detail, "$7.00 of $50.00")


class ParseUsageTests(unittest.TestCase):
    def test_typical_payload_yields_panel_rows(self):
        snapshot = parse_usage(TYPICAL)
        self.assertEqual(snapshot.plan, "Max (20x)")
        labels = [row.label for row in snapshot.rows]
        self.assertEqual(
            labels,
            ["5-hour limit", "Weekly · all models", "Weekly · Fable", "Usage credits"],
        )
        self.assertAlmostEqual(snapshot.rows[0].percent, 4)
        self.assertEqual(snapshot.rows[3].detail, "$13.88 of $50.00")

    def test_credits_percent_is_used_over_limit(self):
        row = parse_usage(TYPICAL).rows[3]
        self.assertAlmostEqual(row.percent, 27.76, places=2)

    def test_credits_derived_from_remaining(self):
        payload = dict(TYPICAL, usage_credits={"remaining": 36.12, "limit": 50.0})
        self.assertEqual(parse_usage(payload).rows[3].detail, "$13.88 of $50.00")

    def test_credits_from_cents(self):
        payload = dict(
            TYPICAL, usage_credits={"used": {"cents": 1388}, "limit": {"cents": 5000}}
        )
        self.assertEqual(parse_usage(payload).rows[3].detail, "$13.88 of $50.00")

    def test_camel_case_keys(self):
        payload = {
            "fiveHour": {"utilization": 12, "resetsAt": iso(hours=2)},
            "sevenDay": {"utilization": 55, "resetsAt": iso(days=2)},
        }
        rows = parse_usage(payload).rows
        self.assertEqual(rows[0].label, "5-hour limit")
        self.assertEqual(rows[1].label, "Weekly · all models")

    def test_nested_under_wrapper_key(self):
        payload = {"usage": {"five_hour": {"utilization": 7, "resets_at": iso(hours=1)}}}
        rows = parse_usage(payload).rows
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].percent, 7)

    def test_fraction_scale_is_normalised(self):
        payload = {"five_hour": {"utilization": 0.42, "resets_at": iso(hours=1)}}
        self.assertAlmostEqual(parse_usage(payload).rows[0].percent, 42)

    def test_unknown_window_still_renders(self):
        """A renamed field should degrade to an odd label, not disappear."""
        payload = dict(TYPICAL, thirty_day={"utilization": 61, "resets_at": iso(days=10)})
        rows = parse_usage(payload).rows
        self.assertIn("Thirty day", [row.label for row in rows])

    def test_unrecognisable_payload_raises(self):
        with self.assertRaises(UsageError):
            parse_usage({"hello": "world"})

    def test_percent_is_clamped_for_credits(self):
        payload = dict(TYPICAL, usage_credits={"used": 80.0, "limit": 50.0})
        self.assertAlmostEqual(parse_usage(payload).rows[3].percent, 100.0)


class ResetFormattingTests(unittest.TestCase):
    def test_relative_under_a_day(self):
        self.assertEqual(
            format_reset(iso(hours=4, minutes=44), now=NOW), "Resets in 4 hr 44 min"
        )

    def test_minutes_only(self):
        self.assertEqual(format_reset(iso(minutes=25), now=NOW), "Resets in 25 min")

    def test_weekday_beyond_a_day(self):
        text = format_reset(iso(days=3), now=NOW)
        self.assertTrue(text.startswith("Resets "))
        self.assertRegex(text, r"Resets \w{3} \d{1,2}:\d{2} (AM|PM)")

    def test_past_timestamp(self):
        self.assertEqual(format_reset(iso(hours=-1), now=NOW), "Resets now")

    def test_epoch_milliseconds(self):
        moment = parse_timestamp(str(int(NOW.timestamp() * 1000)))
        self.assertEqual(moment, NOW)

    def test_garbage_timestamp_is_blank(self):
        self.assertEqual(format_reset("not a date", now=NOW), "")


@unittest.skipUnless(hasattr(time, "tzset"), "TZ cannot be changed at runtime here")
class LocalTimeZoneTests(unittest.TestCase):
    """Reset times must render in the machine's local zone, DST included.

    format_reset uses datetime.astimezone() with no argument, which resolves
    against the OS timezone database — so the same UTC instant reads as PDT in
    August and PST in December without any configuration.
    """

    def setUp(self):
        self._previous = os.environ.get("TZ")
        os.environ["TZ"] = "America/Los_Angeles"
        time.tzset()

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self._previous
        time.tzset()

    def test_weekly_reset_renders_in_pacific_daylight_time(self):
        """The fixture's 2026-08-22T00:00Z is Friday 5pm PDT (UTC-7)."""
        reset = FIXTURE["seven_day"]["resets_at"]
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(format_reset(reset, now=now), "Resets Fri 5:00 PM")

    def test_winter_reset_renders_in_pacific_standard_time(self):
        """The same wall clock in December is PST (UTC-8), an hour earlier."""
        now = datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            format_reset("2026-12-05T00:00:00+00:00", now=now), "Resets Fri 4:00 PM"
        )

    def test_relative_form_is_timezone_independent(self):
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            format_reset("2026-08-17T16:44:00+00:00", now=now), "Resets in 4 hr 44 min"
        )

    def test_utc_machine_would_render_differently(self):
        """Guards against silently falling back to UTC."""
        reset = FIXTURE["seven_day"]["resets_at"]
        now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
        pacific = format_reset(reset, now=now)
        os.environ["TZ"] = "UTC"
        time.tzset()
        self.assertNotEqual(pacific, format_reset(reset, now=now))


class CredentialTests(unittest.TestCase):
    """Credentials are read from CLAUDE_CONFIG_DIR when it is set."""

    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp(prefix="claude-creds-")
        self._previous = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.dir

    def tearDown(self):
        if self._previous is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._previous

    def _write(self, payload):
        (Path(self.dir) / ".credentials.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_reads_nested_access_token(self):
        self._write({"claudeAiOauth": {"accessToken": "sk-ant-oat01-abc"}})
        self.assertEqual(credentials.read_access_token(), "sk-ant-oat01-abc")

    def test_reads_snake_case_access_token(self):
        self._write({"access_token": "sk-ant-oat01-xyz"})
        self.assertEqual(credentials.read_access_token(), "sk-ant-oat01-xyz")

    def test_missing_file_raises(self):
        with self.assertRaises(credentials.CredentialError):
            credentials.read_access_token()

    def test_token_without_value_raises(self):
        self._write({"claudeAiOauth": {"refreshToken": "sk-ant-ort01-abc"}})
        with self.assertRaises(credentials.CredentialError):
            credentials.read_access_token()

    def test_future_expiry_is_not_stale(self):
        future = int((time.time() + 3600) * 1000)
        self._write({"claudeAiOauth": {"accessToken": "t", "expiresAt": future}})
        self.assertIsNone(credentials.expired_seconds_ago())

    def test_past_expiry_reports_age(self):
        past = int((time.time() - 7200) * 1000)
        self._write({"claudeAiOauth": {"accessToken": "t", "expiresAt": past}})
        stale = credentials.expired_seconds_ago()
        self.assertIsNotNone(stale)
        self.assertAlmostEqual(stale, 7200, delta=30)

    def test_expiry_in_seconds_is_accepted(self):
        past = int(time.time() - 600)
        self._write({"claudeAiOauth": {"accessToken": "t", "expiresAt": past}})
        stale = credentials.expired_seconds_ago()
        self.assertIsNotNone(stale)
        self.assertAlmostEqual(stale, 600, delta=30)

    def test_absent_expiry_is_unknown(self):
        self._write({"claudeAiOauth": {"accessToken": "t"}})
        self.assertIsNone(credentials.expired_seconds_ago())


class LongLivedTokenTests(unittest.TestCase):
    """CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token` lasts about a year.

    The session token in the credentials file lasts hours and only renews while
    Claude Code runs, so preferring the long-lived one is what stops the widget
    going stale overnight.
    """

    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp(prefix="claude-longlived-")
        self._previous = {
            key: os.environ.get(key)
            for key in ("CLAUDE_CONFIG_DIR", credentials.ENV_TOKEN_VAR)
        }
        os.environ["CLAUDE_CONFIG_DIR"] = self.dir
        os.environ.pop(credentials.ENV_TOKEN_VAR, None)

    def tearDown(self):
        for key, value in self._previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _write_session_token(self, expires_at=None):
        payload = {"claudeAiOauth": {"accessToken": "sk-ant-oat01-session"}}
        if expires_at is not None:
            payload["claudeAiOauth"]["expiresAt"] = expires_at
        (Path(self.dir) / ".credentials.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_environment_token_takes_priority(self):
        self._write_session_token()
        os.environ[credentials.ENV_TOKEN_VAR] = "sk-ant-oat01-longlivedxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        token = credentials.read_token()
        self.assertEqual(token.value, "sk-ant-oat01-longlivedxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.assertTrue(token.is_long_lived)

    def test_falls_back_to_the_credentials_file(self):
        self._write_session_token()
        token = credentials.read_token()
        self.assertEqual(token.value, "sk-ant-oat01-session")
        self.assertFalse(token.is_long_lived)

    def test_blank_environment_variable_is_ignored(self):
        self._write_session_token()
        os.environ[credentials.ENV_TOKEN_VAR] = "   "
        self.assertEqual(credentials.read_token().value, "sk-ant-oat01-session")

    def test_environment_token_is_stripped(self):
        os.environ[credentials.ENV_TOKEN_VAR] = "  sk-ant-oat01-paddedxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
        self.assertEqual(credentials.read_token().value, "sk-ant-oat01-paddedxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")

    def test_environment_token_skips_the_expiry_pre_check(self):
        """A long-lived token has no local expiry, so nothing to pre-check."""
        self._write_session_token(expires_at=int((time.time() - 7200) * 1000))
        self.assertIsNotNone(credentials.expired_seconds_ago())
        os.environ[credentials.ENV_TOKEN_VAR] = "sk-ant-oat01-longlivedxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        self.assertIsNone(credentials.expired_seconds_ago())

    def test_environment_token_works_without_a_credentials_file(self):
        os.environ[credentials.ENV_TOKEN_VAR] = "sk-ant-oat01-longlivedxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        self.assertFalse((Path(self.dir) / ".credentials.json").exists())
        self.assertEqual(credentials.read_token().value, "sk-ant-oat01-longlivedxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")


class TokenValidationTests(unittest.TestCase):
    """A truncated token must be named as such, not left to look like expiry."""

    def setUp(self):
        self._previous = os.environ.get(credentials.ENV_TOKEN_VAR)

    def tearDown(self):
        if self._previous is None:
            os.environ.pop(credentials.ENV_TOKEN_VAR, None)
        else:
            os.environ[credentials.ENV_TOKEN_VAR] = self._previous

    def test_short_token_is_rejected_with_its_length(self):
        os.environ[credentials.ENV_TOKEN_VAR] = "sk-ant-oat01-eO9gPILW"
        with self.assertRaises(credentials.CredentialError) as caught:
            credentials.read_token()
        self.assertIn("truncated", str(caught.exception))
        self.assertIn("21", str(caught.exception))

    def test_full_length_token_is_accepted(self):
        os.environ[credentials.ENV_TOKEN_VAR] = "sk-ant-oat01-" + "x" * 95
        self.assertTrue(credentials.read_token().is_long_lived)

    def test_malformed_token_does_not_masquerade_as_expiry(self):
        """expired_seconds_ago must stay quiet so one error is reported, not two."""
        os.environ[credentials.ENV_TOKEN_VAR] = "sk-ant-oat01-short"
        self.assertIsNone(credentials.expired_seconds_ago())


class TokenExtractionTests(unittest.TestCase):
    """set_token recovers a token from however the terminal mangled the paste."""

    FULL = "sk-ant-oat01-" + "A" * 95

    def test_plain_paste(self):
        from claude_usage_widget.set_token import extract

        self.assertEqual(extract(self.FULL), self.FULL)

    def test_paste_wrapped_across_lines(self):
        """A wrapped terminal line is the likeliest way a token gets mangled."""
        from claude_usage_widget.set_token import extract

        wrapped = "\n".join([self.FULL[:40], self.FULL[40:80], self.FULL[80:]])
        self.assertEqual(extract(wrapped), self.FULL)

    def test_surrounding_text_is_ignored(self):
        from claude_usage_widget.set_token import extract

        self.assertEqual(extract(f"token: {self.FULL} (keep secret)"), self.FULL)

    def test_non_token_input_returns_none(self):
        from claude_usage_widget.set_token import extract

        self.assertIsNone(extract("hello world"))


class CredentialsFileOrderTests(unittest.TestCase):
    """The credentials file also stores MCP plugin OAuth state: dozens of
    entries with accessToken "" and expiresAt 0. Claude Code rewrites the file
    and key order is not stable, so the lookup must not depend on which
    section a depth-first walk reaches first. A live failure looked exactly
    like this: the structure printout showed a 108-char token while read_token
    reported none, because an mcpOAuth placeholder was found first."""

    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp(prefix="claude-order-")
        self._config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        self._env_token = os.environ.get(credentials.ENV_TOKEN_VAR)
        os.environ["CLAUDE_CONFIG_DIR"] = self.dir
        os.environ.pop(credentials.ENV_TOKEN_VAR, None)

    def tearDown(self):
        for key, value in (
            ("CLAUDE_CONFIG_DIR", self._config_dir),
            (credentials.ENV_TOKEN_VAR, self._env_token),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    REAL_TOKEN = "sk-ant-oat01-" + "r" * 95
    REAL_EXPIRY = int((time.time() + 4 * 3600) * 1000)

    def _mcp_entries(self):
        return {
            f"plugin:productivity:tool{i}|{i:016x}": {
                "accessToken": "",
                "expiresAt": 0,
                "serverName": f"plugin:productivity:tool{i}",
            }
            for i in range(25)
        }

    def _write(self, payload):
        (Path(self.dir) / ".credentials.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _claude_section(self):
        return {
            "accessToken": self.REAL_TOKEN,
            "expiresAt": self.REAL_EXPIRY,
            "rateLimitTier": "default_claude_max_20x",
            "subscriptionType": "max",
        }

    def test_mcp_section_first_in_file(self):
        """The order that broke in the wild: placeholders before the token."""
        self._write({"mcpOAuth": self._mcp_entries(), "claudeAiOauth": self._claude_section()})
        self.assertEqual(credentials.read_token().value, self.REAL_TOKEN)
        self.assertEqual(credentials.token_expiry_ms(), self.REAL_EXPIRY)
        self.assertIsNone(credentials.expired_seconds_ago())
        self.assertEqual(credentials.plan_label(), "Max (20x)")

    def test_claude_section_first_in_file(self):
        self._write({"claudeAiOauth": self._claude_section(), "mcpOAuth": self._mcp_entries()})
        self.assertEqual(credentials.read_token().value, self.REAL_TOKEN)
        self.assertEqual(credentials.token_expiry_ms(), self.REAL_EXPIRY)

    def test_placeholder_expiry_does_not_read_as_1970(self):
        """expiresAt 0 in a plugin entry must not mean 'expired 56 years ago'."""
        self._write({"mcpOAuth": self._mcp_entries(), "claudeAiOauth": self._claude_section()})
        self.assertIsNone(credentials.expired_seconds_ago())

    def test_renamed_primary_section_still_found_by_search(self):
        """If claudeAiOauth is ever renamed, the search must skip placeholders
        rather than stop at one."""
        self._write({"mcpOAuth": self._mcp_entries(), "oauthV2": self._claude_section()})
        self.assertEqual(credentials.read_token().value, self.REAL_TOKEN)
        self.assertEqual(credentials.token_expiry_ms(), self.REAL_EXPIRY)

    def test_genuinely_missing_token_still_errors(self):
        self._write({"mcpOAuth": self._mcp_entries()})
        with self.assertRaises(credentials.CredentialError):
            credentials.read_token()


class RegistryFallbackTests(unittest.TestCase):
    """setx persists to HKCU\\Environment but never updates running processes.

    Reading the registry directly is what makes `setx` take effect on the next
    poll rather than requiring the widget to be restarted.
    """

    def setUp(self):
        import tempfile

        self.dir = tempfile.mkdtemp(prefix="claude-registry-")
        self._config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
        self._env_token = os.environ.get(credentials.ENV_TOKEN_VAR)
        self._real_registry = credentials._token_from_registry
        os.environ["CLAUDE_CONFIG_DIR"] = self.dir
        os.environ.pop(credentials.ENV_TOKEN_VAR, None)
        (Path(self.dir) / ".credentials.json").write_text(
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat01-session",
                        "expiresAt": int((time.time() - 7200) * 1000),
                    }
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        credentials._token_from_registry = self._real_registry
        for key, value in (
            ("CLAUDE_CONFIG_DIR", self._config_dir),
            (credentials.ENV_TOKEN_VAR, self._env_token),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_registry_token_is_used_when_the_process_env_is_stale(self):
        credentials._token_from_registry = lambda: "sk-ant-oat01-fromsetxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        token = credentials.read_token()
        self.assertEqual(token.value, "sk-ant-oat01-fromsetxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.assertEqual(token.source, credentials.SOURCE_REGISTRY)
        self.assertTrue(token.is_long_lived)

    def test_process_environment_still_wins(self):
        credentials._token_from_registry = lambda: "sk-ant-oat01-fromsetxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        os.environ[credentials.ENV_TOKEN_VAR] = "sk-ant-oat01-fromenvxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        self.assertEqual(credentials.read_token().value, "sk-ant-oat01-fromenvxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")

    def test_registry_token_skips_the_expiry_pre_check(self):
        """Otherwise a stale session token would still report as expired."""
        self.assertIsNotNone(credentials.expired_seconds_ago())
        credentials._token_from_registry = lambda: "sk-ant-oat01-fromsetxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        self.assertIsNone(credentials.expired_seconds_ago())

    def test_absent_registry_value_falls_through_to_the_file(self):
        credentials._token_from_registry = lambda: None
        token = credentials.read_token()
        self.assertEqual(token.value, "sk-ant-oat01-session")
        self.assertEqual(token.source, credentials.SOURCE_FILE)

    def test_registry_read_is_safe_off_windows(self):
        """The real implementation must never raise on a non-Windows box."""
        self.assertIsNone(self._real_registry() if os.name != "nt" else None)


class PackagingTests(unittest.TestCase):
    """Guards on the build wiring. No Qt needed, so these always run."""

    @property
    def root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    def test_build_script_targets_the_absolute_import_entry_point(self):
        script = (self.root / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("main.py", script)
        self.assertNotIn(
            "claude_usage_widget\\__main__.py",
            script,
            "PyInstaller must not use the package __main__; its relative "
            "imports fail with no parent package.",
        )

    def test_entry_point_uses_an_absolute_import(self):
        source = (self.root / "main.py").read_text(encoding="utf-8")
        self.assertIn("from claude_usage_widget.app import main", source)
        self.assertNotIn("from .", source)

    def test_icon_is_not_a_hard_build_dependency(self):
        """A failed icon must warn, not abort an otherwise good build."""
        script = (self.root / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("building without a custom icon", script)

    def test_native_command_exit_codes_are_checked(self):
        script = (self.root / "build.ps1").read_text(encoding="utf-8")
        self.assertIn("LASTEXITCODE", script)


if __name__ == "__main__":
    unittest.main()
