"""Helpers that manufacture a specific permission state for a test.

Both platforms hand out a *restrictive* directory from `tempfile.mkdtemp`, so
a protection test written the obvious way -- write into a temp dir, assert the
file is owner-only -- passes even against an implementation that does nothing
at all. It is measuring the temp directory, not the code.

Writing into a directory that grants everyone access removes that false pass:
the file is owner-only afterwards only if something actually made it so.
"""

import contextlib
import os
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"

#: Well-known SID for Everyone. Used rather than the localised group name,
#: which differs by system language and would make this silently a no-op.
_EVERYONE = "*S-1-1-0"


def permissive_dir(path):
    """Create `path` granting access far beyond its owner, and return it."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if WINDOWS:
        result = subprocess.run(
            ["icacls", str(path), "/grant", _EVERYONE + ":(OI)(CI)(F)"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError("could not open up " + str(path) + ": " + result.stderr.strip())
    else:
        os.chmod(path, 0o777)
    return path


@contextlib.contextmanager
def unreadable(path):
    """Make `path` genuinely unreadable for the duration of the block.

    `os.chmod(path, 0o000)` does not do this on Windows -- it only sets the
    read-only attribute, and the file stays perfectly readable -- so a test
    relying on chmod alone silently stops testing the failure it names.
    The denied right is RD (read-data) rather than R (generic read): R also
    withdraws READ_CONTROL, after which icacls cannot read the ACL back and
    the deny becomes permanent -- the file is left unreadable for good.
    """
    path = Path(path)
    if not WINDOWS:
        previous = path.stat().st_mode
        os.chmod(path, 0o000)
        try:
            yield path
        finally:
            os.chmod(path, previous)
        return

    sid = _current_sid()
    denied = subprocess.run(
        ["icacls", str(path), "/deny", sid + ":(RD)"], capture_output=True, text=True
    )
    if denied.returncode != 0:
        raise OSError("could not deny read on " + str(path) + ": " + denied.stderr.strip())
    try:
        yield path
    finally:
        subprocess.run(
            ["icacls", str(path), "/remove:d", sid], capture_output=True, text=True
        )


def _current_sid():
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise OSError("could not determine the current user: " + result.stderr.strip())
    return "*" + result.stdout.strip().split(",")[1].strip().strip('"')
