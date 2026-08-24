"""Locate and validate the Codex CLI."""

import os
import shutil
import subprocess

from .errors import CodexNotAuthenticated, CodexNotFound

# Ordered fallbacks. The CLI ships inside the ChatGPT desktop bundle and is
# not placed on PATH by the installer.
DEFAULT_CANDIDATES = (
    "/Applications/ChatGPT.app/Contents/Resources/codex",
    "~/.codex/bin/codex",
    "/usr/local/bin/codex",
    "/opt/homebrew/bin/codex",
)


def _usable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_codex(env=None, candidates=DEFAULT_CANDIDATES) -> str:
    """Return a path to an executable Codex CLI, or raise CodexNotFound."""
    env = os.environ if env is None else env
    tried = []

    override = env.get("CODEX_BIN")
    if override:
        expanded = os.path.expanduser(override)
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
        expanded = os.path.expanduser(candidate)
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
