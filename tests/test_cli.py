import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "askgpt"

SECRET = "sk-abcdefghij0123456789ABCD"


def run_cli(*args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, str(CLI)] + list(args),
        cwd=str(cwd or REPO),
        capture_output=True,
        text=True,
        env=env if env is not None else dict(os.environ),
    )


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo)] + list(args), check=True, capture_output=True
    )


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.marker = self.root / "codex-was-run"

    def make_repo(self, dirty=True):
        repo = self.root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        git(repo, "config", "user.email", "t@e.com")
        git(repo, "config", "user.name", "T")
        (repo / "f.txt").write_text("x\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init")
        if dirty:
            (repo / "dirty.txt").write_text("y\n")
        return repo

    def landmine(self):
        """A fake codex that records being run and fails. Proves 'sends nothing'."""
        path = self.root / "codex-landmine"
        path.write_text("#!/bin/sh\ntouch '" + str(self.marker) + "'\nexit 1\n")
        path.chmod(0o755)
        return str(path)

    def working_codex(self, thread_id="TID42", body="REVIEW TEXT"):
        """A fake codex that answers `login status` and writes a result."""
        path = self.root / "codex-ok"
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "argv = sys.argv\n"
            "if 'login' in argv:\n"
            "    print('Logged in using ChatGPT')\n"
            "    sys.exit(0)\n"
            "open(argv[argv.index('-o') + 1], 'w').write('" + body + "')\n"
            "print('{\"type\":\"thread.started\",\"thread_id\":\"" + thread_id + "\"}')\n"
        )
        path.chmod(0o755)
        return str(path)

    def env(self, **extra):
        base = dict(os.environ, ASKGPT_STATE_DIR=str(self.root / "state"))
        base.update(extra)
        return base


class HelpTest(CliTestCase):
    def test_help_lists_subcommands(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0)
        for name in ("review", "ask", "follow"):
            self.assertIn(name, result.stdout)

    def test_unknown_subcommand_exits_nonzero(self):
        self.assertNotEqual(run_cli("nonsense").returncode, 0)


class ReviewTest(CliTestCase):
    def test_dry_run_prints_payload_and_sends_nothing(self):
        repo = self.make_repo()
        result = run_cli(
            "review", "--uncommitted", "--dry-run", "--task", "T",
            "--cwd", str(repo), env=self.env(CODEX_BIN=self.landmine()),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertIn("dirty.txt", result.stdout)
        self.assertFalse(self.marker.exists(), "Codex ran during a dry run")

    def test_dry_run_payload_file_still_exists(self):
        # The banner names a path; deleting it makes the advertised file a lie.
        repo = self.make_repo()
        result = run_cli(
            "review", "--uncommitted", "--dry-run", "--task", "T",
            "--cwd", str(repo), env=self.env(CODEX_BIN=self.landmine()),
        )
        path = [w for w in result.stdout.split() if w.endswith("payload.md")]
        self.assertTrue(path, result.stdout)
        self.assertTrue(Path(path[0]).is_file())

    def test_outside_git_repo_reports_clearly(self):
        plain = self.root / "plain"
        plain.mkdir()
        result = run_cli("review", "--uncommitted", "--dry-run", "--cwd", str(plain),
                         env=self.env())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git", (result.stderr + result.stdout).lower())

    def test_clean_tree_reports_nothing_to_review(self):
        repo = self.make_repo(dirty=False)
        result = run_cli("review", "--uncommitted", "--dry-run", "--cwd", str(repo),
                         env=self.env(CODEX_BIN=self.landmine()))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())


