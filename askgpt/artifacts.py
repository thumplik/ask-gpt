"""Scratch directory for payloads and responses.

These files contain conversation and repository content, so the directory is
0700, files are 0600, and everything is removed unless --keep is passed.
"""

import os
import shutil
import tempfile
from pathlib import Path

from . import secfs


class Artifacts:
    def __init__(self, keep=False, parent=None):
        self.keep = keep
        self.dir = tempfile.mkdtemp(prefix="askgpt-", dir=parent)
        # mkdtemp already yields an owner-only directory on both platforms
        # (measured: a real restrictive ACL on Windows, 0700 on POSIX). This
        # re-asserts it so the guarantee does not rest on that implementation
        # detail, and so `parent` overrides inherit nothing permissive.
        secfs.secure_dir(self.dir)

    def write(self, name, text):
        path = Path(self.dir) / name
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        secfs.restrict_file(path)
        return path

    def path(self, name):
        return Path(self.dir) / name

    def cleanup(self):
        if not self.keep:
            shutil.rmtree(self.dir, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.cleanup()
        return False
