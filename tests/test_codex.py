import os
import stat
import tempfile
import unittest
from pathlib import Path

from askgpt.codex import find_codex
from askgpt.errors import CodexNotFound


def make_exe(directory: Path, name: str = "codex") -> Path:
    p = directory / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return p


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


class CheckAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _stub(self, body: str) -> str:
        p = self.dir / "codex-stub"
        p.write_text("#!/bin/sh\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        return str(p)

    def test_accepts_logged_in(self):
        from askgpt.codex import check_auth

        check_auth(self._stub('echo "Logged in using ChatGPT"'))

    def test_rejects_logged_out(self):
        from askgpt.codex import check_auth
        from askgpt.errors import CodexNotAuthenticated

        with self.assertRaises(CodexNotAuthenticated):
            check_auth(self._stub('echo "Not logged in"; exit 1'))


if __name__ == "__main__":
    unittest.main()
