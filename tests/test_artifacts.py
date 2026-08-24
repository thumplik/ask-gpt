import stat
import unittest
from pathlib import Path

from askgpt.artifacts import Artifacts


class ArtifactsTest(unittest.TestCase):
    def test_directory_is_0700(self):
        with Artifacts() as art:
            mode = stat.S_IMODE(Path(art.dir).stat().st_mode)
            self.assertEqual(mode, 0o700)

    def test_written_file_is_0600(self):
        with Artifacts() as art:
            path = art.write("payload.md", "hello")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

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
