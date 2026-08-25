import json
import tempfile
import unittest
from pathlib import Path

import sys

from askgpt.transcript import load_jsonl, pack, project_dir_for, resolve_session
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

    def test_oversized_recent_turn_does_not_discard_smaller_older_ones(self):
        # Turn sizes vary, so one huge recent turn does not mean older turns
        # cannot fit. `break` here returned nothing but the omission marker.
        records = [
            {"type": "user", "message": {"role": "user", "content": "SMALL OLD"}},
            {"type": "user", "message": {"role": "user", "content": "H" * 5000}},
        ]
        out = pack(records, budget=200)
        self.assertIn("SMALL OLD", out)
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

    def test_fail_budget_caps_failed_output_across_turns(self):
        # Distinct from fail_item_cap: each item fits individually, but together
        # they exceed the cross-turn budget. Without this test the entire
        # fail_budget branch can be deleted with the suite still green.
        def failed(marker):
            return {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "is_error": True, "content": marker * 400}
                    ],
                },
            }

        out = pack([failed("A"), failed("B"), failed("C")], fail_budget=900,
                   fail_item_cap=1000)
        self.assertIn("C" * 50, out)   # newest, 400 <= 900
        self.assertIn("B" * 50, out)   # cumulative 800 <= 900
        self.assertNotIn("A" * 50, out)  # cumulative 1200 > 900, skipped

    def test_null_message_is_skipped_not_fatal(self):
        out = pack([
            {"type": "user", "message": None},
            {"type": "user", "message": {"role": "user", "content": "survivor"}},
        ])
        self.assertIn("survivor", out)


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

    def test_rejects_a_session_id_with_separators(self):
        (self.dir / "real.jsonl").write_text("{}\n")
        with self.assertRaises(TranscriptNotFound):
            resolve_session(self.dir, "../../etc/passwd")

    def test_rejects_a_traversal_that_would_escape(self):
        with self.assertRaises(TranscriptNotFound):
            resolve_session(self.dir, "..")

    def test_raises_when_named_session_is_absent(self):
        with self.assertRaises(TranscriptNotFound):
            resolve_session(self.dir, "missing")


class ProjectDirForTest(unittest.TestCase):
    r"""Coverage for the transcript directory name.

    This function had none, and both CLI test helpers reimplemented it rather
    than calling it -- with the same flaw, so they agreed with each other and
    the suite stayed green while `/askgpt` could not find a transcript on
    Windows at all.
    """

    def test_the_name_is_a_single_flat_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            name = project_dir_for(tmp, Path(tmp) / "config").name
            for separator in ("/", "\\", ":"):
                self.assertNotIn(
                    separator,
                    name,
                    "a separator left in the name makes this a nested path, so "
                    "the directory Claude actually wrote is never found",
                )

    def test_it_sits_directly_under_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config"
            self.assertEqual(project_dir_for(tmp, config).parent, config / "projects")

    @unittest.skipUnless(sys.platform == "win32", "Windows drive-letter form")
    def test_windows_drive_and_backslashes_become_dashes(self):
        # The convention Claude Code actually uses, read off a real
        # ~/.claude/projects: C:\Users\thump\ask-gpt -> C--Users-thump-ask-gpt.
        with tempfile.TemporaryDirectory() as tmp:
            resolved = str(Path(tmp).resolve())
            expected = resolved.replace("\\", "-").replace(":", "-")
            self.assertEqual(project_dir_for(tmp, Path("cfg")).name, expected)

    @unittest.skipIf(sys.platform == "win32", "POSIX form")
    def test_posix_slashes_become_dashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            resolved = str(Path(tmp).resolve())
            self.assertEqual(
                project_dir_for(tmp, Path("cfg")).name, resolved.replace("/", "-")
            )


if __name__ == "__main__":
    unittest.main()
