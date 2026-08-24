"""Warn about sensitive files inside the tree Codex can read.

read-only means Codex cannot MODIFY the repository. It does not mean Codex
cannot READ it. Ignore status is irrelevant here, so this walks the whole
tree rather than the review target.
"""

import fnmatch
import os
from pathlib import Path

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


def format_warning(hits):
    """Human-readable notice printed before dispatch."""
    lines = [
        "Codex will be able to read these files (read-only prevents writes, not reads):"
    ]
    for hit in hits:
        lines.append("  " + str(hit))
    lines.append("")
    lines.append("Move or remove anything that must not leave this machine.")
    return "\n".join(lines)
