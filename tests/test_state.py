import stat
import tempfile
import unittest
from pathlib import Path

from askgpt import secfs
from askgpt.state import archive_response, load_thread, save_thread
from permissive import permissive_dir


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_roundtrip(self):
        save_thread(self.dir, "claude-1", "thread-a")
        self.assertEqual(load_thread(self.dir, "claude-1"), "thread-a")

    def test_missing_session_returns_none(self):
        self.assertIsNone(load_thread(self.dir, "absent"))

    def test_sessions_are_independent(self):
        save_thread(self.dir, "claude-1", "thread-a")
        save_thread(self.dir, "claude-2", "thread-b")
        self.assertEqual(load_thread(self.dir, "claude-1"), "thread-a")
        self.assertEqual(load_thread(self.dir, "claude-2"), "thread-b")

    def test_overwrite_replaces_thread(self):
        save_thread(self.dir, "claude-1", "thread-a")
        save_thread(self.dir, "claude-1", "thread-b")
        self.assertEqual(load_thread(self.dir, "claude-1"), "thread-b")

    def test_state_file_is_owner_only(self):
        # Written into a state directory that grants everyone access, so this
        # measures the code rather than the default protection a temp
        # directory happens to carry.
        exposed = permissive_dir(self.dir / "exposed-state")
        path = save_thread(exposed, "claude-1", "thread-a")
        self.assertTrue(
            secfs.is_owner_only(path),
            "thread state is readable by more than its owner",
        )

    def test_an_existing_exposed_directory_is_repaired(self):
        # Upgrade path. A state directory created by an earlier build inherited
        # whatever its parent allowed, and creating it with the right mode only
        # helps directories that do not exist yet. Anyone who ran the tool
        # before this change already has one on disk, so writing into it has to
        # fix it rather than assume it was made correctly.
        threads = permissive_dir(self.dir / "threads")
        self.assertFalse(secfs.is_owner_only(threads), "precondition: starts exposed")

        save_thread(self.dir, "claude-1", "thread-a")

        self.assertTrue(
            secfs.is_owner_only(threads),
            "an already-exposed state directory was left exposed",
        )

    def test_an_exposed_state_root_is_repaired_too(self):
        # Repairing only the directory being written into leaves the root that
        # holds it listable and writable by everyone -- the contents protected,
        # the container not. Found by adversarial review of the first fix.
        root = permissive_dir(self.dir / "exposed-root")
        (root / "threads").mkdir()
        self.assertFalse(secfs.is_owner_only(root), "precondition: root exposed")

        save_thread(root, "claude-1", "thread-a")

        self.assertTrue(
            secfs.is_owner_only(root),
            "the state root was left exposed while its child was repaired",
        )

    def test_migrating_a_legacy_thread_carries_protection_with_it(self):
        # A legacy file predates owner-only storage by definition, and
        # os.replace keeps the SOURCE ACL -- so renaming alone hands the new
        # name an exposed file, and the migration silently launders an
        # unprotected thread into the current format.
        from askgpt.state import _legacy_session_file, _session_file

        root = permissive_dir(self.dir / "legacy-root")
        permissive_dir(root / "threads")
        legacy = _legacy_session_file(root, "claude-1")
        legacy.write_text('{"thread_id": "T-legacy"}', encoding="utf-8")
        self.assertFalse(secfs.is_owner_only(legacy), "precondition: exposed")

        self.assertEqual(load_thread(root, "claude-1"), "T-legacy")

        migrated = _session_file(root, "claude-1")
        self.assertTrue(migrated.is_file(), "the migration did not happen")
        self.assertTrue(
            secfs.is_owner_only(migrated),
            "the migrated thread file kept the legacy file's exposed ACL",
        )

    def test_corrupt_state_is_treated_as_empty(self):
        from askgpt.state import _session_file

        save_thread(self.dir, "claude-1", "thread-a")
        _session_file(self.dir, "claude-1").write_text("{ not json")
        self.assertIsNone(load_thread(self.dir, "claude-1"))

    def test_session_id_cannot_escape_the_state_directory(self):
        # A session id reaches this function from the command line. Without
        # sanitisation it is a path-traversal write primitive.
        path = save_thread(self.dir, "../../evil", "t")
        self.assertEqual(path.parent, self.dir / "threads")
        self.assertNotIn("/", path.name.replace(".json", ""))
        self.assertEqual(load_thread(self.dir, "../../evil"), "t")

    def test_threads_written_before_hashing_still_resolve(self):
        # The aliasing fix renamed thread files. Without a fallback, upgrading
        # silently orphans every existing conversation.
        import json

        legacy = self.dir / "threads"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "manual-test.json").write_text(json.dumps({"thread_id": "OLD-T"}))
        self.assertEqual(load_thread(self.dir, "manual-test"), "OLD-T")

    def test_legacy_thread_is_migrated_to_the_hashed_name(self):
        import json
        from askgpt.state import _session_file

        legacy = self.dir / "threads"
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "manual-test.json").write_text(json.dumps({"thread_id": "OLD-T"}))
        load_thread(self.dir, "manual-test")
        self.assertTrue(_session_file(self.dir, "manual-test").is_file())
        self.assertFalse((legacy / "manual-test.json").exists())

    def test_distinct_ids_that_sanitise_alike_do_not_collide(self):
        # "a/b", "a?b" and "a:b" all sanitise to "a_b"; without the hash they
        # would share one file and resume each other's threads.
        save_thread(self.dir, "a/b", "thread-slash")
        save_thread(self.dir, "a?b", "thread-query")
        save_thread(self.dir, "a:b", "thread-colon")
        self.assertEqual(load_thread(self.dir, "a/b"), "thread-slash")
        self.assertEqual(load_thread(self.dir, "a?b"), "thread-query")
        self.assertEqual(load_thread(self.dir, "a:b"), "thread-colon")

    def test_concurrent_writers_do_not_lose_sessions(self):
        import threading

        def write(index):
            save_thread(self.dir, "session-" + str(index), "thread-" + str(index))

        workers = [threading.Thread(target=write, args=(i,)) for i in range(20)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()

        for index in range(20):
            self.assertEqual(
                load_thread(self.dir, "session-" + str(index)), "thread-" + str(index)
            )


class ArchiveResponseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_writes_the_complete_text(self):
        path, _, _ = archive_response(self.dir, "s1", "t1", "FULL REVIEW BODY")
        self.assertEqual(path.read_text(), "FULL REVIEW BODY")

    def test_reports_length_and_hash_so_truncation_is_detectable(self):
        _, digest, size = archive_response(self.dir, "s1", "t1", "abcdef")
        self.assertEqual(size, 6)
        self.assertEqual(len(digest), 12)

    def test_different_text_gives_a_different_hash(self):
        _, a, _ = archive_response(self.dir, "s1", "t1", "one")
        _, b, _ = archive_response(self.dir, "s1", "t1", "two")
        self.assertNotEqual(a, b)

    def test_archive_file_is_owner_only(self):
        # The archive holds the full review text, so the same reasoning as
        # test_state_file_is_owner_only applies: use an exposed parent.
        exposed = permissive_dir(self.dir / "exposed-archive")
        path, _, _ = archive_response(exposed, "s1", "t1", "x")
        self.assertTrue(
            secfs.is_owner_only(path),
            "the response archive is readable by more than its owner",
        )

    def test_session_id_cannot_escape_the_archive_directory(self):
        path, _, _ = archive_response(self.dir, "../../evil", "t", "x")
        self.assertEqual(path.parent, self.dir / "responses")

    def test_archive_is_pruned(self):
        for i in range(60):
            archive_response(self.dir, "s", "t", "body number " + str(i))
        kept = list((self.dir / "responses").glob("*.md"))
        self.assertLessEqual(len(kept), 50)

    def test_pruning_counts_responses_and_keeps_pairs_intact(self):
        # Payload sidecars share the .md suffix. Counting them as archives
        # halves the retention window and can delete one half of a pair,
        # leaving a response whose payload -- the audit record -- is gone.
        for i in range(60):
            archive_response(self.dir, "s", "t", "body " + str(i),
                             payload="payload " + str(i))
        directory = self.dir / "responses"
        responses = [p for p in directory.glob("*.md")
                     if not p.name.endswith(".payload.md")]
        self.assertEqual(len(responses), 50)
        for response in responses:
            self.assertTrue(
                response.with_suffix(".payload.md").is_file(),
                "response kept without its payload: " + response.name,
            )
        # And no orphans: a pruned response must take its payload with it,
        # or sidecars accumulate without bound while responses are capped.
        sidecars = list(directory.glob("*.payload.md"))
        self.assertEqual(len(sidecars), len(responses))

    def test_same_response_different_payload_does_not_overwrite(self):
        # Folding the payload into the archive identity keeps two runs with
        # identical response text but different payloads as separate records.
        a, _, _ = archive_response(self.dir, "s", "t", "SAME", payload="payload one")
        b, _, _ = archive_response(self.dir, "s", "t", "SAME", payload="payload two")
        self.assertNotEqual(a, b)
        self.assertTrue(a.is_file() and b.is_file())

    def test_payload_snapshot_is_stored_alongside(self):
        # Auditability: once the ledger changes, the snapshot is the only
        # record of which accepted-risks block influenced a given review.
        path, _, _ = archive_response(self.dir, "s", "t", "resp", payload="PAYLOAD X")
        side = path.with_suffix(".payload.md")
        self.assertEqual(side.read_text(), "PAYLOAD X")

    def test_unkeyed_session_still_archives(self):
        path, _, _ = archive_response(self.dir, None, None, "x")
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
