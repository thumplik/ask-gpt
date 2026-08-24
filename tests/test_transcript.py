import json
import tempfile
import unittest
from pathlib import Path

from askgpt.transcript import load_jsonl, pack, resolve_session
from askgpt.errors import TranscriptNotFound

FIXTURE = Path(__file__).parent / "fixtures" / "session.jsonl"


class PackTest(unittest.TestCase):
    def setUp(self):
        self.records = load_jsonl(FIXTURE)
        self.out = pack(self.records)

    def test_keeps_human_dialogue_verbatim(self):
        self.assertIn("Add retry logic to the uploader", self.out)

    def test_keeps_assistant_text(self):
        self.assertIn("The signature is wrong. Fixing.", self.out)

    def test_drops_successful_tool_results(self):
        self.assertNotIn("def upload(): pass", self.out)

    def test_keeps_failed_tool_results(self):
        self.assertIn("TypeError: upload() takes 0 positional arguments", self.out)

    def test_collapses_tool_use_to_a_marker(self):
        self.assertIn("[tool: Read]", self.out)

    def test_drops_non_dialogue_records(self):
        self.assertNotIn("noise that must be dropped", self.out)

    def test_strips_system_reminders(self):
        self.assertNotIn("ignore me", self.out)
        self.assertIn("Looks good", self.out)

    def test_caps_failed_output_length(self):
        records = [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "is_error": True, "content": "E" * 9000}
                    ],
                },
            }
        ]
        out = pack(records, fail_item_cap=100)
        self.assertLess(out.count("E"), 200)
        self.assertIn("truncated", out)

    def test_budget_drops_oldest_first_and_marks_it(self):
        # The OLDEST record must be the large one. With a small oldest record the
        # total falls under any sane budget, nothing is dropped, and the test
        # passes vacuously against a broken implementation.
        records = [
            {"type": "user", "message": {"role": "user", "content": "OLDEST " + "O" * 3000}},
            {"type": "user", "message": {"role": "user", "content": "middle"}},
            {"type": "user", "message": {"role": "user", "content": "NEWEST"}},
        ]
        out = pack(records, budget=1000)
        self.assertIn("middle", out)
        self.assertIn("NEWEST", out)
        self.assertNotIn("OLDEST", out)
        self.assertIn("earlier turns omitted", out)

    def test_drop_last_turns_removes_the_invocation(self):
        records = [
            {"type": "user", "message": {"role": "user", "content": "keep me"}},
            {"type": "user", "message": {"role": "user", "content": "the invocation"}},
        ]
        out = pack(records, drop_last_turns=1)
        self.assertIn("keep me", out)
        self.assertNotIn("the invocation", out)

    def test_drop_last_turns_ignores_trailing_non_dialogue_records(self):
        # Slicing raw records would drop the summary and leak the invocation.
        records = [
            {"type": "user", "message": {"role": "user", "content": "keep me"}},
            {"type": "user", "message": {"role": "user", "content": "the invocation"}},
            {"type": "summary", "summary": "trailing noise"},
        ]
        out = pack(records, drop_last_turns=1)
        self.assertIn("keep me", out)
        self.assertNotIn("the invocation", out)

    def test_drop_last_turns_on_short_history_is_safe(self):
        records = [{"type": "user", "message": {"role": "user", "content": "only"}}]
        self.assertEqual(pack(records, drop_last_turns=3), "")


class ResolveSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_explicit_session_id_wins(self):
        (self.dir / "aaa.jsonl").write_text("{}\n")
        (self.dir / "bbb.jsonl").write_text("{}\n")
        self.assertEqual(
            resolve_session(self.dir, "aaa"), self.dir / "aaa.jsonl"
        )

    def test_falls_back_to_newest_by_mtime(self):
        old = self.dir / "old.jsonl"
        new = self.dir / "new.jsonl"
        old.write_text("{}\n")
        new.write_text("{}\n")
        import os

        os.utime(old, (1, 1))
        os.utime(new, (2, 2))
        self.assertEqual(resolve_session(self.dir, None), new)

    def test_raises_when_directory_has_no_transcripts(self):
        with self.assertRaises(TranscriptNotFound):
            resolve_session(self.dir, None)

    def test_raises_when_named_session_is_absent(self):
        with self.assertRaises(TranscriptNotFound):
            resolve_session(self.dir, "missing")


if __name__ == "__main__":
    unittest.main()
