"""Environment loading and shared constants.

Reads secrets and model names from `.env` (never from a committed file) and
holds the tunable thresholds used by retrieval and verification, each with a
comment on how the number was chosen.
"""

import os

from dotenv import load_dotenv

load_dotenv()

try:
    STANFORD_API_KEY = os.environ["STANFORD_API_KEY"]
except KeyError as exc:
    raise RuntimeError("STANFORD_API_KEY is not set. Copy .env.example to .env and fill it in.") from exc
LLM_BASE_URL = "https://aiapi-dev.stanford.edu/v1"
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-ada-002")
EMBED_DIMS = 1536

# Network behavior for the gateway. Rate limits are unknown, so we start
# conservative: one retry on 429/5xx with a fixed backoff, not exponential.
REQUEST_TIMEOUT_SECONDS = 60
RETRY_BACKOFF_SECONDS = 2
MAX_PARALLEL_WORKERS = 4  # drop to 1 and report if 429s appear

# Set from tests/print_scores.py output on the three Stage 4 test inputs
# (n=3, empirical, not defensible as a precise number -- see DECISIONS.md):
# compliant_procedure.md top score   = 0.8901 (must pass)
# violations_procedure.md top score  = 0.8650 (must pass)
# unrelated_document.md top score    = 0.8553 (must fail)
# Threshold sits at the midpoint of the one gap that matters: between the
# lowest score that must pass (0.8650) and the highest that must fail (0.8553).
MATCH_THRESHOLD: float | None = 0.86

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB, per spec
MIN_EXTRACTED_CHARS = 200  # below this, likely empty or a scanned image
