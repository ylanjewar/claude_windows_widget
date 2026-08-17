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


if __name__ == "__main__":
    unittest.main()
