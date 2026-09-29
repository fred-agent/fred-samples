"""Make the application modules importable from the tests directory.

The API modules are flat (`import app`) because they run as a single-directory
service, so the directory has to be on the path explicitly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))
