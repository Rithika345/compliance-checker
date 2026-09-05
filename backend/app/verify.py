"""Batches requirement checks to the LLM, then applies the honesty layer:
validate the LLM's JSON, verify every quote against the real document text,
downgrade unsupported verdicts, and reconcile coverage so every requirement
ends up with exactly one verdict. Never logs document text.
"""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from pydantic import ValidationError

from app.config import MAX_PARALLEL_WORKERS, VERIFY_BATCH_SIZE
from app.llm import chat_json_validated
from app.prompts import VERIFY_SYSTEM, VERIFY_USER_TEMPLATE
from app.schemas import Finding, FindingBatch, FindingOut, Requirement, Verdict

logger = logging.getLogger(__name__)


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
    every quote is a non-empty, verbatim substring of the document. Missing
    or empty/whitespace-only evidence is treated the same as a failed quote,
    not as vacuously verified: an aligned/contradicted claim with nothing to
    point to should not display as confirmed. (The emptiness check has to
    happen on the *normalized* quote, not the raw one -- a whitespace-only
    quote like "   " is truthy before normalization, but normalizes to "",
    and "" is trivially a substring of any string.)
    """
    if not finding.evidence:
        return False
    for evidence in finding.evidence:
        normalized_quote = _normalize_for_match(evidence.quote)
        if not normalized_quote or normalized_quote not in doc_text_normalized:
            return False
    return True


def _flagged_finding(req: Requirement, rationale: str) -> FindingOut:
    """A FLAGGED finding with no evidence: used both when an LLM batch call
    fails outright and when a requirement never gets a verdict at all.
    """
    return FindingOut(
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


def _call_batch_with_retry(doc_text: str, batch: list[Requirement]) -> FindingBatch:
    """One call; one retry with the validation error appended on failure.

    Network-level retries (429/5xx) already happen inside chat_json -- this
    retry is specifically for a JSON shape that failed Pydantic validation.
    """
    user_message = VERIFY_USER_TEMPLATE.format(
        document_text=doc_text,
        requirements_block=_format_requirements_block(batch),
    )
    return chat_json_validated(VERIFY_SYSTEM, user_message, FindingBatch)


def _process_batch(doc_text: str, doc_text_normalized: str, batch: list[Requirement]) -> list[FindingOut]:
    try:
        finding_batch = _call_batch_with_retry(doc_text, batch)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        # Validation-type failure (bad JSON shape) surviving chat_json_validated's
        # own retry -- degrade this batch to flagged. Gateway/network errors
        # (APIStatusError, APIConnectionError, APITimeoutError) are NOT caught
        # here; they propagate through verify_requirements to the route, which
        # turns them into a 503.
        logger.info(
            "verify batch degraded requirement_ids=%s error=%s",
            [req.id for req in batch],
            type(exc).__name__,
        )
        return [_flagged_finding(req, "LLM output failed validation.") for req in batch]

    req_by_id = {req.id: req for req in batch}
    outputs = []
    for finding in finding_batch.findings:
        req = req_by_id.get(finding.requirement_id)
        if req is None:
            continue  # LLM returned an id we didn't ask about in this batch; coverage reconcile handles gaps

        quotes_ok = _quotes_verified(finding, doc_text_normalized)
        verdict = finding.verdict
        rationale = finding.rationale
        if not quotes_ok and verdict != Verdict.FLAGGED:
            # Every non-flagged verdict makes a claim (a practice present, absent, or
            # conflicting) that only the quoted evidence backs up -- an aligned verdict
            # with an unverifiable quote is exactly as unsupported as a contradicted one.
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
    batches = [requirements[i : i + VERIFY_BATCH_SIZE] for i in range(0, len(requirements), VERIFY_BATCH_SIZE)]

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
            reconciled.append(_flagged_finding(req, "No verdict was returned for this requirement."))
    return reconciled


def build_counts(findings: list[FindingOut]) -> dict[Verdict, int]:
    counts = {verdict: 0 for verdict in Verdict}
    for finding in findings:
        counts[finding.verdict] += 1
    return counts
