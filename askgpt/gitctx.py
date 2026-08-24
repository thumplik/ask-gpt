"""Resolve what a review should cover."""

import subprocess
from dataclasses import dataclass, field

from .errors import GitCommandFailed, NotAGitRepo, NothingToReview


@dataclass
class Target:
    kind: str           # "uncommitted" | "commit" | "base"
    ref: str            # merge base sha, commit sha, or "" for uncommitted
    files: list         # repo-relative paths
    instruction: str    # prose telling Codex exactly what to inspect
    description: str = field(default="")


def git(repo, *args):
    result = subprocess.run(
        ["git"] + list(args), cwd=str(repo), capture_output=True, text=True
    )
    if result.returncode != 0:
        raise GitCommandFailed(
            "git " + " ".join(args) + " failed:\n" + result.stderr.strip()
        )
    return result.stdout.strip()


def is_git_repo(repo):
    try:
        return git(repo, "rev-parse", "--is-inside-work-tree") == "true"
    except GitCommandFailed:
        return False


def default_branch(repo):
    """origin/HEAD, else an existing origin/main|master, else local, else main."""
    try:
        ref = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        return ref.split("/", 1)[1] if "/" in ref else ref
    except GitCommandFailed:
        pass

    for name in ("main", "master"):
        try:
            git(repo, "show-ref", "--verify", "--quiet", "refs/remotes/origin/" + name)
            return name
        except GitCommandFailed:
            continue

    for name in ("main", "master"):
        try:
            git(repo, "show-ref", "--verify", "--quiet", "refs/heads/" + name)
            return name
        except GitCommandFailed:
            continue

    return "main"


def current_branch(repo):
    """Branch name, or the literal "HEAD" when detached.

    "HEAD" never equals default_branch(), so auto-detect routes a detached
    HEAD into base mode. That is deliberate: reviewing against the default
    branch is more useful there than reviewing an empty uncommitted set.
    """
    return git(repo, "rev-parse", "--abbrev-ref", "HEAD")


def _dirty_files(repo):
    """Staged, unstaged, and untracked paths.

    Uses -z. The human-readable format quotes and escapes paths containing
    spaces, quotes, backslashes, or newlines, and splitting a rename on " -> "
    breaks when that sequence appears inside a filename. NUL-delimited output
    has neither problem.
    """
    raw = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if raw.returncode != 0:
        raise GitCommandFailed(raw.stderr.strip())

    fields = raw.stdout.split("\0")
    files = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        status, _, path = entry[:2], entry[2:3], entry[3:]
        # Rename/copy entries are followed by the ORIGINAL path as its own field.
        # "C" is unreachable in practice -- `git status` has no copy-detection
        # option, unlike `git diff` -- but the porcelain format documents it, so
        # it is handled rather than assumed away.
        if status and status[0] in ("R", "C"):
            index += 1
        if path:
            files.append(path)
    return files


def resolve_target(repo, base=None, commit=None, uncommitted=False):
    """Resolve the review target.

    Precedence when more than one is given: commit > uncommitted > base.
    The CLI makes them mutually exclusive, but the contract is pinned here
    and by test_uncommitted_takes_precedence_over_base so it cannot drift.
    With none given, auto-detect: non-default branch -> base, else uncommitted.
    """
    if not is_git_repo(repo):
        raise NotAGitRepo("Not a git repository: " + str(repo))

    if commit:
        files = [
            f for f in git(repo, "show", "--name-only", "--pretty=format:", commit).splitlines() if f
        ]
        if not files:
            raise NothingToReview("Commit " + commit + " touches no files.")
        return Target(
            kind="commit",
            ref=commit,
            files=files,
            description="commit " + commit[:12],
            instruction=(
                "Review exactly the changes introduced by commit " + commit + ".\n"
                "Inspect them with: git show " + commit
            ),
        )

    if not uncommitted and base is None:
        # Auto-detect: feature branch -> base; default branch -> uncommitted.
        if current_branch(repo) != default_branch(repo):
            base = default_branch(repo)
        else:
            uncommitted = True

    if uncommitted:
        files = _dirty_files(repo)
        if not files:
            raise NothingToReview("No staged, unstaged, or untracked changes.")
        return Target(
            kind="uncommitted",
            ref="",
            files=files,
            description="uncommitted changes",
            instruction=(
                "Review the staged, unstaged, and untracked changes in this working\n"
                "tree. Inspect them with: git diff HEAD, git diff --cached, and by\n"
                "reading the untracked files listed below."
            ),
        )

    # Prefer origin/<base> for the merge base. default_branch() returns a bare
    # name for comparison against current_branch(), but using that bare name
    # here resolves the LOCAL ref -- and a stale local main silently includes
    # commits already merged upstream, producing findings against code the
    # branch would not deliver.
    base_ref = base
    try:
        git(repo, "show-ref", "--verify", "--quiet", "refs/remotes/origin/" + base)
        base_ref = "origin/" + base
    except GitCommandFailed:
        pass
    merge_base = git(repo, "merge-base", base_ref, "HEAD")
    committed = [
        f for f in git(repo, "diff", "--name-only", merge_base).splitlines() if f
    ]
    files = sorted(set(committed) | set(_dirty_files(repo)))
    if not files:
        raise NothingToReview("No changes relative to " + base + ".")

    return Target(
        kind="base",
        ref=merge_base,
        files=files,
        description="changes vs " + base,
        instruction=(
            "Review everything that would be delivered by merging this branch into\n"
            + base + " right now. That means commits since the merge base AND the\n"
            "current staged, unstaged, and untracked work -- the author may not have\n"
            "committed their latest changes.\n\n"
            "Merge base: " + merge_base + "\n"
            "Inspect with: git diff " + merge_base + " (plus untracked files below)."
        ),
    )
