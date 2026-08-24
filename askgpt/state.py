"""Persist the Codex thread id for each Claude session.

Keyed by Claude session id so a follow-up resumes the conversation this session
started -- never `--last`, which can attach to an unrelated Codex run.

One file per session, written via atomic replace. A single shared map loses
updates when two Claude windows save concurrently: both read the old map, both
write their own copy, one mapping vanishes. The design explicitly anticipates
concurrent windows, so that race is reachable. Separate files make it
structurally impossible without any locking.
"""

import json
import os
import re
import tempfile
from pathlib import Path

# The session id arrives from the command line, so this is a security
# boundary: unsanitised it would be a path-traversal write primitive.
UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


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
