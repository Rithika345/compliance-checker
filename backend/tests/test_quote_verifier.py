"""Unit tests for app.verify's quote verification and the downgrade rule.
No network calls: the downgrade tests mock _call_batch_with_retry so
_process_batch's real post-processing logic runs against a controlled
FindingBatch instead of a live LLM response.
"""

from unittest.mock import patch

from app.schemas import Evidence, Finding, FindingBatch, Requirement, Verdict
from app.verify import _normalize_for_match, _process_batch, _quotes_verified

DOC = "Passwords must not be shared with anyone. The policy is unacceptable to us in its current form."


def _finding(verdict, quote=None, evidence=None, rationale="rationale"):
    if evidence is None:
        evidence = [Evidence(quote=quote, relation="contradicts")] if quote is not None else []
    return Finding(requirement_id="req-01", verdict=verdict, evidence=evidence, rationale=rationale, confidence=0.9)


def _verified(finding: Finding, doc: str) -> bool:
    """_quotes_verified's second parameter is the ALREADY-normalized document
    (verify_requirements normalizes doc_text once upfront, before any
    per-finding check) -- mirror that contract here instead of passing a raw
    document, or a doc containing curly quotes would wrongly fail to match
    even a real one-quote-side-only normalization case."""
    return _quotes_verified(finding, _normalize_for_match(doc))


def test_exact_quote_is_verified():
    f = _finding(Verdict.CONTRADICTED, "Passwords must not be shared with anyone.")
    assert _verified(f, DOC) is True


def test_quote_with_different_whitespace_is_verified():
    f = _finding(Verdict.CONTRADICTED, "Passwords  must not\nbe shared with anyone.")
    assert _verified(f, DOC) is True


def test_quote_with_curly_quotes_where_document_has_straight_is_verified():
    doc = "The policy says “no sharing” is allowed, and it’s final."
    f = _finding(Verdict.CONTRADICTED, "The policy says \"no sharing\" is allowed, and it's final.")
    assert _verified(f, doc) is True


def test_quote_with_straight_quotes_where_document_has_curly_is_verified():
    doc = "The policy says \"no sharing\" is allowed, and it's final."
    f = _finding(Verdict.CONTRADICTED, "The policy says “no sharing” is allowed, and it’s final.")
    assert _verified(f, doc) is True


def test_quote_with_different_case_not_verified_matching_is_case_sensitive_by_design():
    """Verbatim means verbatim: the verifier does not fold case. A quote
    that differs only in case is treated the same as any other mismatch."""
    f = _finding(Verdict.CONTRADICTED, "PASSWORDS MUST NOT BE SHARED WITH ANYONE.")
    assert _verified(f, DOC) is False


def test_paraphrased_quote_not_verified():
    f = _finding(Verdict.CONTRADICTED, "Passwords should never be given to other people.")
    assert _verified(f, DOC) is False


def test_quote_substring_of_a_larger_word_is_verified_no_word_boundary_enforcement():
    """Documenting current behavior, not asserting it's ideal: the verifier
    does a raw substring check with no word-boundary awareness, so a short
    quote that happens to appear inside a longer word (here, "accept" inside
    "unacceptable") is still reported as verified."""
    f = _finding(Verdict.CONTRADICTED, "accept")
    assert _verified(f, DOC) is True


def test_empty_quote_not_verified():
    f = _finding(Verdict.CONTRADICTED, "")
    assert _verified(f, DOC) is False


def test_quote_longer_than_document_not_verified_no_exception():
    f = _finding(Verdict.CONTRADICTED, DOC * 10)
    assert _verified(f, DOC) is False


def test_missing_verdict_with_empty_evidence_list_not_verified():
    f = _finding(Verdict.MISSING, evidence=[])
    assert _verified(f, DOC) is False


def _process_one_mocked(finding: Finding) -> "list":
    """Run the real _process_batch against one controlled Finding, with the
    network call replaced so no LLM/gateway call happens."""
    req = Requirement(id="req-01", section="4.1", text="Passwords must not be shared.", obligation="must")
    with patch("app.verify._call_batch_with_retry", return_value=FindingBatch(findings=[finding])):
        return _process_batch(DOC, _normalize_for_match(DOC), [req], 1, None)


def test_contradicted_with_unverified_quote_downgraded_to_flagged_with_prefixed_rationale():
    finding = _finding(Verdict.CONTRADICTED, "This sentence is not in the document at all.", rationale="orig reason")
    [out] = _process_one_mocked(finding)
    assert out.verdict == Verdict.FLAGGED
    assert out.quotes_verified is False
    assert out.rationale.startswith("Model quote could not be located in the source document.")
    assert "orig reason" in out.rationale


def test_aligned_with_unverified_quote_downgraded_too_regression_for_earlier_bug():
    """Earlier the same day, aligned verdicts were NOT downgraded when their
    quote couldn't be verified -- only contradicted/missing were. That was
    fixed so any non-flagged verdict with a bad quote gets downgraded.
    Asserting it stays fixed."""
    finding = _finding(Verdict.ALIGNED, "This sentence is not in the document at all.", rationale="orig reason")
    [out] = _process_one_mocked(finding)
    assert out.verdict == Verdict.FLAGGED
    assert out.quotes_verified is False
    assert out.rationale.startswith("Model quote could not be located in the source document.")


def test_missing_with_empty_evidence_list_downgraded_to_flagged():
    finding = _finding(Verdict.MISSING, evidence=[], rationale="orig reason")
    [out] = _process_one_mocked(finding)
    assert out.verdict == Verdict.FLAGGED
    assert out.quotes_verified is False
    assert out.rationale.startswith("Model quote could not be located in the source document.")
