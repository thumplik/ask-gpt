import stat
import sys
import tempfile
import unittest
from pathlib import Path

from askgpt.runner import MODEL, build_argv, model_chain, parse_thread_id, run
from askgpt.errors import AskGptError, ModelUnavailable, QuotaExhausted
from stubs import write_program


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

    @unittest.skipUnless(sys.platform == "win32", "Windows sandbox policy only")
    def test_windows_permits_the_reviewer_to_read_files(self):
        # Without this override the reviewer reads NOTHING and returns an empty
        # review while exiting 0 -- the worst failure shape available, since it
        # looks like a clean pass. Codex reads files on Windows by spawning
        # powershell.exe, its default Windows sandbox rejects that, and the
        # setting permitting it is discarded by --ignore-user-config.
        argv = build_argv("codex.exe", out_path="o.md")
        self.assertIn("-c", argv)
        self.assertIn('windows.sandbox="unelevated"', argv)
        # The hardening must survive alongside it: -c overrides a single key,
        # it does not re-admit the user's config file.
        self.assertIn("--ignore-user-config", argv)
        # And the sandbox must still be read-only. "unelevated" is chosen over
        # "elevated" -- the only other accepted value -- as the lesser
        # privilege; both were measured to restore reads identically.
        self.assertEqual(argv[argv.index("-s") + 1], "read-only")

    @unittest.skipIf(sys.platform == "win32", "POSIX must not carry Windows config")
    def test_posix_carries_no_windows_sandbox_override(self):
        self.assertNotIn('windows.sandbox="unelevated"', build_argv("/bin/codex", out_path="/tmp/o.md"))

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


class ModelChainTest(unittest.TestCase):
    def test_default_chain_puts_the_pinned_model_first(self):
        self.assertEqual(model_chain()[0], MODEL)

    def test_fallback_follows_the_pinned_model(self):
        self.assertGreater(len(model_chain()), 1)

    def test_no_fallback_yields_only_the_request(self):
        self.assertEqual(model_chain(fallback=False), [MODEL])

    def test_requested_model_is_never_duplicated(self):
        chain = model_chain("gpt-5.6-terra")
        self.assertEqual(len(chain), len(set(chain)))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _stub(self, body):
        return write_program(self.dir / "codex-stub", body)

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

    def test_raises_when_every_model_in_the_chain_is_unavailable(self):
        # Fallback means "try the next one", not "succeed anyway". With the
        # whole chain exhausted this must still raise rather than returning an
        # empty result that reads like a clean review.
        # (The real rejection shape, verified against the CLI: a structured
        # error event on stdout, stderr empty.)
        stub = self._stub(
            "import sys\n"
            "print('{\"type\":\"error\",\"message\":\"The model is not supported "
            "when using Codex with a ChatGPT account.\"}')\n"
            "sys.exit(1)\n"
        )
        with self.assertRaises(ModelUnavailable):
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")

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

    def test_reviewing_text_about_quotas_is_not_a_quota_error(self):
        # Codex echoes the material it reviews into its stream. Scanning a
        # SUCCESSFUL run for error phrases misreads reviewed content as an
        # error about the run: a real review of this repo was discarded as a
        # quota failure because the repo says "quota" 27 times.
        stub = self._stub(
            "import sys\n"
            "argv = sys.argv\n"
            "open(argv[argv.index('-o') + 1], 'w').write('the code mentions quota, "
            "usage limit, rate limit and 429')\n"
            "print('reviewing rate limit handling, error 429, quota logic')\n"
        )
        result = run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertIn("quota", result.text)

    def test_reviewed_content_is_not_mistaken_for_a_run_error(self):
        # Sol's repro, from its review of the first fix: reviewed text
        # containing a quota phrase plus an UNRELATED failure must report the
        # real failure. Restricting to non-zero exits was not enough -- only
        # structured extraction distinguishes echoed content from diagnostics.
        stub = self._stub(
            "import sys\n"
            "print('{\"type\":\"item.completed\",\"item\":{\"type\":"
            "\"agent_message\",\"text\":\"the code says quota exceeded\"}}')\n"
            "sys.stderr.write('connection reset\\n')\n"
            "sys.exit(3)\n"
        )
        with self.assertRaises(AskGptError) as ctx:
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertNotIsInstance(ctx.exception, QuotaExhausted)
        self.assertIn("connection reset", str(ctx.exception))

    def test_generic_failure_surfaces_stdout_diagnostics(self):
        # Real Codex errors are structured events on stdout with stderr empty,
        # so reporting stderr alone gave the user an exit code and nothing else.
        stub = self._stub(
            "import sys\n"
            "print('{\"type\":\"error\",\"message\":\"connection reset\"}')\n"
            "sys.exit(1)\n"
        )
        with self.assertRaises(AskGptError) as ctx:
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertIn("connection reset", str(ctx.exception))

    def test_exit_zero_without_output_is_an_error(self):
        stub = self._stub("import sys\nsys.exit(0)\n")
        with self.assertRaises(AskGptError) as ctx:
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertIn("no response", str(ctx.exception).lower())

    def test_falls_back_when_the_pinned_model_is_unavailable(self):
        # Rejecting a slug costs no quota (400 before inference), so advancing
        # the chain here does not violate the never-retry rule.
        stub = self._stub(
            "import sys\n"
            "argv = sys.argv\n"
            "model = argv[argv.index('-m') + 1]\n"
            "if model == '" + MODEL + "':\n"
            "    print('{\"type\":\"error\",\"message\":\"is not supported when "
            "using Codex with a ChatGPT account\"}')\n"
            "    sys.exit(1)\n"
            "open(argv[argv.index('-o') + 1], 'w').write('FALLBACK REVIEW')\n"
        )
        result = run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertEqual(result.text, "FALLBACK REVIEW")
        self.assertNotEqual(result.model, MODEL)
        self.assertTrue(result.fell_back)

    def test_no_fallback_restores_fail_closed(self):
        stub = self._stub(
            "import sys\n"
            "print('{\"type\":\"error\",\"message\":\"is not supported when "
            "using Codex with a ChatGPT account\"}')\n"
            "sys.exit(1)\n"
        )
        with self.assertRaises(ModelUnavailable):
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md",
                fallback=False)

    def test_successful_first_attempt_is_not_marked_as_fallback(self):
        stub = self._stub(
            "import sys\n"
            "argv = sys.argv\n"
            "open(argv[argv.index('-o') + 1], 'w').write('OK')\n"
        )
        result = run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertFalse(result.fell_back)
        self.assertEqual(result.model, MODEL)

    def test_transport_failure_does_not_advance_the_chain(self):
        # Only ModelUnavailable may retry. Retrying a real failure would cost
        # quota and would not help.
        counter = self.dir / "attempts"
        stub = self._stub(
            "import sys, pathlib\n"
            # repr, not manual quoting: a Windows path is full of backslashes
            # and lands in this stub's source as escape sequences otherwise.
            "p = pathlib.Path(" + repr(str(counter)) + ")\n"
            "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')\n"
            "sys.stderr.write('connection reset\\n')\n"
            "sys.exit(1)\n"
        )
        with self.assertRaises(AskGptError):
            run(stub, "payload", cwd=self.dir, out_path=self.dir / "out.md")
        self.assertEqual(counter.read_text(), "1")

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
