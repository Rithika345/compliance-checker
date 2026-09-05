"""Batches requirement checks to the LLM, then applies the honesty layer:
validate the LLM's JSON, verify every quote against the real document text,
downgrade unsupported verdicts, and reconcile coverage so every requirement
ends up with exactly one verdict. Never logs document text.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from pydantic import ValidationError

from app.config import MAX_PARALLEL_WORKERS
from app.llm import chat_json
from app.prompts import VERIFY_SYSTEM, VERIFY_USER_TEMPLATE
from app.schemas import Finding, FindingBatch, FindingOut, Requirement, Verdict

logger = logging.getLogger(__name__)

BATCH_SIZE = 8


def _format_requirements_block(requirements: list[Requirement]) -> str:
    lines = [
        f"- id: {req.id}\n  section: {req.section}\n  obligation: {req.obligation}\n  text: {req.text}"
        for req in requirements
    ]
    return "\n".join(lines)


def _normalize_for_match(text: str) -> str:
    """Curly quotes to straight, whitespace runs collapsed, for substring matching."""
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def _quotes_verified(finding: Finding, doc_text_normalized: str) -> bool:
    """A finding's quotes are verified only if it has at least one quote and
    every quote is a verbatim substring of the document. Missing evidence is
    treated the same as a failed quote, not as vacuously verified: an
    aligned/contradicted claim with nothing to point to should not display
    as confirmed.
    """
    if not finding.evidence:
        return False
    return all(_normalize_for_match(e.quote) in doc_text_normalized for e in finding.evidence if e.quote)


def _call_batch_with_retry(doc_text: str, batch: list[Requirement]) -> FindingBatch:
    """One call; one retry with the validation error appended on failure.

    Network-level retries (429/5xx) already happen inside chat_json -- this
    retry is specifically for a JSON shape that failed Pydantic validation.
    """
    user_message = VERIFY_USER_TEMPLATE.format(
        document_text=doc_text,
        requirements_block=_format_requirements_block(batch),
    )
    try:
        return FindingBatch.model_validate(chat_json(VERIFY_SYSTEM, user_message))
    except (ValidationError, ValueError) as first_error:
        retry_message = (
            user_message
            + f"\n\nYour previous response was invalid ({type(first_error).__name__}). "
            "Return a corrected JSON object matching the required shape exactly. Respond with a JSON object."
        )
        return FindingBatch.model_validate(chat_json(VERIFY_SYSTEM, retry_message))


def _degraded_findings(batch: list[Requirement], rationale: str) -> list[FindingOut]:
    return [
        FindingOut(
            requirement_id=req.id,
            verdict=Verdict.FLAGGED,
            evidence=[],
            rationale=rationale,
            confidence=0.0,
            requirement_text=req.text,
            section=req.section,
            obligation=req.obligation,
            quotes_verified=False,
        )
        for req in batch
    ]


def _process_batch(doc_text: str, doc_text_normalized: str, batch: list[Requirement]) -> list[FindingOut]:
    try:
        finding_batch = _call_batch_with_retry(doc_text, batch)
    except Exception as exc:  # noqa: BLE001 - one bad batch must not fail the whole request
        logger.info(
            "verify batch degraded requirement_ids=%s error=%s",
            [req.id for req in batch],
            type(exc).__name__,
        )
        return _degraded_findings(batch, "LLM output failed validation.")

    req_by_id = {req.id: req for req in batch}
    outputs = []
    for finding in finding_batch.findings:
        req = req_by_id.get(finding.requirement_id)
        if req is None:
            continue  # LLM returned an id we didn't ask about in this batch; coverage reconcile handles gaps

        quotes_ok = _quotes_verified(finding, doc_text_normalized)
        verdict = finding.verdict
        rationale = finding.rationale
        if not quotes_ok and verdict in (Verdict.CONTRADICTED, Verdict.MISSING):
            verdict = Verdict.FLAGGED
            rationale = f"Model quote could not be located in the source document. {rationale}"

        outputs.append(
            FindingOut(
                requirement_id=finding.requirement_id,
                verdict=verdict,
                evidence=finding.evidence,
                rationale=rationale,
                confidence=finding.confidence,
                requirement_text=req.text,
                section=req.section,
                obligation=req.obligation,
                quotes_verified=quotes_ok,
            )
        )
    return outputs


def verify_requirements(doc_text: str, requirements: list[Requirement]) -> list[FindingOut]:
    """Check the document against every requirement of the matched standard."""
    doc_text_normalized = _normalize_for_match(doc_text)
    batches = [requirements[i : i + BATCH_SIZE] for i in range(0, len(requirements), BATCH_SIZE)]

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_WORKERS) as executor:
        batch_results = list(
            executor.map(lambda batch: _process_batch(doc_text, doc_text_normalized, batch), batches)
        )

    findings_by_id: dict[str, FindingOut] = {}
    for batch_findings in batch_results:
        for finding in batch_findings:
            findings_by_id.setdefault(finding.requirement_id, finding)

    reconciled: list[FindingOut] = []
    for req in requirements:
        if req.id in findings_by_id:
            reconciled.append(findings_by_id[req.id])
        else:
            reconciled.append(
                FindingOut(
                    requirement_id=req.id,
                    verdict=Verdict.FLAGGED,
                    evidence=[],
                    rationale="No verdict was returned for this requirement.",
                    confidence=0.0,
                    requirement_text=req.text,
                    section=req.section,
                    obligation=req.obligation,
                    quotes_verified=False,
                )
            )
    return reconciled


def build_counts(findings: list[FindingOut]) -> dict[Verdict, int]:
    counts = {verdict: 0 for verdict in Verdict}
    for finding in findings:
        counts[finding.verdict] += 1
    return counts
