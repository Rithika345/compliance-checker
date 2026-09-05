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


def run_compliant() -> dict[Verdict, int]:
    print("=== compliant_procedure.md ===")
    match, findings = evaluate_document("compliant_procedure.md")
    print(f"  matched={match.matched} standard={match.standard_id} score={match.score:.4f}")
    assert match.matched, "compliant_procedure.md should match a standard"

    counts = build_counts(findings)
    print(f"  counts={ {v.value: c for v, c in counts.items()} }")
    print()
    return counts


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


def run_format_regression() -> None:
    """compliant_clean_desk_procedure.pdf and .docx carry the same content as
    the .md version (paragraphs re-flowed, markdown syntax stripped) --
    confirms the pipeline behaves consistently across upload formats.

    This deliberately does NOT use compliant_procedure.md/Password Protection
    Policy: that standard has a known, documented sibling-confusion issue
    (Password Protection vs. Password Construction) that flips the matched
    standard when headings are flattened to plain text -- confirmed stable
    across repeated runs, not a flake. That's a real, separate finding (see
    DECISIONS.md and tests/test_known_issues.py, which keeps it visible via
    an explicit xfail rather than hiding it). Format-handling consistency
    needs a standard with no close sibling to test the thing it's meant to
    test, so this uses Clean Desk Policy instead, whose top candidate led
    the runner-up by 0.06+ for all three formats.
    """
    print("=== compliant_clean_desk_procedure.md (format regression baseline) ===")
    md_match, md_findings = evaluate_document("compliant_clean_desk_procedure.md")
    print(f"  matched={md_match.matched} standard={md_match.standard_id} score={md_match.score:.4f}")
    assert md_match.matched, "compliant_clean_desk_procedure.md should match a standard"
    md_counts = build_counts(md_findings)
    print(f"  counts={ {v.value: c for v, c in md_counts.items()} }")
    print()

    for filename in ("compliant_clean_desk_procedure.pdf", "compliant_clean_desk_procedure.docx"):
        print(f"=== {filename} (format regression) ===")
        match, findings = evaluate_document(filename)
        print(f"  matched={match.matched} standard={match.standard_id} score={match.score:.4f}")
        assert match.matched, f"{filename} should match a standard"
        assert match.standard_id == md_match.standard_id, (
            f"{filename} matched {match.standard_id}, expected {md_match.standard_id} (same as the .md version)"
        )

        counts = build_counts(findings)
        print(f"  counts={ {v.value: c for v, c in counts.items()} }")
        for verdict in Verdict:
            diff = abs(counts[verdict] - md_counts[verdict])
            assert diff <= 2, (
                f"{filename} {verdict.value} count {counts[verdict]} differs from .md's "
                f"{md_counts[verdict]} by {diff} (> 2)"
            )
        print()


def main() -> None:
    run_compliant()
    caught, total = run_violations()
    run_unrelated()
    run_format_regression()

    print(f"=== Summary: {caught}/{total} planted violations caught ===")
    if caught < total:
        print("Not all planted violations were caught -- see test_inputs/violations_answer_key.md.")


if __name__ == "__main__":
    main()
