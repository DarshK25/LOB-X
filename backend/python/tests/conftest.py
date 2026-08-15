"""conftest.py — make 'backend/python' visible to pytest so 'import lobx' works."""
import sys
from pathlib import Path

# Insert backend/python at the front of sys.path so all test files
# can do `from lobx.xxx import yyy` without needing pip install -e .
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
