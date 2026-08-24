"""Scratch directory for payloads and responses.

These files contain conversation and repository content, so the directory is
0700, files are 0600, and everything is removed unless --keep is passed.
"""

import os
import shutil
import tempfile
from pathlib import Path


class Artifacts:
    def __init__(self, keep=False, parent=None):
        self.keep = keep
        self.dir = tempfile.mkdtemp(prefix="askgpt-", dir=parent)
        os.chmod(self.dir, 0o700)

    def write(self, name, text):
        path = Path(self.dir) / name
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
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
