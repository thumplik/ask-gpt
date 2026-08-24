import stat
import tempfile
import unittest
from pathlib import Path

from askgpt.runner import MODEL, build_argv, parse_thread_id, run
from askgpt.errors import AskGptError, ModelUnavailable, QuotaExhausted


class BuildArgvTest(unittest.TestCase):
    def test_pins_model_and_read_only_sandbox(self):
        argv = build_argv("/bin/codex", out_path="/tmp/o.md")
        self.assertEqual(argv[:2], ["/bin/codex", "exec"])
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], MODEL)
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")

    def test_ignores_user_config(self):
        self.assertIn("--ignore-user-config", build_argv("/bin/codex", out_path="/tmp/o.md"))

    def test_reads_prompt_from_stdin(self):
        self.assertEqual(build_argv("/bin/codex", out_path="/tmp/o.md")[-1], "-")

    def test_sandbox_flag_precedes_resume_subcommand(self):
        # Verified against the CLI: `exec resume --last -s read-only` fails with
        # "unexpected argument '-s'". Sandbox belongs on the exec side.
        argv = build_argv("/bin/codex", out_path="/tmp/o.md", resume_thread="T1")
        self.assertLess(argv.index("-s"), argv.index("resume"))
        self.assertEqual(argv[argv.index("resume") + 1], "T1")

    def test_skips_the_git_repo_check(self):
        # `askgpt ask` must work outside a repository.
        self.assertIn(
            "--skip-git-repo-check", build_argv("/bin/codex", out_path="/tmp/o.md")
        )

    def test_never_uses_last(self):
        self.assertNotIn("--last", build_argv("/bin/codex", out_path="/tmp/o.md", resume_thread="T1"))

    def test_requests_the_json_event_stream(self):
        # --json is the ONLY reason thread.started is parseable. Drop it and
        # every follow-up silently breaks, with no test noticing.
        self.assertIn("--json", build_argv("/bin/codex", out_path="/tmp/o.md"))

    def test_skip_git_repo_check_can_be_disabled(self):
        argv = build_argv("/bin/codex", out_path="/tmp/o.md", skip_git_repo_check=False)
        self.assertNotIn("--skip-git-repo-check", argv)


class ParseThreadIdTest(unittest.TestCase):
    def test_finds_thread_started(self):
        stream = '{"type":"thread.started","thread_id":"abc"}\n'
        self.assertEqual(parse_thread_id(stream), "abc")

    def test_finds_it_when_not_first(self):
        # Position is an observation of one CLI version, not a guarantee.
        stream = (
            '{"type":"session.configured"}\n'
            '{"type":"thread.started","thread_id":"xyz"}\n'
        )
        self.assertEqual(parse_thread_id(stream), "xyz")

    def test_tolerates_non_json_lines(self):
        stream = 'warning: something\n{"type":"thread.started","thread_id":"ok"}\n'
        self.assertEqual(parse_thread_id(stream), "ok")

    def test_returns_none_when_absent(self):
        self.assertIsNone(parse_thread_id('{"type":"turn.started"}\n'))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _stub(self, body):
        path = self.dir / "codex-stub"
        path.write_text("#!/usr/bin/env python3\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return str(path)

    def test_returns_text_and_thread_id(self):
        stub = self._stub(
            "import sys\n"
            "argv = sys.argv\n"
            "out = argv[argv.index('-o') + 1]\n"
            "open(out, 'w').write('REVIEW BODY')\n"
            "print('{\"type\":\"thread.started\",\"thread_id\":\"T9\"}')\n"
        )
        out = self.dir / "out.md"
        result = run(stub, "payload", cwd=self.dir, out_path=out)
        self.assertEqual(result.text, "REVIEW BODY")
        self.assertEqual(result.thread_id, "T9")

    def test_fails_closed_on_unsupported_model(self):
        # Real rejection exits non-zero; a stub exiting 0 proves only string matching.
        stub = self._stub(
            "import sys\n"
            "print('ERROR: The model is not supported when using Codex with a "
            "ChatGPT account.')\n"
            "sys.exit(1)\n"
        )
        with self.assertRaises(ModelUnavailable) as ctx:
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertNotIn("terra", str(ctx.exception).lower())

    def test_nonzero_exit_fails_even_with_output_written(self):
        # Codex can write partial output and then fail. Returning it would report
        # an auth or transport failure as a successful review.
        stub = self._stub(
            "import sys\n"
            "argv = sys.argv\n"
            "open(argv[argv.index('-o') + 1], 'w').write('PARTIAL')\n"
            "sys.stderr.write('transport failure\\n')\n"
            "sys.exit(3)\n"
        )
        with self.assertRaises(AskGptError) as ctx:
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertIn("transport failure", str(ctx.exception))

    def test_timeout_is_reported_and_not_retried(self):
        # The whole timeout branch was otherwise untested. Retrying here would
        # silently burn subscription quota.
        stub = self._stub("import time\ntime.sleep(30)\n")
        with self.assertRaises(AskGptError) as ctx:
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md", timeout=1)
        message = str(ctx.exception).lower()
        self.assertIn("timed out", message)
        self.assertIn("not retrying", message)

    def test_quota_exhaustion_is_reported_plainly(self):
        stub = self._stub(
            "import sys\n"
            "sys.stderr.write('429: usage limit reached for this plan\\n')\n"
            "sys.exit(1)\n"
        )
        with self.assertRaises(QuotaExhausted):
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")

    def test_unexecutable_binary_stays_inside_the_error_contract(self):
        # errors.py states callers catch AskGptError; a stale CODEX_BIN or a
        # moved binary must not escape as a raw OSError traceback.
        with self.assertRaises(AskGptError):
            run(str(self.dir / "no-such-codex"), "payload", cwd=self.dir,
                out_path=self.dir / "out.md")

    def test_success_result_carries_returncode_and_stderr(self):
        stub = self._stub(
            "import sys\n"
            "argv = sys.argv\n"
            "open(argv[argv.index('-o') + 1], 'w').write('OK BODY')\n"
            "sys.stderr.write('a warning\\n')\n"
        )
        result = run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertEqual(result.returncode, 0)
        self.assertIn("a warning", result.stderr)

    def test_unsupported_model_detected_on_stderr_too(self):
        # The marker can land on either stream. Checking only one lets a real
        # rejection through as a generic failure with the wrong guidance.
        stub = self._stub(
            "import sys\n"
            "sys.stderr.write('ERROR: the model is not supported when using "
            "Codex with a ChatGPT account\\n')\n"
            "sys.exit(1)\n"
        )
        with self.assertRaises(ModelUnavailable):
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")


if __name__ == "__main__":
    unittest.main()
