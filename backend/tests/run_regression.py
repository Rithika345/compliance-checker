"""Runs all three Stage 4 test inputs directly through the pipeline
functions (no HTTP), checks match/no-match, and for violations_procedure.md
checks each planted violation's actual verdict against the table in
test_inputs/violations_answer_key.md. Prints PASS/FAIL per planted
violation and an honest count -- it does not inflate the number caught.
"""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.extract import chunk_text, extract_text  # noqa: E402
from app.retrieval import get_requirements, match_document  # noqa: E402
from app.schemas import MatchResult, FindingOut, Verdict  # noqa: E402
from app.verify import build_counts, verify_requirements  # noqa: E402

TEST_INPUTS = BACKEND_ROOT.parent / "test_inputs"

# Mirrors the "Planted violations" table in test_inputs/violations_answer_key.md.
PLANTED_VIOLATIONS = [
    ("password-protection-policy-02", Verdict.CONTRADICTED),
    ("password-protection-policy-05", Verdict.CONTRADICTED),
    ("password-protection-policy-08", Verdict.CONTRADICTED),
    ("password-protection-policy-10", Verdict.CONTRADICTED),
    ("password-protection-policy-12", Verdict.CONTRADICTED),
    ("password-protection-policy-04", Verdict.MISSING),
    ("password-protection-policy-11", Verdict.MISSING),
]


def evaluate_document(filename: str) -> tuple[MatchResult, list[FindingOut]]:
    path = TEST_INPUTS / filename
    doc_text = extract_text(path.read_bytes())
    doc_chunks = chunk_text(doc_text)
    match = match_document(doc_text, doc_chunks)
    findings: list[FindingOut] = []
    if match.matched:
        requirements = get_requirements(match.standard_id)
        findings = verify_requirements(doc_text, requirements)
    return match, findings


def run_compliant() -> None:
    print("=== compliant_procedure.md ===")
    match, findings = evaluate_document("compliant_procedure.md")
    print(f"  matched={match.matched} standard={match.standard_id} score={match.score:.4f}")
    assert match.matched, "compliant_procedure.md should match a standard"

    counts = build_counts(findings)
    print(f"  counts={ {v.value: c for v, c in counts.items()} }")
    print()


def run_violations() -> tuple[int, int]:
    print("=== violations_procedure.md ===")
    match, findings = evaluate_document("violations_procedure.md")
    print(f"  matched={match.matched} standard={match.standard_id} score={match.score:.4f}")
    assert match.matched, "violations_procedure.md should match a standard"
    assert match.standard_id == "password-protection-policy", (
        f"expected password-protection-policy, got {match.standard_id}"
    )

    findings_by_id = {f.requirement_id: f for f in findings}
    caught = 0
    for req_id, expected_verdict in PLANTED_VIOLATIONS:
        actual = findings_by_id.get(req_id)
        actual_verdict = actual.verdict if actual else None
        ok = actual_verdict == expected_verdict
        caught += int(ok)
        actual_label = actual_verdict.value if actual_verdict else "NO FINDING"
        print(
            f"  {'PASS' if ok else 'FAIL'}  {req_id:35s} "
            f"expected={expected_verdict.value:13s} actual={actual_label}"
        )
    print()
    return caught, len(PLANTED_VIOLATIONS)


def run_unrelated() -> None:
    print("=== unrelated_document.md ===")
    match, _findings = evaluate_document("unrelated_document.md")
    print(f"  matched={match.matched} reason={match.reason}")
    assert not match.matched, "unrelated_document.md should not match any standard"
    print()


def main() -> None:
    run_compliant()
    caught, total = run_violations()
    run_unrelated()

    print(f"=== Summary: {caught}/{total} planted violations caught ===")
    if caught < total:
        print("Not all planted violations were caught -- see test_inputs/violations_answer_key.md.")


if __name__ == "__main__":
    main()
