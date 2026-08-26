import os
import tempfile
import unittest
from pathlib import Path

from askgpt.codex import TESTED_VERSION, check_auth, find_codex, version, version_warning
from askgpt.errors import CodexNotAuthenticated, CodexNotFound
from stubs import make_exe, write_stub


class FindCodexTest(unittest.TestCase):
    def assertSamePath(self, left, right):
        # shutil.which appends PATHEXT entries verbatim and PATHEXT is upper
        # case, so a resolved path comes back as codex.CMD while the file was
        # written as codex.cmd. On Windows those name one file; comparing the
        # strings raw asserts a case convention nobody promised.
        self.assertEqual(os.path.normcase(str(left)), os.path.normcase(str(right)))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_codex_bin_env_var_wins(self):
        exe = make_exe(self.dir, "custom-codex")
        env = {"CODEX_BIN": str(exe), "PATH": ""}
        self.assertSamePath(find_codex(env=env, candidates=()), exe)

    def test_falls_back_to_path(self):
        exe = make_exe(self.dir)
        env = {"PATH": str(self.dir)}
        self.assertSamePath(find_codex(env=env, candidates=()), exe)

    def test_falls_back_to_candidate_paths(self):
        exe = make_exe(self.dir)
        env = {"PATH": ""}
        self.assertSamePath(find_codex(env=env, candidates=(str(exe),)), exe)

    def test_non_executable_candidate_is_skipped(self):
        plain = self.dir / "not-exec"
        plain.write_text("x")
        env = {"PATH": ""}
        with self.assertRaises(CodexNotFound):
            find_codex(env=env, candidates=(str(plain),))

    def test_error_lists_every_path_tried(self):
        # Paths are reported in this platform's own notation -- on Windows
        # "/nope/b" is shown as "\nope\b" -- so the expectation is normalised
        # too. What is being asserted is that no attempted location is omitted
        # from the message, not the separator it is spelled with.
        env = {"CODEX_BIN": "/nope/a", "PATH": ""}
        with self.assertRaises(CodexNotFound) as ctx:
            find_codex(env=env, candidates=("/nope/b",))
        message = str(ctx.exception)
        self.assertIn(os.path.normpath("/nope/a"), message)
        self.assertIn(os.path.normpath("/nope/b"), message)

    def test_codex_bin_beats_a_real_path_match(self):
        on_path = make_exe(self.dir, "codex")
        override = make_exe(self.dir, "preferred-codex")
        env = {"CODEX_BIN": str(override), "PATH": str(self.dir)}
        self.assertSamePath(find_codex(env=env, candidates=()), override)

    def test_path_beats_candidates(self):
        on_path = make_exe(self.dir, "codex")
        candidate = make_exe(self.dir, "fallback-codex")
        env = {"PATH": str(self.dir)}
        self.assertSamePath(
            find_codex(env=env, candidates=(str(candidate),)), on_path
        )


class CheckAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _stub(self, **spec) -> str:
        return write_stub(self.dir / "codex-stub", **spec)

    def test_accepts_logged_in_reported_on_stderr(self):
        # This is what the real Codex CLI does: the status line goes to stderr
        # and stdout is empty. A stub echoing to stdout hid this for the whole
        # build, and check_auth rejected every genuinely logged-in user.
        check_auth(self._stub(stderr="Logged in using ChatGPT"))

    def test_accepts_logged_in(self):
        check_auth(self._stub(stdout="Logged in using ChatGPT"))

    def test_rejects_logged_out(self):
        with self.assertRaises(CodexNotAuthenticated):
            check_auth(self._stub(stdout="Not logged in", code=1))

    # The two cases below isolate each half of the `returncode == 0 AND
    # "Logged in" in stdout` gate. Without them, an implementation checking
    # only ONE of the two conditions passes the whole suite -- verified by
    # mutation testing, where both half-implementations went undetected.

    def test_rejects_clean_exit_without_logged_in_marker(self):
        with self.assertRaises(CodexNotAuthenticated):
            check_auth(self._stub(stdout="some other output", code=0))

    def test_rejects_logged_in_text_with_nonzero_exit(self):
        with self.assertRaises(CodexNotAuthenticated):
            check_auth(self._stub(stdout="Logged in using ChatGPT", code=1))

    def test_missing_binary_raises_codex_not_found(self):
        with self.assertRaises(CodexNotFound):
            check_auth(str(self.dir / "does-not-exist"))

    def test_timeout_is_reported_as_not_authenticated(self):
        with self.assertRaises(CodexNotAuthenticated):
            check_auth(self._stub(delay=5), timeout=1)

    def test_failure_message_includes_what_codex_said(self):
        with self.assertRaises(CodexNotAuthenticated) as ctx:
            check_auth(self._stub(stderr="config parse error", code=2))
        self.assertIn("config parse error", str(ctx.exception))


class VersionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _stub(self, reported):
        return write_stub(self.dir / "codex-v", stdout=reported)

    def test_parses_the_reported_version(self):
        self.assertEqual(version(self._stub("codex-cli 1.2.3")), "1.2.3")

    def test_matching_version_produces_no_warning(self):
        stub = self._stub("codex-cli " + TESTED_VERSION)
        self.assertIsNone(version_warning(stub))

    def test_different_version_warns_and_names_both(self):
        stub = self._stub("codex-cli 9.9.9")
        message = version_warning(stub)
        self.assertIn("9.9.9", message)
        self.assertIn(TESTED_VERSION, message)

    def test_unknown_version_still_warns(self):
        self.assertIsNotNone(version_warning(str(self.dir / "absent")))


if __name__ == "__main__":
    unittest.main()
