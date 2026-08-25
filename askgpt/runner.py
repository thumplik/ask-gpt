"""Invoke `codex exec` and interpret its output."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .errors import AskGptError, ModelUnavailable, QuotaExhausted

MODEL = "gpt-5.6-sol"

# Tried in order when the pinned model is unavailable. Retrying here does NOT
# violate the never-retry rule: an unavailable slug is rejected with HTTP 400
# BEFORE inference (verified), so a model retry costs no quota. Every other
# failure -- transport, auth, quota -- still stops at the first attempt.
FALLBACK_MODELS = ("gpt-5.6-terra",)

# Emitted verbatim by the API when a slug is not available to the account.
UNSUPPORTED_MARKER = "is not supported when using Codex with a ChatGPT account"

# Quota wording is NOT verified -- reproducing it means exhausting a real plan.
# These are deliberately narrow phrases rather than bare words: the earlier set
# included "quota" and "429", which appear 40 times in this repository's own
# text, so ANY review of it was misclassified as a quota failure and the real
# review discarded. Markers are only ever consulted on a non-zero exit (below).
QUOTA_MARKERS = (
    "usage limit reached",
    "rate limit exceeded",
    "too many requests",
    "quota exceeded",
    "insufficient_quota",
)

DEFAULT_TIMEOUT = 1800

# Event types that describe a failure OF the run. Verified against the CLI:
# under --json a rejected model arrives as {"type":"error"} plus
# {"type":"turn.failed"} on STDOUT, with stderr empty.
ERROR_EVENT_TYPES = ("error", "turn.failed", "stream.error")


def error_text(stdout, stderr):
    """Return only text describing failures of the run, never content from it.

    Codex echoes the material under review into agent_message and
    command_execution events. Substring-scanning the whole stream therefore
    reads the reviewed code as if it described the run: a review of anything
    discussing rate limits was reported as a quota failure. Structured
    extraction is the fix -- match on event type, not on words.
    """
    parts = []
    if stderr:
        parts.append(stderr)
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue  # not an event, so not evidence about the run
        if not isinstance(event, dict):
            continue
        if event.get("type") in ERROR_EVENT_TYPES:
            parts.append(json.dumps(event))
            continue
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "error":
            parts.append(json.dumps(item))
    return "\n".join(parts)


@dataclass
class Result:
    text: str
    thread_id: str
    returncode: int
    stderr: str
    model: str = MODEL          # which model actually answered
    fell_back: bool = False     # True when it was not the one requested


def model_chain(model=MODEL, fallback=True):
    """The requested model, then the fallbacks, without duplicates."""
    chain = [model]
    if fallback:
        chain += [m for m in FALLBACK_MODELS if m != model]
    return chain


def build_argv(
    codex_bin,
    out_path,
    model=MODEL,
    resume_thread=None,
    skip_git_repo_check=True,
):
    """Assemble the argv.

    Order matters: `resume` accepts no --sandbox of its own, so every exec-level
    flag must appear before the subcommand.
    """
    argv = [
        codex_bin,
        "exec",
        "-m",
        model,
        "-s",
        "read-only",
        "--ignore-user-config",
        "--json",
        "-o",
        str(out_path),
    ]
    if skip_git_repo_check:
        argv.append("--skip-git-repo-check")
    if resume_thread:
        argv += ["resume", resume_thread]
    argv.append("-")
    return argv


def parse_thread_id(stream_text):
    """Scan JSONL events for thread.started. Never assume it is first."""
    for line in stream_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
            if thread_id:
                return thread_id
    return None


def run_once(
    codex_bin,
    payload,
    cwd,
    out_path,
    model=MODEL,
    resume_thread=None,
    timeout=DEFAULT_TIMEOUT,
):
    """One attempt with one model. Never retries anything."""
    argv = build_argv(codex_bin, out_path, model=model, resume_thread=resume_thread)
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # `from None`: callers print str(e); a chained TimeoutExpired traceback
        # is noise. Same convention as codex.check_auth.
        raise AskGptError(
            "Codex timed out after " + str(timeout) + "s. Not retrying."
        ) from None
    except OSError as error:
        # Keeps every failure inside the AskGptError contract, as errors.py states.
        raise AskGptError("Could not execute " + str(codex_bin) + ": " + str(error)) from None

    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    out_file = Path(out_path)
    text = out_file.read_text(encoding="utf-8").strip() if out_file.is_file() else ""

    # Failure classification happens ONLY on a non-zero exit. Codex echoes the
    # material it is reviewing into its event stream, so scanning a successful
    # run's output for error phrases misreads the reviewed content as an error
    # about the run -- a review of any code discussing rate limits was reported
    # as a quota failure and thrown away.
    if proc.returncode != 0:
        diagnostics = error_text(proc.stdout, proc.stderr)
        lowered = diagnostics.lower()
        if any(marker in lowered for marker in QUOTA_MARKERS):
            raise QuotaExhausted(
                "Codex reports a usage or rate limit on this ChatGPT plan.\n"
                "Not retrying.\n\n" + diagnostics.strip()[:2000]
            )

        if UNSUPPORTED_MARKER in diagnostics:
            raise ModelUnavailable(
                "Model '" + model + "' is not available on this ChatGPT account.\n"
                "Re-run with --model <slug> to choose a different one, or\n"
                "--no-fallback to fail immediately rather than trying a weaker model."
            )

        # Any non-zero exit is a failure, even when out.md holds partial or stale
        # content. Returning that text as a result would report an auth, quota, or
        # transport failure as a successful review.
        # Use the extracted diagnostics, not stderr alone: real Codex errors
        # arrive as structured events on STDOUT with stderr empty, so reporting
        # stderr gave the user an exit code and nothing else.
        message = (
            "Codex exited " + str(proc.returncode) + ". Not retrying (retries burn "
            "subscription quota).\n\n"
            + (diagnostics.strip() or "(no diagnostics)")
        )
        if text:
            message += "\n\nPartial output before the failure:\n" + text
        raise AskGptError(message)

    if not text:
        # Exit 0 with an empty or absent -o file is not a successful review.
        # Printing nothing and returning 0 tells the user their code is clean.
        raise AskGptError(
            "Codex exited 0 but produced no response.\n"
            "Not retrying (retries burn subscription quota).\n\n"
            + (error_text(proc.stdout, proc.stderr).strip()[:1000] or "(no diagnostics)")
        )

    return Result(
        text=text,
        thread_id=parse_thread_id(proc.stdout or ""),
        returncode=proc.returncode,
        stderr=proc.stderr or "",
        model=model,
        fell_back=False,
    )


def run(
    codex_bin,
    payload,
    cwd,
    out_path,
    model=MODEL,
    resume_thread=None,
    timeout=DEFAULT_TIMEOUT,
    fallback=True,
):
    """Try the requested model, then fallbacks if it is unavailable.

    ONLY ModelUnavailable advances the chain. A quota, transport, or auth
    failure stops immediately -- those retries would cost real quota, and
    repeating them would not help.
    """
    chain = model_chain(model, fallback=fallback)
    last = None
    for index, candidate in enumerate(chain):
        try:
            result = run_once(
                codex_bin,
                payload,
                cwd,
                out_path,
                model=candidate,
                resume_thread=resume_thread,
                timeout=timeout,
            )
        except ModelUnavailable as error:
            last = error
            continue
        result.fell_back = index > 0
        return result
    raise last
