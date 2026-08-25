"""Platform-specific file locking and owner-only permissions.

Two guarantees this project depends on, neither of which is portable:

*Locking.* accept/unaccept read the ledger, change it, and write it back.
Without an exclusive lock across the whole cycle, concurrent writers lose each
other's entries -- 30 concurrent accepts collapsed to 5 before locking existed.

*Owner-only permissions.* Payloads, response archives and thread state hold
conversation and repository content. On POSIX a 0600 mode protects them
wherever they live. On Windows the POSIX bits are ignored entirely, so
protection has to be written as a real ACL; see `restrict_file`.

Both are gathered here so that a platform is either fully supported or refused
as a unit, rather than silently providing one guarantee and not the other.
"""

import os
import subprocess
import sys

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only off-Unix
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised only on Unix
    msvcrt = None

WINDOWS = sys.platform == "win32"

#: Locking is byte-range on Windows; the region is arbitrary but must be
#: identical in every process for the locks to actually contend.
_LOCK_BYTES = 1


def available():
    """True when this platform can provide locking *and* owner-only files."""
    if WINDOWS:
        return msvcrt is not None
    return fcntl is not None


def lock_exclusive(handle):
    """Take an exclusive lock on an open file descriptor, blocking until held."""
    if not WINDOWS:
        fcntl.flock(handle, fcntl.LOCK_EX)
        return

    # msvcrt.locking locks a byte range from the current position, and LK_LOCK
    # gives up after ~10 seconds. flock blocks indefinitely, so retry to match
    # it: a caller that waits is correct, a caller that fails loses entries.
    os.lseek(handle, 0, os.SEEK_SET)
    while True:
        try:
            msvcrt.locking(handle, msvcrt.LK_LOCK, _LOCK_BYTES)
            return
        except OSError:
            continue


def unlock(handle):
    """Release a lock taken by `lock_exclusive`."""
    if not WINDOWS:
        fcntl.flock(handle, fcntl.LOCK_UN)
        return
    os.lseek(handle, 0, os.SEEK_SET)
    msvcrt.locking(handle, msvcrt.LK_UNLCK, _LOCK_BYTES)


# --- owner-only permissions -------------------------------------------------
#
# Measured on Windows 10 19045, because the behaviour is not what the POSIX
# calls suggest:
#
#   os.mkdir(mode=0o700)            -> a real restrictive ACL. Works.
#   os.open(..., 0o600)             -> mode ignored; the file inherits.
#   tempfile.mkstemp() + os.chmod   -> mode ignored; the file inherits.
#
# So on Windows a file is exactly as protected as its directory, and nothing
# more. In a permissive parent, both file recipes above produced files granting
# NT AUTHORITY\INTERACTIVE -- any logged-on user -- Modify. Directories are
# therefore secured explicitly, and files are restricted individually too
# rather than trusting whatever they inherited.

#: Principals that may appear on a protected object without it being a leak.
#: SYSTEM and Administrators are not excludable in practice; a local admin can
#: take ownership regardless, so denying them buys nothing.
_ALLOWED = {
    r"NT AUTHORITY\SYSTEM",
    r"BUILTIN\ADMINISTRATORS",
    "OWNER RIGHTS",
    "CREATOR OWNER",
}

_identity_cache = {}


def _run(argv):
    """Run a command, keeping stdout and stderr separate.

    Deliberately not merged: `2>&1` hid a real bug in this project for its
    entire history, and permission checks are exactly where a swallowed error
    would be most costly.
    """
    return subprocess.run(argv, capture_output=True, text=True)


def _whoami(field):
    if field not in _identity_cache:
        result = _run(["whoami", "/user", "/fo", "csv", "/nh"])
        if result.returncode != 0:
            raise OSError("could not determine the current user: " + result.stderr.strip())
        name, sid = [part.strip().strip('"') for part in result.stdout.strip().split(",")[:2]]
        _identity_cache["name"] = name
        _identity_cache["sid"] = sid
    return _identity_cache[field]


def _principals(path):
    """Every principal named in `path`'s ACL, upper-cased."""
    result = _run(["icacls", str(path)])
    if result.returncode != 0:
        raise OSError("could not read the ACL of " + str(path) + ": " + result.stderr.strip())

    found = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("Successfully") or line.startswith("Failed"):
            continue
        # The first line repeats the path, which may itself contain colons
        # (C:\...), so strip it before splitting the principal off.
        if str(path) in line:
            line = line.replace(str(path), "", 1).strip()
        if ":" not in line:
            continue
        found.append(line.rsplit(":", 1)[0].strip())
    return found


def _restrict_windows(path, directory):
    """Reduce `path`'s ACL to its owner alone.

    `/inheritance:r` drops *inherited* entries and `/grant:r` replaces the
    grant for the principal named -- neither touches an explicit entry for
    somebody else. A directory that an earlier build left with an explicit
    "Everyone" grant therefore survives both, so the surviving principals are
    enumerated and removed individually.
    """
    # A bare SID must be given to icacls as *S-1-5-...; without the star it is
    # read as an account name and fails with "No mapping between account names
    # and security IDs was done."
    rights = ":(OI)(CI)(F)" if directory else ":(F)"
    grant = "*" + _whoami("sid") + rights
    result = _run(["icacls", str(path), "/inheritance:r", "/grant:r", grant])
    if result.returncode != 0:
        raise OSError("could not restrict " + str(path) + ": " + (result.stderr or result.stdout).strip())

    me = _whoami("name").upper()
    for principal in _principals(path):
        if principal.upper() == me or principal.upper() in _ALLOWED:
            continue
        removed = _run(["icacls", str(path), "/remove:g", principal])
        if removed.returncode != 0:
            raise OSError(
                "could not drop "
                + principal
                + " from "
                + str(path)
                + ": "
                + (removed.stderr or removed.stdout).strip()
            )


def restrict_file(path):
    """Make `path` readable and writable by its owner alone."""
    if WINDOWS:
        _restrict_windows(path, directory=False)
    else:
        os.chmod(path, 0o600)
    return path


def secure_dir(path):
    """Create `path` and every missing parent, owner-accessible only.

    `Path.mkdir(parents=True, mode=...)` applies the mode to the final
    component only -- intermediates are created with the default permissions.
    Since the state root and the per-project directory are both created this
    way, each level is walked explicitly.

    Restricting an already-existing final directory is deliberate: state
    directories created by an earlier build inherited whatever their parent
    allowed, and this repairs them in place.
    """
    from pathlib import Path as _Path

    path = _Path(path)
    missing = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent

    for directory in reversed(missing):
        directory.mkdir(mode=0o700, exist_ok=True)
        if WINDOWS:
            _restrict_windows(directory, directory=True)

    if not missing:
        if WINDOWS:
            _restrict_windows(path, directory=True)
        else:
            os.chmod(path, 0o700)
    return path


def is_owner_only(path):
    """True when nobody but the owner can read `path`.

    This inspects the *resolved* protection, not the call that was supposed to
    apply it. On Windows a `chmod` assertion passes against a world-readable
    file, so tests that assert the call rather than the result prove nothing.
    """
    if not WINDOWS:
        return os.stat(path).st_mode & 0o077 == 0

    me = _whoami("name").upper()
    for principal in _principals(path):
        principal = principal.upper()
        if principal and principal != me and principal not in _ALLOWED:
            return False
    return True
