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

from . import secfs

# The session id arrives from the command line, so this is a security
# boundary: unsanitised it would be a path-traversal write primitive.
UNSAFE = re.compile(r"[^A-Za-z0-9._-]")

# Keep the archive bounded; these are review bodies, not an audit log.
MAX_ARCHIVED_RESPONSES = 50


def _session_file(state_dir, claude_session):
    # A readable name PLUS a hash of the ORIGINAL id. Lossy replacement alone
    # aliases distinct sessions -- "a/b", "a?b" and "a:b" all become "a_b" --
    # so one session could resume another's thread, the exact failure the
    # per-session design exists to prevent. The hash disambiguates them.
    raw = str(claude_session)
    readable = UNSAFE.sub("_", raw) or "unnamed"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return Path(state_dir) / "threads" / (readable + "-" + digest + ".json")


def save_thread(state_dir, claude_session, thread_id):
    path = _session_file(state_dir, claude_session)
    # From the root down: an exposed state root left by an earlier build is
    # repaired too, not just the directory being written into.
    secfs.secure_tree(state_dir, "threads")

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"thread_id": thread_id}, handle, indent=2)
    secfs.restrict_file(tmp)
    os.replace(tmp, path)          # atomic within a filesystem
    return path


def _legacy_session_file(state_dir, claude_session):
    """The pre-hash filename. Threads written before session ids were hashed
    live here; without a fallback an upgrade silently orphans every existing
    conversation."""
    name = UNSAFE.sub("_", str(claude_session)) or "unnamed"
    return Path(state_dir) / "threads" / (name + ".json")


def load_thread(state_dir, claude_session):
    path = _session_file(state_dir, claude_session)
    if not path.is_file():
        legacy = _legacy_session_file(state_dir, claude_session)
        if legacy.is_file():
            # Migrate on read: move it to the hashed name so the aliasing fix
            # applies from here on, and the conversation survives the upgrade.
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(legacy), str(path))
            except OSError:
                path = legacy   # read-only state dir: still readable in place
        else:
            return None
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
    # Fold the payload into the identity. Two runs in one thread can produce
    # identical response TEXT from different code or payloads; keying on the
    # response alone made the second overwrite the first, destroying the audit
    # record of which payload influenced which response.
    identity = hashlib.sha256(raw + b"\x00" + (payload or "").encode("utf-8")).hexdigest()[:12]
    digest = hashlib.sha256(raw).hexdigest()[:12]

    directory = secfs.secure_tree(state_dir, "responses")
    name = (
        UNSAFE.sub("_", str(claude_session or "unkeyed"))
        + "-"
        + UNSAFE.sub("_", str(thread_id or "nothread"))
        + "-"
        + identity
        + ".md"
    )
    path = directory / name

    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    secfs.restrict_file(path)

    if payload is not None:
        # The payload snapshot makes dispositions auditable: once the ledger
        # changes, this is the only record of which accepted-risks block
        # actually influenced a given review.
        side = path.with_suffix(".payload.md")
        fd = os.open(str(side), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload.encode("utf-8"))
        secfs.restrict_file(side)

    _prune(directory)
    return path, digest, len(raw)


def _prune(directory, keep=MAX_ARCHIVED_RESPONSES):
    try:
        # Count RESPONSES only. Payload sidecars share the .md suffix, so
        # globbing blindly halves the retention window and can delete one
        # half of a pair, leaving a response whose payload is gone.
        files = sorted(
            (p for p in directory.glob("*.md") if not p.name.endswith(".payload.md")),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return
    for stale in files[keep:]:
        for target in (stale, stale.with_suffix(".payload.md")):
            try:
                target.unlink()
            except OSError:
                pass
