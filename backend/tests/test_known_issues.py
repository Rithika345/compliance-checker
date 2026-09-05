"""Known, documented issues kept visible via xfail rather than hidden or
silently left broken. See DECISIONS.md for the full investigation of each.
"""

from pathlib import Path

import pytest

from app.extract import chunk_text, extract_text
from app.retrieval import match_document

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_INPUTS = REPO_ROOT / "test_inputs"


@pytest.mark.xfail(
    reason="sibling confusion: Protection vs Construction flips on heading loss", strict=True
)
def test_flattened_password_protection_pdf_matches_wrong_sibling():
    """compliant_procedure.pdf/docx (headings stripped to plain paragraphs by
    the fixture generator) stably matches password-construction-guidelines
    instead of the correct password-protection-policy -- confirmed identical
    across 3 repeated runs, not LLM randomness. Retrieval itself ranks
    protection ahead by a real margin (0.8915 vs 0.8792); gate 2's judgment
    flips it anyway. The equivalent .md version (headings intact) correctly
    matches password-protection-policy with the same underlying content.

    Not fixed here: an earlier attempt to make gate 2 stricter about
    "subject matter vs. topic overlap" fixed this exact sibling pair for one
    test case but broke it for another (see DECISIONS.md), and was reverted.
    The planned direction is filter-then-rank in gate 2 rather than a single
    LLM judgment call, not attempted yet.
    """
    data = (TEST_INPUTS / "compliant_procedure.pdf").read_bytes()
    text = extract_text(data)
    chunks = chunk_text(text)
    match = match_document(text, chunks)
    assert match.standard_id == "password-protection-policy"
