"""Ensures backend/ is on sys.path so test modules can `import app...` and
`import ingest` regardless of the directory pytest is invoked from.
"""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
