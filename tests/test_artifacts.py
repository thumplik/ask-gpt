import tempfile
import unittest
from pathlib import Path

from askgpt import secfs
from askgpt.artifacts import Artifacts
from permissive import permissive_dir


class ArtifactsTest(unittest.TestCase):
    def setUp(self):
        # The permission tests below build artifacts inside a directory that
        # grants everyone access, so the property is checked where it could
        # actually be violated.
        #
        # Honest limitation: these two do not discriminate. `Artifacts` goes
        # through `tempfile.mkdtemp`, which CPython already makes owner-only on
        # both platforms, so they keep passing even with our own restriction
        # removed -- verified by mutation. They assert a property that holds
        # rather than proving this code delivers it. The tests that do pin our
        # own behaviour are in test_state.py, where the directories are ours.
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.open_parent = permissive_dir(Path(self.tmp.name) / "exposed")

    def test_directory_is_owner_only(self):
        with Artifacts(parent=str(self.open_parent)) as art:
            self.assertTrue(
                secfs.is_owner_only(art.dir),
                "the artifacts directory is readable by more than its owner",
            )

    def test_written_file_is_owner_only(self):
        with Artifacts(parent=str(self.open_parent)) as art:
            path = art.write("payload.md", "hello")
            self.assertTrue(
                secfs.is_owner_only(path),
                "the payload is readable by more than its owner",
            )

    def test_the_permission_check_can_actually_fail(self):
        # Guards the guard. If is_owner_only ever returns True unconditionally
        # -- a plausible way for it to rot on a platform change -- both tests
        # above would keep passing while protecting nothing.
        stray = self.open_parent / "unprotected.md"
        stray.write_text("secret")
        self.assertFalse(
            secfs.is_owner_only(stray),
            "is_owner_only called an unprotected file owner-only",
        )

    def test_roundtrip_content(self):
        with Artifacts() as art:
            path = art.write("payload.md", "hello")
            self.assertEqual(path.read_text(), "hello")

    def test_cleans_up_by_default(self):
        with Artifacts() as art:
            directory = Path(art.dir)
        self.assertFalse(directory.exists())

    def test_path_returns_a_location_inside_the_directory(self):
        with Artifacts() as art:
            self.assertEqual(art.path("out.md").parent, Path(art.dir))

    def test_cleans_up_even_when_the_block_raises(self):
        directory = None
        with self.assertRaises(RuntimeError):
            with Artifacts() as art:
                directory = Path(art.dir)
                raise RuntimeError("boom")
        # Payloads carry conversation content; an exception must not leave
        # them behind on disk.
        self.assertFalse(directory.exists())

    def test_keep_preserves_directory(self):
        with Artifacts(keep=True) as art:
            directory = Path(art.dir)
        self.assertTrue(directory.exists())
        import shutil

        shutil.rmtree(directory)


if __name__ == "__main__":
    unittest.main()
