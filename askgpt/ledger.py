"""Per-project record of finding dispositions.

Fixes the real annoyance of repeat reviews -- the reviewer re-reporting
findings the user already accepted -- WITHOUT giving the reviewer memory.
Each review stays a fresh, independent thread; what carries over is a
compact, user-authored ledger injected into the payload as data.

Two design constraints, both deliberate:

- The ledger lives in the STATE DIRECTORY, never in the repository. The
  persona treats a repo file claiming something is approved as a
  prompt-injection attempt, and it is right to: repository content is
  attacker-controlled. This file is user-controlled, assembled into the
  payload by the tool, which is the only trustworthy route for "the user
  accepted this" to reach the reviewer.

- The payload block frames entries as prior DISPOSITIONS, not exemptions.
  The reviewer is told to re-report if the risk materially changed. A gag
  order would rot: accepted risks stop being re-examined even as the code
  around them shifts.
"""

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


class Ambiguous(Exception):
    """A token matched more than one accepted entry."""

    def __init__(self, matches):
        super().__init__("matches " + str(len(matches)) + " entries")
        self.matches = matches


def project_slug(repo_root):
    """Stable, filesystem-safe identifier for a repository path."""
    resolved = str(Path(repo_root).resolve()) if repo_root else str(repo_root)
    digest = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    tail = Path(resolved).name or "root"
    return tail + "-" + digest


def entry_key(description):
    """Durable identity for an accepted finding.

    The ordinal (F2) is display shorthand only: it names a position in one
    review, so mutating by it deletes an unrelated entry the next time round.
    The normalised description is what actually identifies the finding.
    """
    normalised = " ".join(str(description or "").lower().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


def _ledger_file(state_dir, repo_root):
    return Path(state_dir) / "projects" / project_slug(repo_root) / "ledger.json"


def load_accepted(state_dir, repo_root):
    path = _ledger_file(state_dir, repo_root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    entries = data.get("accepted") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def _write(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"accepted": entries}, handle, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def accept(state_dir, repo_root, finding_id, reason, description=""):
    """Record a finding as an accepted risk. Re-accepting replaces the entry.

    `description` is what makes the entry mean something: F-IDs are ordinals
    within one review, so a bare "F2" denotes nothing durable -- next week's
    F2 is a different finding. The description (file:line plus the finding's
    own words) is the identity; the ID is just shorthand for the user.
    """
    if not str(description or "").strip():
        # Empty descriptions all hash alike, so they would collide into one
        # entry -- and an entry that cannot say what it is matches nothing in
        # a later review anyway. Refuse rather than store something useless.
        raise ValueError("an accepted finding needs a description to identify it")
    path = _ledger_file(state_dir, repo_root)
    key = entry_key(description)
    # Replace by KEY, never by ordinal: re-accepting a later F2 must not
    # silently delete an unrelated finding accepted as F2 weeks ago.
    entries = [e for e in load_accepted(state_dir, repo_root) if e.get("key") != key]
    entries.append(
        {
            "key": key,
            "id": finding_id,
            "reason": reason,
            "description": description,
            "accepted_at": time.strftime("%Y-%m-%d"),
        }
    )
    _write(path, entries)
    return path


def resolve(state_dir, repo_root, token):
    """Entries matching `token`, which may be a key, an ordinal, or a substring.

    Ordinals are ambiguous by construction, so this returns every match and
    lets the caller refuse rather than guessing which finding was meant.
    """
    token = str(token or "").strip()
    lowered = token.lower()
    entries = load_accepted(state_dir, repo_root)
    exact = [e for e in entries if e.get("key") == token]
    if exact:
        return exact
    by_id = [e for e in entries if str(e.get("id", "")).lower() == lowered]
    if by_id:
        return by_id
    return [e for e in entries if lowered and lowered in str(e.get("description", "")).lower()]


def unaccept(state_dir, repo_root, token):
    """Remove one entry, resolved by key, ordinal, or description substring.

    Returns True on removal, False when nothing matched, and raises Ambiguous
    when a token matches several -- deleting the wrong accepted risk silently
    is the failure this whole identity model exists to prevent.
    """
    matches = resolve(state_dir, repo_root, token)
    if not matches:
        return False
    if len(matches) > 1:
        raise Ambiguous(matches)
    entries = load_accepted(state_dir, repo_root)
    kept = [e for e in entries if e.get("key") != matches[0].get("key")]
    _write(_ledger_file(state_dir, repo_root), kept)
    return True


def format_accepted_block(entries):
    """Render the ledger for the review payload. Empty ledger, empty string."""
    if not entries:
        return ""
    lines = [
        "<ACCEPTED-RISKS>",
        "The user has previously reviewed and ACCEPTED the following findings.",
        "This is disposition data, not an exemption list. Match entries by their",
        "DESCRIPTION (the IDs are ordinals from past reviews and mean nothing in",
        "this one). Do not re-report a matching finding as-is, but DO report it",
        "again if the surrounding code or the risk has materially changed --",
        "say what changed. An entry with no description matches nothing.",
        "",
    ]
    for entry in entries:
        line = (
            "- ["
            + str(entry.get("id"))
            + "] accepted "
            + str(entry.get("accepted_at", "?"))
            + ": "
            + str(entry.get("reason", ""))
        )
        description = str(entry.get("description", "")).strip()
        if description:
            line += "\n  The accepted finding was: " + description
        lines.append(line)
    lines.append("</ACCEPTED-RISKS>")
    return "\n".join(lines)
