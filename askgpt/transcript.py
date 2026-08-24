"""Claude Code JSONL transcripts -> filtered dialogue markdown.

Dialogue is preserved word for word. Tool payloads are omitted, except
failed ones: when a session was spent chasing an error, that output is the
most useful evidence a reviewer can have.
"""

import json
import re
from pathlib import Path

# A Claude session id is a bare identifier. Anything with a separator is either
# a mistake or a traversal attempt (../../secret selects a file outside the
# project dir, whose contents would then be packed into the outbound payload).
_SAFE_SESSION = re.compile(r"^[A-Za-z0-9._-]+$")

from .errors import TranscriptNotFound

REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)

DEFAULT_BUDGET = 60_000
DEFAULT_FAIL_BUDGET = 12_000
DEFAULT_FAIL_ITEM_CAP = 3_000


def load_jsonl(path):
    """Parse a JSONL file, skipping malformed lines rather than aborting."""
    records = []
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def project_dir_for(cwd, config_dir):
    """Map a working directory to its Claude transcript directory."""
    slug = str(Path(cwd).resolve()).replace("/", "-")
    return Path(config_dir) / "projects" / slug


def resolve_session(project_dir, session_id):
    """Resolve a transcript path.

    An explicit id is authoritative. The mtime fallback exists because no
    environment variable exposes the Claude session id, but it picks wrongly
    when two sessions share a directory -- so callers should pass the id.
    """
    project_dir = Path(project_dir)
    if session_id:
        if not _SAFE_SESSION.match(str(session_id)):
            raise TranscriptNotFound(
                "Invalid session id: " + repr(session_id) + ". Expected a bare "
                "identifier (letters, digits, dot, dash, underscore)."
            )
        candidate = project_dir / (session_id + ".jsonl")
        # Belt and braces: confirm the resolved path stays inside project_dir.
        if project_dir.resolve() not in candidate.resolve().parents:
            raise TranscriptNotFound("Session id escapes the project directory.")
        if candidate.is_file():
            return candidate
        raise TranscriptNotFound("No transcript at " + str(candidate))

    transcripts = sorted(
        project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not transcripts:
        raise TranscriptNotFound("No transcripts found in " + str(project_dir))
    return transcripts[0]


def _clean(text):
    return REMINDER_RE.sub("", text).strip()


def _render(record, fail_item_cap):
    """Return (markdown, failed_chars) for one record, or (None, 0) to skip."""
    kind = record.get("type")
    if kind not in ("user", "assistant"):
        return None, 0

    # `or {}` not `.get("message", {})`: a present-but-null message is a real
    # shape in partial transcripts, and .get's default only covers an ABSENT key.
    # Without this one record aborts packing for the whole session.
    content = (record.get("message") or {}).get("content")
    parts = []
    failed_chars = 0

    if isinstance(content, str):
        cleaned = _clean(content)
        if cleaned:
            parts.append(cleaned)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                cleaned = _clean(block.get("text", ""))
                if cleaned:
                    parts.append(cleaned)
            elif btype == "tool_use":
                parts.append("[tool: " + str(block.get("name", "unknown")) + "]")
            elif btype == "tool_result" and block.get("is_error") is True:
                body = block.get("content", "")
                if isinstance(body, list):
                    body = "\n".join(
                        b.get("text", "") for b in body if isinstance(b, dict)
                    )
                body = str(body)
                if len(body) > fail_item_cap:
                    body = body[:fail_item_cap] + "\n... [truncated]"
                failed_chars += len(body)
                parts.append("[tool failed]\n```\n" + body + "\n```")

    if not parts:
        return None, 0
    return "## " + kind + "\n" + "\n".join(parts), failed_chars


def pack(
    records,
    budget=DEFAULT_BUDGET,
    fail_budget=DEFAULT_FAIL_BUDGET,
    fail_item_cap=DEFAULT_FAIL_ITEM_CAP,
    drop_last_turns=0,
):
    """Render records to markdown, newest-first within `budget` characters."""
    # Render first, THEN drop. Slicing raw records removes whatever happens to be
    # last -- an attachment, a queue-operation, a tool result -- rather than the
    # dialogue turn that invoked this command.
    turns = []
    for record in records:
        rendered, failed_chars = _render(record, fail_item_cap)
        if rendered is not None:
            turns.append((rendered, failed_chars))

    if drop_last_turns:
        if drop_last_turns >= len(turns):
            turns = []
        else:
            turns = turns[:-drop_last_turns]

    chosen = []
    used = 0
    failed_used = 0
    truncated = False

    for rendered, failed_chars in reversed(turns):
        # `continue`, not `break`: one oversized failure should not hide older
        # unrelated turns. (`break` below IS right for the main budget -- turns
        # are visited newest-first, so once one does not fit, none older will.)
        if failed_chars and failed_used + failed_chars > fail_budget:
            continue
        if used + len(rendered) > budget:
            # `continue`, not `break`. Turn sizes vary wildly, so one oversized
            # recent turn does NOT imply older turns cannot fit -- breaking here
            # discarded every older turn and could return nothing but the
            # omission marker. (An earlier comment claimed the opposite.)
            truncated = True
            continue
        chosen.append(rendered)
        used += len(rendered)
        failed_used += failed_chars

    chosen.reverse()
    body = "\n\n---\n\n".join(chosen)
    if truncated:
        body = "_[earlier turns omitted to fit the size budget]_\n\n" + body
    return body
