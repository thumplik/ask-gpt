import tempfile
import unittest
from pathlib import Path

from askgpt.ledger import (
    accept,
    format_accepted_block,
    load_accepted,
    project_slug,
    unaccept,
)


class ProjectSlugTest(unittest.TestCase):
    def test_stable_for_a_path(self):
        self.assertEqual(project_slug("/a/b/repo"), project_slug("/a/b/repo"))

    def test_differs_between_paths(self):
        self.assertNotEqual(project_slug("/a/repo"), project_slug("/b/repo"))

    def test_contains_no_path_separators(self):
        self.assertNotIn("/", project_slug("/a/b/repo"))


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.repo = "/fake/repo"

    def test_accept_then_load_roundtrip(self):
        accept(self.dir, self.repo, "R1", "uses eval on trusted input only", "a.py:1 eval")
        entries = load_accepted(self.dir, self.repo)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "R1")
        self.assertIn("eval", entries[0]["reason"])

    def test_empty_ledger_loads_as_empty(self):
        self.assertEqual(load_accepted(self.dir, self.repo), [])

    def test_projects_are_isolated(self):
        accept(self.dir, "/repo/a", "R1", "reason a", "a.py:1 thing")
        self.assertEqual(load_accepted(self.dir, "/repo/b"), [])

    def test_reaccepting_an_id_replaces_the_reason(self):
        accept(self.dir, self.repo, "R1", "old reason", "a.py:1 same finding")
        accept(self.dir, self.repo, "R1", "new reason", "a.py:1 same finding")
        entries = load_accepted(self.dir, self.repo)
        self.assertEqual(len(entries), 1)
        self.assertIn("new", entries[0]["reason"])

    def test_unaccept_removes_an_entry(self):
        accept(self.dir, self.repo, "R1", "x", "a.py:1 first")
        accept(self.dir, self.repo, "R2", "y", "b.py:2 second")
        self.assertTrue(unaccept(self.dir, self.repo, "R1"))
        ids = [e["id"] for e in load_accepted(self.dir, self.repo)]
        self.assertEqual(ids, ["R2"])

    def test_unaccept_missing_id_returns_false(self):
        self.assertFalse(unaccept(self.dir, self.repo, "nope"))

    def test_same_ordinal_different_finding_does_not_overwrite(self):
        # The Blocker: F-numbers are per-review ordinals, so re-accepting a
        # later F2 must not delete an unrelated finding accepted as F2 before.
        accept(self.dir, self.repo, "F2", "reason one", "a.py:1 sql injection")
        accept(self.dir, self.repo, "F2", "reason two", "z.py:9 authz bypass")
        descriptions = [e["description"] for e in load_accepted(self.dir, self.repo)]
        self.assertEqual(len(descriptions), 2)

    def test_same_finding_reaccepted_replaces_in_place(self):
        accept(self.dir, self.repo, "F2", "old", "a.py:1 sql injection")
        accept(self.dir, self.repo, "F7", "new", "a.py:1 SQL   Injection")
        entries = load_accepted(self.dir, self.repo)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["reason"], "new")

    def test_description_is_required(self):
        with self.assertRaises(ValueError):
            accept(self.dir, self.repo, "F1", "reason", "")

    def test_unaccept_is_ambiguous_when_an_ordinal_matches_several(self):
        from askgpt.ledger import Ambiguous

        accept(self.dir, self.repo, "F2", "r1", "a.py:1 first thing")
        accept(self.dir, self.repo, "F2", "r2", "b.py:2 other thing")
        with self.assertRaises(Ambiguous):
            unaccept(self.dir, self.repo, "F2")

    def test_unaccept_by_description_substring(self):
        accept(self.dir, self.repo, "F1", "r", "a.py:1 race on cache")
        self.assertTrue(unaccept(self.dir, self.repo, "race on cache"))
        self.assertEqual(load_accepted(self.dir, self.repo), [])

    def test_corrupt_ledger_loads_as_empty(self):
        accept(self.dir, self.repo, "R1", "x", "a.py:1 thing")
        files = list(self.dir.rglob("ledger.json"))
        files[0].write_text("{ not json")
        self.assertEqual(load_accepted(self.dir, self.repo), [])

    def test_entries_record_when_they_were_accepted(self):
        accept(self.dir, self.repo, "R1", "x", "a.py:1 thing")
        self.assertIn("accepted_at", load_accepted(self.dir, self.repo)[0])


class FormatBlockTest(unittest.TestCase):
    def test_empty_ledger_produces_no_block(self):
        self.assertEqual(format_accepted_block([]), "")

    def test_block_carries_id_and_reason_as_data(self):
        entries = [{"id": "R1", "reason": "trusted input only", "accepted_at": "2026-08-24"}]
        block = format_accepted_block(entries)
        self.assertIn("<ACCEPTED-RISKS>", block)
        self.assertIn("</ACCEPTED-RISKS>", block)
        self.assertIn("R1", block)
        self.assertIn("trusted input only", block)

    def test_block_frames_reacceptance_not_suppression(self):
        # The reviewer may still re-report if the risk materially changed;
        # the block must say so rather than reading as a gag order.
        block = format_accepted_block([{"id": "R1", "reason": "x", "accepted_at": "d"}])
        self.assertIn("materially changed", block)


if __name__ == "__main__":
    unittest.main()
