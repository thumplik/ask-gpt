import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.claude = self.root / "claude"
        self.claude.mkdir()

        stub = self.root / "codex"
        stub.write_text('#!/bin/sh\necho "Logged in using ChatGPT"\n')
        stub.chmod(0o755)
        self.env = dict(
            os.environ,
            CLAUDE_CONFIG_DIR=str(self.claude),
            CODEX_BIN=str(stub),
            ASKGPT_BIN_DIR=str(self.root / "bin"),
        )

    def install(self):
        return subprocess.run(
            ["bash", str(INSTALL)], capture_output=True, text=True, env=self.env
        )

    def test_creates_every_symlink(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        for rel in ("ask-gpt", "commands/gptreview.md", "commands/askgpt.md",
                    "commands/gptfollow.md", "commands/gptusage.md",
                    "skills/second-opinion"):
            self.assertTrue((self.claude / rel).is_symlink(), rel)

    def test_puts_the_cli_on_path(self):
        # `askgpt usage` should work from a terminal, not only via a full path.
        self.install()
        link = self.root / "bin" / "askgpt"
        self.assertTrue(link.is_symlink())
        self.assertEqual(
            os.path.realpath(link), str((REPO / "bin" / "askgpt").resolve())
        )

    def test_cli_is_executable_afterwards(self):
        self.install()
        self.assertTrue(os.access(REPO / "bin" / "askgpt", os.X_OK))

    def test_rerunning_replaces_existing_symlinks(self):
        self.assertEqual(self.install().returncode, 0)
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)

    def test_refuses_to_clobber_a_real_directory(self):
        victim = self.claude / "skills" / "second-opinion"
        victim.mkdir(parents=True)
        (victim / "precious.md").write_text("do not delete me")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing", result.stderr.lower())
        self.assertTrue((victim / "precious.md").is_file())

    def test_symlinks_point_at_the_right_targets(self):
        # Existence is not correctness: swapping the two command links passes
        # a .is_symlink() check while giving you the wrong prompt entirely.
        self.install()
        for name in ("gptreview.md", "askgpt.md", "gptfollow.md", "gptusage.md"):
            self.assertEqual(
                os.path.realpath(self.claude / "commands" / name),
                str((REPO / "commands" / name).resolve()),
                name,
            )

    def test_skill_link_resolves_to_a_directory_holding_skill_md(self):
        self.install()
        self.assertTrue(
            (self.claude / "skills" / "second-opinion" / "SKILL.md").is_file()
        )

    def test_reports_the_codex_binary_and_auth(self):
        result = self.install()
        self.assertIn("codex:", result.stdout)
        self.assertIn("auth:", result.stdout)


if __name__ == "__main__":
    unittest.main()
