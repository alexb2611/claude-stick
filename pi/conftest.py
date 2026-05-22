"""
Root conftest for the pi/ test suite.

Adds the pi/ directory itself to sys.path so that tests can do
  from state import ...
  from config import ...
etc. without qualifying the package name. This mirrors the runtime
environment on the Pi, where claude_stick_pi.py is run directly from
the pi/ directory.
"""
import sys
from pathlib import Path

# Insert pi/ at the front of sys.path so bare `import state` resolves
# to pi/state.py rather than any installed package.
_pi_dir = str(Path(__file__).parent)
if _pi_dir not in sys.path:
    sys.path.insert(0, _pi_dir)
