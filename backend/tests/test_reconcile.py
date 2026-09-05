"""Unit tests for verify_requirements()'s coverage reconcile: every
requirement ends up with exactly one verdict, regardless of what the LLM
actually returns. The LLM call is mocked (via _call_batch_with_retry) so
no network call happens; the real _process_batch/verify_requirements
reconcile logic runs against a controlled FindingBatch.
"""

from unittest.mock import patch

from app.schemas import Evidence, Finding, FindingBatch, Requirement, Verdict
from app.verify import build_counts, verify_requirements

DOC = "Passwords must not be shared with anyone. Multi-factor authentication is encouraged."


def _requirement(n: int) -> Requirement:
    return Requirement(id=f"req-{n:02d}", section="4.1", text=f"Requirement number {n}.", obligation="must")


def _aligned_finding(n: int, rationale: str = "ok") -> Finding:
    return Finding(
        requirement_id=f"req-{n:02d}",
        verdict=Verdict.ALIGNED,
        evidence=[Evidence(quote="Passwords must not be shared with anyone.", relation="supports")],
        rationale=rationale,
        confidence=0.9,
    )


def test_llm_returns_findings_for_all_requirements_unchanged():
    requirements = [_requirement(1), _requirement(2), _requirement(3)]
    fake_batch = FindingBatch(findings=[_aligned_finding(1), _aligned_finding(2), _aligned_finding(3)])
    with patch("app.verify._call_batch_with_retry", return_value=fake_batch):
        result = verify_requirements(DOC, requirements)
    assert [f.requirement_id for f in result] == ["req-01", "req-02", "req-03"]
    assert all(f.verdict == Verdict.ALIGNED for f in result)


def test_llm_omits_two_requirement_ids_synthetic_flagged_findings_added():
    requirements = [_requirement(n) for n in range(1, 6)]  # req-01..req-05
    # LLM only answers for 1, 2, 3 -- silently omits 4 and 5
    fake_batch = FindingBatch(findings=[_aligned_finding(1), _aligned_finding(2), _aligned_finding(3)])
    with patch("app.verify._call_batch_with_retry", return_value=fake_batch):
        result = verify_requirements(DOC, requirements)
    assert len(result) == len(requirements) == 5
    by_id = {f.requirement_id: f for f in result}
    assert by_id["req-04"].verdict == Verdict.FLAGGED
    assert by_id["req-04"].rationale == "No verdict was returned for this requirement."
    assert by_id["req-05"].verdict == Verdict.FLAGGED
    assert by_id["req-05"].rationale == "No verdict was returned for this requirement."
    assert by_id["req-01"].verdict == Verdict.ALIGNED


def test_llm_returns_duplicate_requirement_id_first_occurrence_kept():
    """Documenting current behavior: within one batch's findings list, the
    reconcile step (findings_by_id.setdefault) keeps whichever finding for a
    given id appears FIRST in the LLM's own findings list order; later
    duplicates for the same id are silently ignored."""
    requirements = [_requirement(1), _requirement(2)]
    first = _aligned_finding(1, rationale="first answer")
    duplicate = Finding(
        requirement_id="req-01",
        verdict=Verdict.CONTRADICTED,
        evidence=[Evidence(quote="Passwords must not be shared with anyone.", relation="contradicts")],
        rationale="second answer",
        confidence=0.9,
    )
    fake_batch = FindingBatch(findings=[first, duplicate, _aligned_finding(2)])
    with patch("app.verify._call_batch_with_retry", return_value=fake_batch):
        result = verify_requirements(DOC, requirements)
    assert len(result) == 2
    by_id = {f.requirement_id: f for f in result}
    assert by_id["req-01"].rationale == "first answer"
    assert by_id["req-01"].verdict == Verdict.ALIGNED


def test_llm_returns_unknown_requirement_id_dropped_not_in_output():
    """The unknown id is correctly dropped and never appears in the output.
    Note: the current code only has a comment explaining the drop
    (`app/verify.py`'s `_process_batch`, "LLM returned an id we didn't ask
    about") -- there is no actual logger call for this specific case, so
    this test does not assert on logging."""
    requirements = [_requirement(1), _requirement(2)]
    unknown = Finding(
        requirement_id="req-99-does-not-exist",
        verdict=Verdict.ALIGNED,
        evidence=[Evidence(quote="Passwords must not be shared with anyone.", relation="supports")],
        rationale="hallucinated id",
        confidence=0.9,
    )
    fake_batch = FindingBatch(findings=[_aligned_finding(1), _aligned_finding(2), unknown])
    with patch("app.verify._call_batch_with_retry", return_value=fake_batch):
        result = verify_requirements(DOC, requirements)
    assert len(result) == 2
    assert "req-99-does-not-exist" not in {f.requirement_id for f in result}


def test_counts_dict_always_has_all_four_verdict_keys_even_when_all_zero():
    counts = build_counts([])
    assert set(counts.keys()) == {Verdict.ALIGNED, Verdict.CONTRADICTED, Verdict.MISSING, Verdict.FLAGGED}
    assert all(count == 0 for count in counts.values())
