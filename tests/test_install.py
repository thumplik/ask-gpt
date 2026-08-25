import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from capabilities import SYMLINK_REASON, can_symlink
from stubs import write_stub

REPO = Path(__file__).resolve().parent.parent
WINDOWS = sys.platform == "win32"

# One installer per platform, deliberately not one script pretending to be
# portable: install.sh is bash and symlinks, install.ps1 is PowerShell and the
# same symlinks plus a .cmd shim, because a shebang is not executable here.
if WINDOWS:
    INSTALL_ARGV = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(REPO / "install.ps1"),
    ]
    CLI_NAME = "askgpt.cmd"
else:
    INSTALL_ARGV = ["bash", str(REPO / "install.sh")]
    CLI_NAME = "askgpt"


# Skipped loudly rather than quietly: an installer that is never exercised is
# how "Windows is supported" becomes a claim nobody checked.
@unittest.skipUnless(can_symlink(), SYMLINK_REASON)
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.claude = self.root / "claude"
        self.claude.mkdir()

        stub = write_stub(self.root / "codex", stdout="Logged in using ChatGPT")
        self.env = dict(
            os.environ,
            CLAUDE_CONFIG_DIR=str(self.claude),
            CODEX_BIN=str(stub),
            ASKGPT_BIN_DIR=str(self.root / "bin"),
        )

    def install(self):
        return subprocess.run(
            INSTALL_ARGV, capture_output=True, text=True, env=self.env
        )

    def cli(self):
        return self.root / "bin" / CLI_NAME

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
        entry = self.cli()
        self.assertTrue(entry.exists(), str(entry))
        if WINDOWS:
            # A generated shim rather than a symlink, so what matters is that
            # it dispatches to this repository's CLI and not some other copy.
            self.assertIn(
                str((REPO / "bin" / "askgpt").resolve()),
                entry.read_text(),
            )
        else:
            self.assertTrue(entry.is_symlink())
            self.assertEqual(
                os.path.realpath(entry), str((REPO / "bin" / "askgpt").resolve())
            )

    def test_cli_runs_afterwards(self):
        # Stronger than the executable-bit check this replaced, and meaningful
        # on both platforms: Windows has no such bit, and os.access(X_OK) there
        # answers True for any file at all, so it proved nothing.
        self.install()
        result = subprocess.run(
            [str(self.cli()), "--help"], capture_output=True, text=True, env=self.env
        )
        self.assertEqual(result.returncode, 0, result.stderr)

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

    @unittest.skipUnless(WINDOWS, "junctions are a Windows reparse point")
    def test_refuses_to_replace_a_directory_junction(self):
        # A junction is NOT a symlink, so the never-replace-a-non-symlink
        # promise covers it. Both are reparse points though, so a check on that
        # attribute waves junctions through and deletes one somebody placed
        # deliberately -- OneDrive and several dev tools create them.
        victim = self.claude / "skills" / "second-opinion"
        victim.parent.mkdir(parents=True, exist_ok=True)
        real = self.root / "junction-target"
        real.mkdir()
        (real / "precious.md").write_text("do not delete me")
        # Arguments passed separately: embedding them in one string means
        # list2cmdline re-quotes the quotes and cmd receives something else.
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(victim), str(real)],
            capture_output=True,
            check=True,
        )

        result = self.install()

        self.assertNotEqual(result.returncode, 0, "installer replaced a junction")
        self.assertIn("refusing", result.stderr.lower())
        self.assertTrue((real / "precious.md").is_file(), "junction target was destroyed")

    @unittest.skipUnless(WINDOWS, "dangling-link semantics differ per platform")
    def test_replaces_a_dangling_symlink_without_a_partial_install(self):
        # Raised by review as a supposed preflight hole: Test-Path was said to
        # report false for a dangling link, letting it pass preflight and then
        # fail at mklink mid-install. Measured otherwise -- Test-Path returns
        # TRUE for one here, so it is correctly seen as a replaceable symlink.
        # Kept as a regression test because the behaviour is worth pinning even
        # though the reported defect does not reproduce.
        victim = self.claude / "skills" / "second-opinion"
        victim.parent.mkdir(parents=True, exist_ok=True)
        gone = self.root / "target-that-disappears"
        gone.mkdir()
        subprocess.run(
            ["cmd", "/c", "mklink", "/D", str(victim), str(gone)],
            capture_output=True,
            check=True,
        )
        shutil.rmtree(gone)

        result = self.install()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            os.path.realpath(victim), str(REPO.resolve()),
            "the dangling link was not repointed at the repository",
        )

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

    def test_preflight_leaves_no_partial_install_on_conflict(self):
        # A blocking non-symlink at the LAST destination must abort before any
        # earlier link is created.
        (self.claude / "skills").mkdir(parents=True, exist_ok=True)
        victim = self.claude / "skills" / "second-opinion"
        victim.mkdir()
        (victim / "keep.md").write_text("mine")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.claude / "ask-gpt").exists(),
                         "an earlier link was created despite a later conflict")

    def test_reports_the_codex_binary_and_auth(self):
        result = self.install()
        self.assertIn("codex:", result.stdout)
        self.assertIn("auth:", result.stdout)


@unittest.skipUnless(WINDOWS, "the privilege refusal is Windows-only")
class WindowsSymlinkRefusalTest(unittest.TestCase):
    def test_refuses_clearly_without_the_privilege(self):
        # The one case that must behave well on an unprepared machine: no
        # symlink privilege. It has to explain the fix rather than fail with a
        # raw OSError, and it must not half-install anything first.
        #
        # Forced via ASKGPT_TEST_NO_SYMLINK rather than gated on whether this
        # account happens to lack the privilege. Gating meant it ran only on an
        # unprepared machine: enabling Developer Mode skipped it here, and CI is
        # elevated so it skipped there too, leaving the branch every new user
        # meets first covered nowhere at all.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claude = root / "claude"
            claude.mkdir()
            env = dict(
                os.environ,
                CLAUDE_CONFIG_DIR=str(claude),
                ASKGPT_BIN_DIR=str(root / "bin"),
                ASKGPT_TEST_NO_SYMLINK="1",
            )
            result = subprocess.run(
                INSTALL_ARGV, capture_output=True, text=True, env=env
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Developer Mode", result.stderr)
            self.assertFalse((claude / "ask-gpt").exists())
            # Nothing may be left half-done: no command links, no shim.
            self.assertFalse((claude / "commands" / "gptreview.md").exists())
            self.assertFalse((root / "bin" / CLI_NAME).exists())


if __name__ == "__main__":
    unittest.main()
