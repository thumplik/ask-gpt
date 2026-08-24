import stat
import tempfile
import unittest
from pathlib import Path

from askgpt.state import load_thread, save_thread


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

    def test_state_file_is_0600(self):
        path = save_thread(self.dir, "claude-1", "thread-a")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_corrupt_state_is_treated_as_empty(self):
        save_thread(self.dir, "claude-1", "thread-a")
        (self.dir / "threads" / "claude-1.json").write_text("{ not json")
        self.assertIsNone(load_thread(self.dir, "claude-1"))

    def test_session_id_cannot_escape_the_state_directory(self):
        # A session id reaches this function from the command line. Without
        # sanitisation it is a path-traversal write primitive.
        path = save_thread(self.dir, "../../evil", "t")
        self.assertEqual(path.parent, self.dir / "threads")
        self.assertNotIn("/", path.name.replace(".json", ""))
        self.assertEqual(load_thread(self.dir, "../../evil"), "t")

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


if __name__ == "__main__":
    unittest.main()