class SecretGateTest(CliTestCase):
    def test_secret_in_payload_halts_before_sending(self):
        repo = self.make_repo()
        result = run_cli(
            "review", "--uncommitted", "--dry-run", "--task", "key is " + SECRET,
            "--cwd", str(repo), env=self.env(CODEX_BIN=self.landmine()),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret", (result.stderr + result.stdout).lower())
        self.assertFalse(self.marker.exists())

    def test_secret_warning_does_not_reprint_the_secret(self):
        repo = self.make_repo()
        result = run_cli(
            "review", "--uncommitted", "--dry-run", "--task", "key is " + SECRET,
            "--cwd", str(repo), env=self.env(CODEX_BIN=self.landmine()),
        )
        self.assertNotIn(SECRET, result.stderr + result.stdout)

    def test_allow_secrets_overrides_the_halt(self):
        repo = self.make_repo()
        result = run_cli(
            "review", "--uncommitted", "--dry-run", "--allow-secrets",
            "--task", "key is " + SECRET, "--cwd", str(repo),
            env=self.env(CODEX_BIN=self.landmine()),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN", result.stdout)


class AskTest(CliTestCase):
    def make_transcript(self, repo, session):
        config = self.root / "claude"
        slug = str(repo.resolve()).replace("/", "-")
        project = config / "projects" / slug
        project.mkdir(parents=True)
        rows = [
            {"type": "user", "message": {"role": "user", "content": "REAL QUESTION"}},
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "is_error": True, "content": "BOOM EVIDENCE"}]}},
            {"type": "user", "message": {"role": "user", "content": "THE INVOCATION"}},
        ]
        (project / (session + ".jsonl")).write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n"
        )
        return config

    def test_missing_session_id_warns_about_the_mtime_fallback(self):
        # The fallback can select another window's session. Silent is not an
        # option; dropping the warning passed the whole suite.
        repo = self.make_repo()
        config = self.make_transcript(repo, "33333333-4444-5555-6666-777777777777")
        result = run_cli(
            "ask", "Q?", "--dry-run", "--cwd", str(repo),
            env=self.env(CLAUDE_CONFIG_DIR=str(config), CODEX_BIN=self.landmine()),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mtime", result.stderr.lower())

    def test_dry_run_packs_the_named_session_offline(self):
        # The ask path's session mapping, transcript resolution, invocation
        # removal and packing are otherwise never exercised end to end.
        repo = self.make_repo()
        session = "11111111-2222-3333-4444-555555555555"
        config = self.make_transcript(repo, session)
        result = run_cli(
            "ask", "Is this sound?", "--session-id", session, "--dry-run",
            "--cwd", str(repo),
            env=self.env(CLAUDE_CONFIG_DIR=str(config), CODEX_BIN=self.landmine()),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Is this sound?", result.stdout)
        self.assertIn("REAL QUESTION", result.stdout)
        self.assertIn("BOOM EVIDENCE", result.stdout)      # failed output retained
        self.assertNotIn("THE INVOCATION", result.stdout)  # last turn dropped
        self.assertFalse(self.marker.exists())


class PlumbingTest(CliTestCase):
    """Flags that are silently ignorable. Each of these was droppable with the
    whole suite green until it had a test."""

    def recorder(self):
        path = self.root / "codex-recorder"
        self.argv_log = self.root / "argv.txt"
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "argv = sys.argv\n"
            "if 'login' in argv:\n"
            "    print('Logged in using ChatGPT')\n"
            "    sys.exit(0)\n"
            "open('" + str(self.argv_log) + "', 'w').write(' '.join(argv))\n"
            "open(argv[argv.index('-o') + 1], 'w').write('OK')\n"
        )
        path.chmod(0o755)
        return str(path)

    def test_model_flag_reaches_codex(self):
        # Silently ignoring --model means the user believes they overrode the
        # model and did not -- and the review looks completely normal.
        repo = self.make_repo()
        result = run_cli(
            "review", "--uncommitted", "--task", "T", "--model", "gpt-custom-999",
            "--cwd", str(repo), env=self.env(CODEX_BIN=self.recorder()),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("gpt-custom-999", self.argv_log.read_text())

    def test_keep_preserves_artifacts_on_a_real_run(self):
        repo = self.make_repo()
        result = run_cli(
            "review", "--uncommitted", "--task", "T", "--keep",
            "--cwd", str(repo), env=self.env(CODEX_BIN=self.working_codex()),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        kept = [w for w in result.stderr.split() if "askgpt-" in w]
        self.assertTrue(kept, result.stderr)
        self.assertTrue(Path(kept[0]).is_dir())

    def test_preflight_warns_about_sensitive_files(self):
        # Deleting the preflight call entirely passed the whole suite. It is a
        # privacy feature; its absence must not be silent.
        repo = self.make_repo()
        (repo / ".env").write_text("TOKEN=x\n")
        result = run_cli(
            "review", "--uncommitted", "--task", "T",
            "--cwd", str(repo), env=self.env(CODEX_BIN=self.working_codex()),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(".env", result.stderr)
        self.assertIn("read", result.stderr.lower())


class FollowTest(CliTestCase):
    def test_without_a_prior_thread_errors_clearly(self):
        repo = self.make_repo()
        result = run_cli("follow", "and another thing", "--session-id", "s1",
                         "--cwd", str(repo), env=self.env(CODEX_BIN=self.landmine()))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no prior", (result.stderr + result.stdout).lower())
        self.assertFalse(self.marker.exists())

    def test_thread_id_is_saved_then_resumed(self):
        repo = self.make_repo()
        codex = self.working_codex(thread_id="TID42")
        first = run_cli("review", "--uncommitted", "--task", "T", "--session-id", "s1",
                        "--cwd", str(repo), env=self.env(CODEX_BIN=codex))
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("REVIEW TEXT", first.stdout)
        saved = (self.root / "state" / "threads" / "s1.json").read_text()
        self.assertIn("TID42", saved)

        second = run_cli("follow", "and another thing", "--session-id", "s1",
                         "--cwd", str(repo), env=self.env(CODEX_BIN=codex))
        self.assertEqual(second.returncode, 0, second.stderr)


if __name__ == "__main__":
    unittest.main()
