import os
import stat
import tempfile
import unittest
from pathlib import Path

from askgpt.codex import check_auth, find_codex
from askgpt.errors import CodexNotAuthenticated, CodexNotFound


def _write_script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def make_exe(directory: Path, name: str = "codex") -> Path:
    return _write_script(directory / name, "exit 0")


class FindCodexTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_codex_bin_env_var_wins(self):
        exe = make_exe(self.dir, "custom-codex")
        env = {"CODEX_BIN": str(exe), "PATH": ""}
        self.assertEqual(find_codex(env=env, candidates=()), str(exe))

    def test_falls_back_to_path(self):
        exe = make_exe(self.dir)
        env = {"PATH": str(self.dir)}
        self.assertEqual(find_codex(env=env, candidates=()), str(exe))

    def test_falls_back_to_candidate_paths(self):
        exe = make_exe(self.dir)
        env = {"PATH": ""}
        self.assertEqual(find_codex(env=env, candidates=(str(exe),)), str(exe))

    def test_non_executable_candidate_is_skipped(self):
        plain = self.dir / "not-exec"
        plain.write_text("x")
        env = {"PATH": ""}
        with self.assertRaises(CodexNotFound):
            find_codex(env=env, candidates=(str(plain),))

    def test_error_lists_every_path_tried(self):
        env = {"CODEX_BIN": "/nope/a", "PATH": ""}
        with self.assertRaises(CodexNotFound) as ctx:
            find_codex(env=env, candidates=("/nope/b",))
        message = str(ctx.exception)
        self.assertIn("/nope/a", message)
        self.assertIn("/nope/b", message)

    def test_codex_bin_beats_a_real_path_match(self):
        on_path = make_exe(self.dir, "codex")
        override = make_exe(self.dir, "preferred-codex")
        env = {"CODEX_BIN": str(override), "PATH": str(self.dir)}
        self.assertEqual(find_codex(env=env, candidates=()), str(override))

    def test_path_beats_candidates(self):
        on_path = make_exe(self.dir, "codex")
        candidate = make_exe(self.dir, "fallback-codex")
        env = {"PATH": str(self.dir)}
        self.assertEqual(
            find_codex(env=env, candidates=(str(candidate),)), str(on_path)
        )


class CheckAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _stub(self, body: str) -> str:
        return str(_write_script(self.dir / "codex-stub", body))

    def test_accepts_logged_in(self):
        check_auth(self._stub('echo "Logged in using ChatGPT"'))

    def test_rejects_logged_out(self):
        with self.assertRaises(CodexNotAuthenticated):
            check_auth(self._stub('echo "Not logged in"; exit 1'))

    # The two cases below isolate each half of the `returncode == 0 AND
    # "Logged in" in stdout` gate. Without them, an implementation checking
    # only ONE of the two conditions passes the whole suite -- verified by
    # mutation testing, where both half-implementations went undetected.

    def test_rejects_clean_exit_without_logged_in_marker(self):
        with self.assertRaises(CodexNotAuthenticated):
            check_auth(self._stub('echo "some other output"; exit 0'))

    def test_rejects_logged_in_text_with_nonzero_exit(self):
        with self.assertRaises(CodexNotAuthenticated):
            check_auth(self._stub('echo "Logged in using ChatGPT"; exit 1'))

    def test_missing_binary_raises_codex_not_found(self):
        with self.assertRaises(CodexNotFound):
            check_auth(str(self.dir / "does-not-exist"))

    def test_timeout_is_reported_as_not_authenticated(self):
        with self.assertRaises(CodexNotAuthenticated):
            check_auth(self._stub("sleep 5"), timeout=1)

    def test_failure_message_includes_what_codex_said(self):
        with self.assertRaises(CodexNotAuthenticated) as ctx:
            check_auth(self._stub('echo "config parse error" >&2; exit 2'))
        self.assertIn("config parse error", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
