import tempfile
import unittest
from pathlib import Path

from askgpt.preflight import (
    format_content_warning,
    format_warning,
    scan_contents,
    scan_tree,
)


class ScanTreeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def touch(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
        return path

    def test_finds_dotenv(self):
        self.touch(".env")
        self.assertEqual([p.name for p in scan_tree(self.root)], [".env"])

    def test_finds_env_variants(self):
        self.touch(".env.production")
        self.assertTrue(scan_tree(self.root))

    def test_finds_nested_pem(self):
        self.touch("deploy/keys/server.pem")
        self.assertEqual([p.name for p in scan_tree(self.root)], ["server.pem"])

    def test_finds_gitignored_files(self):
        # The whole point: ignore status is irrelevant to what Codex can read.
        self.touch(".gitignore").write_text(".env\n")
        self.touch(".env")
        self.assertIn(".env", [p.name for p in scan_tree(self.root)])

    def test_skips_dot_git(self):
        self.touch(".git/config.pem")
        self.assertEqual(scan_tree(self.root), [])

    def test_ignores_ordinary_files(self):
        self.touch("src/main.py")
        self.touch("README.md")
        self.assertEqual(scan_tree(self.root), [])

    def test_results_are_sorted_and_relative(self):
        # Two files in ONE directory cannot test this: readdir may return them
        # already in sorted order, and on APFS it did -- removing hits.sort()
        # left this test green. os.walk is top-down, so a root-level file is
        # always yielded before a nested one; pairing a root "zzz.pem" with a
        # nested "aaa/a.pem" makes walk order and sorted order differ on every
        # filesystem, so sort() is provably load-bearing.
        self.touch("zzz.pem")
        self.touch("aaa/a.pem")
        self.assertEqual(
            [str(p) for p in scan_tree(self.root)], ["aaa/a.pem", "zzz.pem"]
        )

    def test_finds_aws_credentials_directory(self):
        self.touch(".aws/credentials")
        self.assertIn(".aws", [str(p) for p in scan_tree(self.root)])

    def test_respects_result_cap(self):
        for index in range(30):
            self.touch("k" + str(index) + ".pem")
        self.assertEqual(len(scan_tree(self.root, limit=10)), 10)

    def test_warning_names_each_file_and_the_read_write_distinction(self):
        # This string is the user's only signal that read-only does not mean
        # confidential. Untested, it can silently become useless.
        self.touch("deploy/server.pem")
        message = format_warning(scan_tree(self.root))
        self.assertIn("deploy/server.pem", message)
        self.assertIn("read", message.lower())


class ScanContentsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_finds_a_key_inside_an_ordinary_source_file(self):
        # The gap filename matching cannot close: config.py looks innocuous.
        self.write("config.py", "TOKEN = 'sk-abcdefghij0123456789ABCD'\n")
        hits = scan_contents(self.root)
        self.assertEqual([str(p) for p, _ in hits], ["config.py"])

    def test_reports_the_line_and_redacts_the_value(self):
        secret = "sk-abcdefghij0123456789ABCD"
        self.write("a.py", "x = 1\nTOKEN = '" + secret + "'\n")
        (_, finding), = scan_contents(self.root)
        self.assertEqual(finding.line, 2)
        self.assertNotIn(secret, finding.excerpt)

    def test_ignores_ordinary_code(self):
        self.write("main.py", "def add(a, b):\n    return a + b\n")
        self.assertEqual(scan_contents(self.root), [])

    def test_skips_vendor_and_git_directories(self):
        self.write("node_modules/p/i.js", "k='sk-abcdefghij0123456789ABCD'")
        self.write(".git/cfg", "k='sk-abcdefghij0123456789ABCD'")
        self.assertEqual(scan_contents(self.root), [])

    def test_skips_binary_files_without_crashing(self):
        path = self.root / "blob.bin"
        path.write_bytes(b"\x00\x01\x02\xff" * 100)
        self.assertEqual(scan_contents(self.root), [])

    def test_respects_the_result_cap(self):
        for i in range(30):
            self.write("f%d.py" % i, "k='sk-abcdefghij0123456789ABCD'")
        self.assertEqual(len(scan_contents(self.root, limit=5)), 5)

    def test_warning_names_file_and_line_but_not_the_secret(self):
        secret = "sk-abcdefghij0123456789ABCD"
        self.write("config.py", "TOKEN='" + secret + "'\n")
        message = format_content_warning(scan_contents(self.root))
        self.assertIn("config.py", message)
        self.assertNotIn(secret, message)
        self.assertIn("--allow-sensitive-files", message)


if __name__ == "__main__":
    unittest.main()
