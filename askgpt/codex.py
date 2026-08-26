"""Locate and validate the Codex CLI."""

import os
import shutil
import subprocess
import sys

from .errors import CodexNotAuthenticated, CodexNotFound

# Ordered fallbacks. The CLI ships inside the ChatGPT desktop bundle and is
# not placed on PATH by the installer.
# The build this was developed and verified against. ask-gpt depends on several
# Codex contracts that are not public API: the `login status` wording and which
# stream it uses, `exec --ignore-user-config --json -o`, the `thread.started`
# and `turn.failed` event names, the unsupported-model error text, and the
# on-disk rollout layout that `askgpt usage` reads. A different build may move
# any of them, so the version is surfaced rather than assumed.
TESTED_VERSION = "0.148.0-alpha.9"

_UNIX_CANDIDATES = (
    "/Applications/ChatGPT.app/Contents/Resources/codex",
    "~/.codex/bin/codex",
    "/usr/local/bin/codex",
    "/opt/homebrew/bin/codex",
)

# Windows ships codex inside the ChatGPT desktop app and does not put it on
# PATH, exactly as macOS does not. Measured on Windows 10 19045 with ChatGPT
# desktop installed: the binary was at the .plugin-appserver path below and
# nothing named codex was resolvable on PATH. The bin/ entry is listed first
# because it is the stable, documented-looking location; the appserver path is
# an internal detail that may well move, which is why CODEX_BIN still wins.
_WINDOWS_CANDIDATES = (
    "~/.codex/bin/codex.exe",
    "~/.codex/plugins/.plugin-appserver/codex.exe",
    "~/AppData/Local/Programs/ChatGPT/resources/codex.exe",
)

DEFAULT_CANDIDATES = _WINDOWS_CANDIDATES if sys.platform == "win32" else _UNIX_CANDIDATES


def _usable(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    if sys.platform == "win32":
        # os.access(X_OK) is meaningless on Windows: it answers True for any
        # readable file, a .txt included, so the POSIX check would accept a
        # stray text file as the Codex binary and fail later with WinError 193
        # from somewhere far less obvious. Executability is decided by the
        # extension, so PATHEXT is what to consult.
        extensions = [
            ext.strip().upper()
            for ext in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
            if ext.strip()
        ]
        return any(path.upper().endswith(ext) for ext in extensions)
    return os.access(path, os.X_OK)


def find_codex(env=None, candidates=DEFAULT_CANDIDATES) -> str:
    """Return a path to an executable Codex CLI, or raise CodexNotFound."""
    env = os.environ if env is None else env
    tried = []

    override = env.get("CODEX_BIN")
    if override:
        expanded = os.path.normpath(os.path.expanduser(override))
        tried.append(expanded)
        if _usable(expanded):
            return expanded

    searched_path = env.get("PATH", "")
    on_path = shutil.which("codex", path=searched_path)
    if on_path:
        return on_path
    # Report the PATH actually searched: when PATH itself is the problem (an
    # empty or minimal environment), "searched PATH" alone is not diagnostic.
    tried.append("codex on PATH (" + (searched_path or "<empty>") + ")")

    for candidate in candidates:
        # normpath as well as expanduser: the candidates are written with
        # forward slashes, so on Windows expanduser alone yields the mixed
        # "C:\Users\you/.codex/bin/codex.exe" that then gets printed at the
        # user during install and in every error message.
        expanded = os.path.normpath(os.path.expanduser(candidate))
        tried.append(expanded)
        if _usable(expanded):
            return expanded

    raise CodexNotFound(
        "Could not find the Codex CLI. Tried:\n  "
        + "\n  ".join(tried)
        + "\n\nInstall the ChatGPT desktop app, or set CODEX_BIN to the binary."
    )


def check_auth(codex_bin: str, timeout: int = 30) -> None:
    """Raise CodexNotAuthenticated unless Codex reports a logged-in account."""
    try:
        result = subprocess.run(
            [codex_bin, "login", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise CodexNotAuthenticated(
            "`codex login status` timed out after " + str(timeout) + "s."
        ) from None
    except OSError as error:
        # errors.py sets the contract: every user-facing failure is an
        # AskGptError. find_codex validates the path, but check_auth accepts
        # any path and the binary can vanish between the two calls -- without
        # this, the user gets a raw FileNotFoundError traceback.
        raise CodexNotFound(
            "Could not execute " + str(codex_bin) + ": " + str(error)
        ) from None

    # The real CLI prints this to STDERR with an empty stdout -- verified by
    # running `codex login status 2>/dev/null` (silent) vs `2>&1 1>/dev/null`
    # ("Logged in using ChatGPT"). Checking stdout alone reports every logged-in
    # user as logged out. The original verification used `2>&1`, which merges
    # the streams and hid this.
    reported = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "Logged in" not in reported:
        # Do not assert "not logged in": a corrupt config, a missing library, or
        # a network failure lands here too, and telling the user to run
        # `codex login` would point them at the wrong fix. Show what Codex said.
        message = (
            "Codex is not logged in, or `codex login status` failed.\n"
            "If you are logged out, run this yourself and retry:\n\n"
            "    " + str(codex_bin) + " login\n"
        )
        detail = (result.stderr or result.stdout or "").strip()
        if detail:
            message += (
                "\nCodex said (exit " + str(result.returncode) + "):\n" + detail[:500]
            )
        raise CodexNotAuthenticated(message)


def version(codex_bin, timeout=15):
    """Return the reported version string, or None if it cannot be determined."""
    try:
        result = subprocess.run(
            [codex_bin, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    reported = ((result.stdout or "") + (result.stderr or "")).strip()
    return reported.split()[-1] if reported else None


def version_warning(codex_bin):
    """A caveat when the CLI is not the build this was verified against.

    Returns None when it matches. Deliberately a warning and not a refusal: a
    newer Codex will usually work, and blocking every user on an exact match
    would age badly. But the dependencies are undocumented, so silence would
    be worse -- a mismatch shows up later as a confusing auth failure, a lost
    thread, or wrong usage figures.
    """
    found = version(codex_bin)
    if found is None:
        return "Could not determine the Codex version; expected " + TESTED_VERSION + "."
    if found == TESTED_VERSION:
        return None
    return (
        "Codex " + found + " differs from the tested " + TESTED_VERSION + ".\n"
        "ask-gpt relies on undocumented Codex behaviour (login-status wording and\n"
        "stream, --json event names, error strings, session-log layout). If auth,\n"
        "thread continuation or usage reporting misbehave, this is the first thing\n"
        "to suspect."
    )
