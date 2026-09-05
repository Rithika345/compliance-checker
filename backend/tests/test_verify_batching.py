"""Unit tests for verify.py's batching and per-batch degradation. The LLM
call is mocked (via _call_batch_with_retry, verify.py's own call site) so
no network call happens; ThreadPoolExecutor concurrency itself still runs
for real, just over mocked work.
"""

from unittest.mock import patch

from app.config import VERIFY_BATCH_SIZE
from app.schemas import Evidence, Finding, FindingBatch, Requirement, Verdict
from app.verify import verify_requirements

DOC = "Passwords must not be shared with anyone."


def _requirement(n: int) -> Requirement:
    return Requirement(id=f"req-{n:02d}", section="4.1", text=f"Requirement number {n}.", obligation="must")


def _aligned_finding(n: int) -> Finding:
    return Finding(
        requirement_id=f"req-{n:02d}",
        verdict=Verdict.ALIGNED,
        evidence=[Evidence(quote="Passwords must not be shared with anyone.", relation="supports")],
        rationale="ok",
        confidence=0.9,
    )


def test_18_requirements_split_into_batches_of_8_8_2():
    assert VERIFY_BATCH_SIZE == 8, "test assumes the documented default batch size; update if config changes"
    requirements = [_requirement(n) for n in range(1, 19)]  # 18 requirements
    batch_sizes: dict[int, int] = {}

    def fake_call(doc_text, batch, batch_number, usage):
        batch_sizes[batch_number] = len(batch)
        return FindingBatch(findings=[_aligned_finding(int(r.id.split("-")[1])) for r in batch])

    with patch("app.verify._call_batch_with_retry", side_effect=fake_call):
        result = verify_requirements(DOC, requirements)

    assert batch_sizes == {1: 8, 2: 8, 3: 2}
    assert len(result) == 18


def test_one_batch_fails_validation_others_unaffected_no_exception_propagates():
    requirements = [_requirement(n) for n in range(1, 19)]  # -> batches 1, 2, 3

    def fake_call(doc_text, batch, batch_number, usage):
        if batch_number == 2:
            # simulates chat_json_validated exhausting its own internal retry
            # and still failing -- _process_batch must catch this, not let
            # it propagate out of verify_requirements. Triggered naturally
            # (not hand-constructed) so it's a real ValidationError.
            FindingBatch.model_validate({"findings": "not a list, invalid shape"})
        return FindingBatch(findings=[_aligned_finding(int(r.id.split("-")[1])) for r in batch])

    with patch("app.verify._call_batch_with_retry", side_effect=fake_call):
        result = verify_requirements(DOC, requirements)  # must not raise

    by_id = {f.requirement_id: f for f in result}
    # batch 2 covers req-09..req-16 (the second group of 8)
    for n in range(9, 17):
        f = by_id[f"req-{n:02d}"]
        assert f.verdict == Verdict.FLAGGED
        assert f.rationale == "LLM output failed validation."
    # batches 1 and 3 (req-01..08, req-17..18) are unaffected
    for n in list(range(1, 9)) + [17, 18]:
        assert by_id[f"req-{n:02d}"].verdict == Verdict.ALIGNED


def test_findingbatch_json_with_extra_unexpected_field_accepted_not_a_failure():
    """A stricter schema (extra="forbid") would turn a harmless extra field
    from the LLM into a validation failure, needlessly degrading a batch
    that's otherwise perfectly usable. Pydantic's default (extra="ignore")
    means this must not happen."""
    raw = {
        "findings": [
            {
                "requirement_id": "req-01",
                "verdict": "aligned",
                "evidence": [{"quote": "Passwords must not be shared with anyone.", "relation": "supports"}],
                "rationale": "ok",
                "confidence": 0.9,
                "unexpected_extra_field": "should be ignored, not rejected",
            }
        ],
        "another_unexpected_top_level_field": 12345,
    }
    batch = FindingBatch.model_validate(raw)  # must not raise
    assert len(batch.findings) == 1
    assert batch.findings[0].requirement_id == "req-01"
    assert not hasattr(batch.findings[0], "unexpected_extra_field")

    # and end to end through _process_batch: the finding is processed
    # normally, not degraded, since chat_json_validated would have handed
    # back an already-successfully-validated FindingBatch.
    requirements = [_requirement(1)]
    with patch("app.verify._call_batch_with_retry", return_value=batch):
        result = verify_requirements(DOC, requirements)
    assert result[0].verdict == Verdict.ALIGNED
