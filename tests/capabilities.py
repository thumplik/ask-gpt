"""Runtime probes for platform privileges a test may need.

Skips must be *visible* and explained. A test silently absent reads as a test
that passed, which is how a gap becomes a false claim of support -- so each
probe here comes with a reason naming the privilege and how to get it.
"""

import os
import sys
import tempfile
from pathlib import Path

WINDOWS = sys.platform == "win32"

SYMLINK_REASON = (
    "creating symlinks needs SeCreateSymbolicLinkPrivilege on Windows: "
    "enable Developer Mode (Settings > System > For developers) or run elevated"
)


def can_symlink():
    """True when this process may create a symlink."""
    if not WINDOWS:
        return True
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "target"
        target.write_text("x")
        try:
            os.symlink(target, Path(tmp) / "link")
            return True
        except (OSError, NotImplementedError):
            return False
