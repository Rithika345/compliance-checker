"""Prints the full sorted score vector (every standard) for each of the
three Stage 4 test inputs, plus the gate 1/gate 2 match decision once
MATCH_THRESHOLD is set in app/config.py. Used to pick the threshold
empirically and to verify Stage 2 before moving on.
"""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app import config  # noqa: E402
from app.extract import chunk_text, extract_text  # noqa: E402
from app.retrieval import match_document, score_standards  # noqa: E402

TEST_INPUTS = BACKEND_ROOT.parent / "test_inputs"
INPUT_FILES = ["compliant_procedure.md", "violations_procedure.md", "unrelated_document.md"]


def run_one(filename: str) -> None:
    path = TEST_INPUTS / filename
    doc_text = extract_text(path.read_bytes())
    doc_chunks = chunk_text(doc_text)

    print(f"=== {filename} ({len(doc_chunks)} chunks) ===")
    scores = score_standards(doc_chunks)
    for candidate in scores:
        print(f"  {candidate.score:.4f}  {candidate.standard_id:50s} {candidate.title}")

    if config.MATCH_THRESHOLD is None:
        print("  MATCH_THRESHOLD not set yet in app/config.py -- gate 1/2 skipped.\n")
        return

    result = match_document(doc_text, doc_chunks)
    print(
        f"  -> matched={result.matched} standard={result.standard_id} "
        f"score={result.score:.4f} reason={result.reason}"
    )
    print()


def main() -> None:
    for filename in INPUT_FILES:
        run_one(filename)


if __name__ == "__main__":
    main()
