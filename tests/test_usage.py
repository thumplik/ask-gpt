import json
import tempfile
import unittest
from pathlib import Path

from askgpt.usage import (
    format_footer,
    format_usage,
    read_latest,
    sessions_dir,
    used_percent,
)


def record(stamp, percent, plan="plus", window=10080, resets=1788136533):
    return json.dumps(
        {
            "timestamp": stamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {
                        "used_percent": percent,
                        "window_minutes": window,
                        "resets_at": resets,
                    },
                    "plan_type": plan,
                    "credits": {"has_credits": False, "unlimited": False},
                },
            },
        }
    )


class SessionsDirTest(unittest.TestCase):
    def test_honours_codex_home(self):
        self.assertEqual(
            sessions_dir({"CODEX_HOME": "/custom"}), Path("/custom/sessions")
        )

    def test_defaults_under_home(self):
        self.assertEqual(
            sessions_dir({"HOME": "/h"}), Path("/h/.codex/sessions")
        )


class ReadLatestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, *lines):
        path = self.root / "2026" / "08" / "24" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_returns_none_without_any_sessions(self):
        self.assertIsNone(read_latest(self.root / "absent"))

    def test_reads_the_record(self):
        self.write("rollout-a.jsonl", record("2026-08-24T01:00:00Z", 7.0))
        stamp, limits = read_latest(self.root)
        self.assertEqual(stamp, "2026-08-24T01:00:00Z")
        self.assertEqual(limits["primary"]["used_percent"], 7.0)

    def test_last_record_in_a_file_wins(self):
        # A run appends repeatedly; the final line is the current figure.
        self.write(
            "rollout-a.jsonl",
            record("2026-08-24T01:00:00Z", 3.0),
            record("2026-08-24T02:00:00Z", 9.0),
        )
        self.assertEqual(used_percent(read_latest(self.root)), 9.0)

    def test_ignores_unrelated_and_malformed_lines(self):
        self.write(
            "rollout-a.jsonl",
            "not json at all",
            json.dumps({"type": "event_msg", "payload": {"type": "turn.started"}}),
            record("2026-08-24T01:00:00Z", 4.0),
        )
        self.assertEqual(used_percent(read_latest(self.root)), 4.0)


class FormatTest(unittest.TestCase):
    def setUp(self):
        self.rec = json.loads(record("2026-08-24T01:00:00Z", 12.0))
        self.rec = ("2026-08-24T01:00:00Z", self.rec["payload"]["rate_limits"])

    def test_reports_percent_plan_and_window(self):
        out = format_usage(self.rec)
        self.assertIn("12%", out)
        self.assertIn("plus", out)
        self.assertIn("7-day", out)

    def test_states_that_the_reading_can_be_stale(self):
        # Without this the number reads as live, which it is not.
        self.assertIn("fresh", format_usage(self.rec).lower())

    def test_handles_no_data_without_crashing(self):
        self.assertIn("No Codex usage data", format_usage(None))
        self.assertIsNone(format_footer(None))

    def test_footer_is_one_line(self):
        self.assertEqual(len(format_footer(self.rec).splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
