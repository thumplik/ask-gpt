"""Test package.

`make test` runs `unittest discover -s tests`, which puts this directory on
sys.path, so the shared helpers import as plain `stubs` / `permissive`. Running
one module directly (`python -m unittest tests.test_state`) imports through the
package instead and would not find them, so the same directory is added here --
otherwise the two invocations disagree about whether the suite even loads.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
