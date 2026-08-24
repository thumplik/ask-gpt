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

    on_path = shutil.which("codex", path=env.get("PATH", ""))
    if on_path:
        return on_path
    tried.append("codex (searched PATH)")

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
        raise CodexNotAuthenticated("`codex login status` timed out.")

    if result.returncode != 0 or "Logged in" not in result.stdout:
        raise CodexNotAuthenticated(
            "Codex is not logged in. Run this yourself, then retry:\n\n"
            "    " + codex_bin + " login\n"
        )
