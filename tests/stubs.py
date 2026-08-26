"""Executable Codex stubs that behave identically on POSIX and Windows.

These stubs must control stdout, stderr and exit status *independently*. That
is not incidental: `codex login status` writes its result to stderr, the check
originally read stdout, and every logged-in user was reported as logged out. A
stub that blurred the two streams would stop the suite catching that again.

A `#!/bin/sh` script is not executable on Windows -- CreateProcess rejects it
with WinError 193, which silently disabled 21 tests here. So the body is
written in Python, which is always present because it is running the tests,
and on Windows paired with a .cmd launcher. Measured: subprocess executes a
.cmd from a list argv with both streams intact and the exit code preserved.
"""

import os
import stat
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"


def write_program(path, source):
    """Write `source` as a program `path` that subprocess can execute directly."""
    path = Path(path)
    if not WINDOWS:
        path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
        return str(path)

    script = path.with_suffix(".py")
    script.write_text(source, encoding="utf-8")
    launcher = path.with_suffix(".cmd")
    # exit /b %ERRORLEVEL% is required: without it the launcher reports its own
    # status and every non-zero exit from the stub would read as success.
    launcher.write_text(
        "@echo off\r\n"
        '"' + sys.executable + '" "' + str(script) + '" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n",
        encoding="utf-8",
    )
    return str(launcher)


def write_stub(path, stdout="", stderr="", code=0, delay=0.0):
    """A stub emitting exactly `stdout`/`stderr`, exiting `code` after `delay`."""
    source = "import sys, time\n"
    if delay:
        source += "time.sleep(" + repr(float(delay)) + ")\n"
    if stdout:
        source += "sys.stdout.write(" + repr(stdout + "\n") + ")\n"
    if stderr:
        source += "sys.stderr.write(" + repr(stderr + "\n") + ")\n"
    source += "sys.stdout.flush()\nsys.stderr.flush()\n"
    source += "sys.exit(" + str(int(code)) + ")\n"
    return write_program(path, source)


def make_exe(directory, name="codex"):
    """A stub that does nothing and succeeds -- for path-resolution tests."""
    return Path(write_stub(Path(directory) / name))


def make_executable(path):
    """Make an already-written `#!` script runnable, and say how to invoke it.

    Returns the path to *invoke*, which on Windows is a generated .cmd
    launcher rather than the script itself -- so callers must use the return
    value and not the path they wrote.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if not WINDOWS:
        path.chmod(0o755)
        return str(path)

    first, _, rest = text.partition("\n")
    if not first.startswith("#!"):
        return write_program(path, text)
    if "python" not in first:
        raise ValueError("only python stubs can be run on Windows, got: " + first)
    return write_program(path, rest)
