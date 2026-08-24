"""Warn about sensitive files inside the tree Codex can read.

read-only means Codex cannot MODIFY the repository. It does not mean Codex
cannot READ it. Ignore status is irrelevant here, so this walks the whole
tree rather than the review target.
"""

import fnmatch
import os
from pathlib import Path

from .secrets import scan as scan_text

SENSITIVE_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
    "id_ed25519*",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "credentials.json",
    "service-account*.json",
    ".npmrc",
    ".netrc",
    ".pypirc",
)

# Directories whose entire contents are sensitive. Matched by directory name,
# because the interesting file is `credentials`, which no filename glob catches.
SENSITIVE_DIRS = (".aws", ".gnupg", ".ssh")

SKIP_DIRS = (".git",)

DEFAULT_LIMIT = 50

# Content scanning is bounded so it can sit in front of every review. These
# caps trade completeness for a predictable cost; the filename pass is the
# cheap first line and this is the second.
MAX_CONTENT_BYTES = 256 * 1024
MAX_CONTENT_FILES = 2000
SKIP_CONTENT_DIRS = (
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    "target", ".mypy_cache", ".pytest_cache", "vendor", ".next",
)
# Scanning our own fixtures and detector would flag every run.
SKIP_CONTENT_NAMES = ("secrets.py", "test_secrets.py")


def scan_tree(root, skip_dirs=SKIP_DIRS, limit=DEFAULT_LIMIT):
    """Return sorted repo-relative paths of sensitive-looking files.

    Filename matching only. Content scanning of a whole tree is too slow to
    sit in front of every review, and the filename is the actionable signal.
    """
    root = Path(root)
    hits = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]

        for dirname in list(dirnames):
            if dirname in SENSITIVE_DIRS:
                hits.append(Path(dirpath, dirname).relative_to(root))
                dirnames.remove(dirname)   # reported as a unit; do not descend

        for filename in filenames:
            for pattern in SENSITIVE_GLOBS:
                if fnmatch.fnmatch(filename, pattern):
                    hits.append(Path(dirpath, filename).relative_to(root))
                    break

    hits.sort()
    return hits[:limit]


def external_symlinks(root, skip_dirs=SKIP_CONTENT_DIRS, limit=DEFAULT_LIMIT):
    """Symlinks whose target resolves OUTSIDE the repository.

    Content scanning skips symlinks, but Codex follows them: `repo/config ->
    ~/.aws/credentials` reads real credentials while matching no pattern. Such
    links are a direct disclosure route and are reported, not skipped.
    """
    root = Path(root).resolve()
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for name in list(dirnames) + filenames:
            path = Path(dirpath, name)
            if not path.is_symlink():
                continue
            try:
                target = path.resolve()
            except OSError:
                continue
            if root not in target.parents and target != root:
                hits.append((path.relative_to(root), target))
                if len(hits) >= limit:
                    return hits
    return hits


def coverage_is_partial(root, skip_dirs=SKIP_CONTENT_DIRS):
    """True when scan_contents would stop early (too many or too-large files),
    so the caller can say the scan was incomplete rather than imply it was clean."""
    root = Path(root)
    scannable = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for filename in filenames:
            path = Path(dirpath, filename)
            try:
                if path.is_symlink():
                    continue
                if path.stat().st_size > MAX_CONTENT_BYTES:
                    return True
            except OSError:
                continue
            scannable += 1
            if scannable > MAX_CONTENT_FILES:
                return True
    return False


def format_external_symlinks(hits):
    lines = ["Symlinks that let Codex read OUTSIDE this repository:"]
    for link, target in hits:
        lines.append("  " + str(link) + " -> " + str(target))
    lines.append("")
    lines.append("Codex follows these. Remove them or pass --allow-sensitive-files.")
    return "\n".join(lines)


def scan_contents(root, limit=DEFAULT_LIMIT):
    """Find secret-shaped material INSIDE readable files.

    The filename pass catches `.env` and `*.pem`. It cannot catch a live key
    pasted into `config.py`, which Codex reads just as happily -- so filename
    matching alone left the most likely real leak invisible while a fake
    credential mentioned in conversation would block the run. This closes that
    asymmetry.

    Returns [(relative_path, Finding)], bounded in files, size and results.
    """
    root = Path(root)
    hits = []
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_CONTENT_DIRS]
        for filename in filenames:
            if len(hits) >= limit or scanned >= MAX_CONTENT_FILES:
                return hits
            if filename in SKIP_CONTENT_NAMES:
                continue
            path = Path(dirpath, filename)
            try:
                if path.is_symlink() or path.stat().st_size > MAX_CONTENT_BYTES:
                    continue
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # binary or unreadable: nothing to match
            scanned += 1
            for finding in scan_text(text):
                hits.append((path.relative_to(root), finding))
                if len(hits) >= limit:
                    return hits
    return hits


def format_content_warning(hits):
    """Notice for secret-shaped content found inside readable files."""
    lines = [
        "Secret-shaped content found in files Codex would be able to read:"
    ]
    for path, finding in hits:
        lines.append(
            "  " + str(path) + ":" + str(finding.line)
            + "  " + finding.name + " (" + finding.excerpt + ")"
        )
    lines.append("")
    lines.append("Nothing was sent. Remove or move these, or pass")
    lines.append("--allow-sensitive-files if they are not real credentials.")
    return "\n".join(lines)


def format_warning(hits):
    """Human-readable notice printed before dispatch."""
    lines = [
        "Codex will be able to read these files (read-only prevents writes, not reads):"
    ]
    for hit in hits:
        lines.append("  " + str(hit))
    lines.append("")
    lines.append("Move or remove anything that must not leave this machine.")
    lines.append("")
    lines.append(
        "Note: this scan covers the repository. Codex's read-only sandbox is NOT"
    )
    lines.append(
        "confined to it -- reads can reach your home directory and beyond."
    )
    return "\n".join(lines)
