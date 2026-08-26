import subprocess
import tempfile
import unittest
from pathlib import Path

import sys

from askgpt.gitctx import default_branch, resolve_target
from askgpt.errors import GitCommandFailed, NotAGitRepo, NothingToReview

# Filenames that break a naive line-or-space split. Windows rejects " < > : | ?
# * and newline in a name, so it gets a different set rather than a thinner
# one -- the point is to keep exercising the NUL-based parser everywhere.
if sys.platform == "win32":
    AWKWARD_NAMES = (
        "a file with spaces.txt",
        "semi;colon.txt",
        "amp&and.txt",
        "brack[et].txt",
        # Escaped rather than literal so the case does not depend on this
        # file's own encoding being read back correctly.
        "café-unicode.txt",
    )
else:
    AWKWARD_NAMES = (
        "a file with spaces.txt",
        'quote"name.txt',
        "back\\slash.txt",
    )


def git(repo, *args):
    subprocess.run(
        ["git"] + list(args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


class GitTargetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "Test")
        (self.repo / "base.txt").write_text("base\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")

    def test_default_branch_falls_back_to_main(self):
        self.assertEqual(default_branch(self.repo), "main")

    def test_uncommitted_includes_untracked(self):
        (self.repo / "new.txt").write_text("new\n")
        target = resolve_target(self.repo, uncommitted=True)
        self.assertEqual(target.kind, "uncommitted")
        self.assertIn("new.txt", target.files)

    def test_uncommitted_includes_unstaged_edit(self):
        (self.repo / "base.txt").write_text("changed\n")
        target = resolve_target(self.repo, uncommitted=True)
        self.assertIn("base.txt", target.files)

    def test_uncommitted_raises_when_clean(self):
        with self.assertRaises(NothingToReview):
            resolve_target(self.repo, uncommitted=True)

    def test_commit_target_lists_that_commits_files(self):
        (self.repo / "feature.txt").write_text("f\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "feature")
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        target = resolve_target(self.repo, commit=sha)
        self.assertEqual(target.kind, "commit")
        self.assertEqual(target.files, ["feature.txt"])

    def test_base_includes_commits_since_merge_base(self):
        git(self.repo, "checkout", "-q", "-b", "feature")
        (self.repo / "committed.txt").write_text("c\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "work")
        target = resolve_target(self.repo, base="main")
        self.assertIn("committed.txt", target.files)

    def test_base_also_includes_dirty_working_tree(self):
        # The case that matters: review requested before Claude commits.
        # All three dirty classes, since an implementation covering only
        # untracked files would otherwise pass.
        git(self.repo, "checkout", "-q", "-b", "feature")
        (self.repo / "committed.txt").write_text("c\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "work")

        (self.repo / "staged.txt").write_text("s\n")
        git(self.repo, "add", "staged.txt")
        (self.repo / "base.txt").write_text("unstaged edit\n")
        (self.repo / "untracked.txt").write_text("u\n")

        target = resolve_target(self.repo, base="main")
        for expected in ("committed.txt", "staged.txt", "base.txt", "untracked.txt"):
            self.assertIn(expected, target.files)

    def test_porcelain_handles_awkward_filenames(self):
        # The awkward set differs by platform because Windows forbids " in a
        # filename outright. Dropping the case there would quietly stop testing
        # the parser, so it is swapped for names that are legal on Windows and
        # still defeat naive splitting.
        for name in AWKWARD_NAMES:
            (self.repo / name).write_text("x\n")
        target = resolve_target(self.repo, uncommitted=True)
        for name in AWKWARD_NAMES:
            self.assertIn(name, target.files, name)

    def test_porcelain_handles_renames(self):
        git(self.repo, "mv", "base.txt", "renamed.txt")
        target = resolve_target(self.repo, uncommitted=True)
        self.assertIn("renamed.txt", target.files)

    def test_auto_on_feature_branch_uses_base(self):
        git(self.repo, "checkout", "-q", "-b", "feature")
        (self.repo / "x.txt").write_text("x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        target = resolve_target(self.repo)
        self.assertEqual(target.kind, "base")

    def test_auto_on_default_branch_uses_uncommitted(self):
        (self.repo / "x.txt").write_text("x\n")
        target = resolve_target(self.repo)
        self.assertEqual(target.kind, "uncommitted")

    @unittest.skipIf(
        sys.platform == "win32",
        "Windows forbids newlines in filenames, so this fixture cannot exist; "
        "the -z NUL splitting it guards is exercised by the awkward-name test",
    )
    def test_commit_files_with_newline_are_not_split(self):
        weird = "evil\nname.txt"
        (self.repo / weird).write_text("x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "weird")
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo,
                             capture_output=True, text=True).stdout.strip()
        target = resolve_target(self.repo, commit=sha)
        self.assertIn(weird, target.files)

    def test_bad_commit_reports_git_failure_not_missing_repo(self):
        # NotAGitRepo here would tell the user to fix the wrong thing.
        with self.assertRaises(GitCommandFailed):
            resolve_target(self.repo, commit="deadbeef" * 5)

    def test_outside_a_repo_raises_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as plain:
            with self.assertRaises(NotAGitRepo):
                resolve_target(plain, uncommitted=True)

    def test_instruction_names_the_merge_base(self):
        git(self.repo, "checkout", "-q", "-b", "feature")
        (self.repo / "x.txt").write_text("x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "x")
        target = resolve_target(self.repo, base="main")
        self.assertIn("merge base", target.instruction.lower())

    def test_default_branch_prefers_origin_head_over_local_main(self):
        # A local scratch branch named main must not beat the real upstream
        # default. Without this, reordering the fallback chain passes the
        # whole suite while resolving --base against the wrong branch.
        git(self.repo, "checkout", "-q", "-b", "develop")
        git(self.repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD",
            "refs/remotes/origin/develop")
        self.assertEqual(default_branch(self.repo), "develop")

    def test_default_branch_prefers_origin_master_over_local_main(self):
        git(self.repo, "update-ref", "refs/remotes/origin/master", "HEAD")
        self.assertEqual(default_branch(self.repo), "master")

    def test_default_branch_hardcodes_main_when_nothing_matches(self):
        git(self.repo, "branch", "-m", "main", "trunk")
        self.assertEqual(default_branch(self.repo), "main")

    def test_uncommitted_takes_precedence_over_base(self):
        # Precedence is commit > uncommitted > base. The CLI makes these
        # mutually exclusive, but this function's own contract should be pinned.
        (self.repo / "x.txt").write_text("x\n")
        target = resolve_target(self.repo, base="main", uncommitted=True)
        self.assertEqual(target.kind, "uncommitted")

    def test_base_with_no_commits_ahead_still_reviews_dirty_tree(self):
        # Common: branch created, nothing committed yet, review requested.
        git(self.repo, "checkout", "-q", "-b", "feature")
        (self.repo / "wip.txt").write_text("w\n")
        target = resolve_target(self.repo, base="main")
        self.assertEqual(target.kind, "base")
        self.assertIn("wip.txt", target.files)


if __name__ == "__main__":
    unittest.main()
