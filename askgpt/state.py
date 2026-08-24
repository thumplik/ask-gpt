"""Persist the Codex thread id for each Claude session.

Keyed by Claude session id so a follow-up resumes the conversation this session
started -- never `--last`, which can attach to an unrelated Codex run.

One file per session, written via atomic replace. A single shared map loses
updates when two Claude windows save concurrently: both read the old map, both
write their own copy, one mapping vanishes. The design explicitly anticipates
concurrent windows, so that race is reachable. Separate files make it
structurally impossible without any locking.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

# The session id arrives from the command line, so this is a security
# boundary: unsanitised it would be a path-traversal write primitive.
UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# Keep the archive bounded; these are review bodies, not an audit log.
MAX_ARCHIVED_RESPONSES = 50


def _session_file(state_dir, claude_session):
    name = UNSAFE.sub("_", str(claude_session)) or "unnamed"
    return Path(state_dir) / "threads" / (name + ".json")


def save_thread(state_dir, claude_session, thread_id):
    path = _session_file(state_dir, claude_session)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"thread_id": thread_id}, handle, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)          # atomic within a filesystem
    return path


def load_thread(state_dir, claude_session):
    path = _session_file(state_dir, claude_session)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    return data.get("thread_id") if isinstance(data, dict) else None


def archive_response(state_dir, claude_session, thread_id, text, payload=None):
    """Persist the complete response and return (path, sha, byte_count).

    The relay-verbatim contract lived only as prose, and it failed the second
    time it was exercised: a response was truncated in transit and the
    remainder presented as the whole review. Nothing detected it. Writing the
    full text somewhere stable, with a hash and a length, makes that class of
    failure checkable instead of invisible.
    """
    raw = text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:12]

    directory = Path(state_dir) / "responses"
    directory.mkdir(parents=True, exist_ok=True)
    name = (
        UNSAFE.sub("_", str(claude_session or "unkeyed"))
        + "-"
        + UNSAFE.sub("_", str(thread_id or "nothread"))
        + "-"
        + digest
        + ".md"
    )
    path = directory / name

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)

    if payload is not None:
        # The payload snapshot makes dispositions auditable: once the ledger
        # changes, this is the only record of which accepted-risks block
        # actually influenced a given review.
        side = path.with_suffix(".payload.md")
        fd = os.open(str(side), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload.encode("utf-8"))

    _prune(directory)
    _prune(directory.parent / "responses", keep=MAX_ARCHIVED_RESPONSES)
    return path, digest, len(raw)


def _prune(directory, keep=MAX_ARCHIVED_RESPONSES):
    try:
        files = sorted(
            directory.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True
        )
    except OSError:
        return
    for stale in files[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass
