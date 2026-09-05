"""Standards index loading and document-to-standard matching. Retrieval
narrows 30 standards to 1 candidate before any LLM reads the document; the
LLM never sees more than the single best candidate.
"""

import json
import logging
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from app.config import (
    CANDIDATE_POOL_SIZE,
    GATE2_POOL_SIZE,
    MATCH_THRESHOLD,
    PLAUSIBILITY_EXCERPT_CHARS,
    TOP_K_CHUNKS_PER_STANDARD,
)
from app.extract import DocChunk
from app.llm import UsageAccumulator, chat_json_validated, embed
from app.prompts import CANDIDATE_SELECTION_SYSTEM, CANDIDATE_SELECTION_USER_TEMPLATE
from app.schemas import CandidateScore, CandidateSelection, MatchResult, Requirement, StandardExtraction

logger = logging.getLogger(__name__)

STANDARDS_CACHE = Path(__file__).resolve().parent.parent / "standards_cache"


def _load_index() -> tuple[np.ndarray, list[dict]]:
    try:
        vectors = np.load(STANDARDS_CACHE / "index.npy")
        meta = json.loads((STANDARDS_CACHE / "index_meta.json").read_text())
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Standards cache is missing at {STANDARDS_CACHE}; run `python ingest.py` first."
        ) from exc
    if vectors.shape[0] != len(meta):
        raise RuntimeError(
            f"Standards cache is inconsistent: index.npy has {vectors.shape[0]} rows but "
            f"index_meta.json has {len(meta)}; re-run `python ingest.py` to rebuild both together."
        )
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    normalized = vectors / norms
    return normalized, meta


def _load_standards() -> dict[str, StandardExtraction]:
    standards = {}
    for path in sorted(STANDARDS_CACHE.glob("*.json")):
        if path.name == "index_meta.json":
            continue
        standards[path.stem] = StandardExtraction.model_validate_json(path.read_text())
    if not standards:
        raise RuntimeError(
            f"No cached standards found at {STANDARDS_CACHE}; run `python ingest.py` first."
        )
    return standards


# Loaded once at import time, not per-request.
_INDEX_VECTORS, _INDEX_META = _load_index()
_STANDARDS = _load_standards()

_STANDARD_ROW_IDS: dict[str, list[int]] = {}
for _row_idx, _row in enumerate(_INDEX_META):
    _STANDARD_ROW_IDS.setdefault(_row["standard_id"], []).append(_row_idx)

_missing_standards = set(_STANDARD_ROW_IDS) - set(_STANDARDS)
if _missing_standards:
    raise RuntimeError(
        f"Standards cache is inconsistent: index_meta.json references {sorted(_missing_standards)} "
        "with no matching cached JSON; re-run `python ingest.py` to rebuild both together."
    )


def get_requirements(standard_id: str) -> list[Requirement]:
    return _STANDARDS[standard_id].requirements


def list_standards_summary() -> list[dict]:
    return [
        {"standard_id": sid, "title": std.title, "requirement_count": len(std.requirements)}
        for sid, std in sorted(_STANDARDS.items())
    ]


def score_standards(doc_chunks: list[DocChunk], usage: UsageAccumulator | None = None) -> list[CandidateScore]:
    """Score every standard against the document, sorted highest first.

    For each index row (one requirement or profile chunk), take the max
    similarity across all document chunks. Then, per standard, average its
    top-3 row scores. Averaging top-3 rather than taking a single max
    rewards sustained overlap across several requirements, not one
    coincidentally similar sentence.
    """
    doc_vectors = embed([chunk.text for chunk in doc_chunks], purpose="embed", usage=usage)
    doc_norms = np.linalg.norm(doc_vectors, axis=1, keepdims=True)
    doc_vectors = doc_vectors / doc_norms

    similarity = doc_vectors @ _INDEX_VECTORS.T  # (n_doc_chunks, n_index_rows)
    row_max = similarity.max(axis=0)

    scores = []
    for standard_id, row_ids in _STANDARD_ROW_IDS.items():
        row_scores = row_max[row_ids]
        top_k = np.sort(row_scores)[-TOP_K_CHUNKS_PER_STANDARD:]
        scores.append(
            CandidateScore(
                standard_id=standard_id,
                title=_STANDARDS[standard_id].title,
                score=float(top_k.mean()),
            )
        )
    scores.sort(key=lambda candidate: candidate.score, reverse=True)
    return scores


def select_candidate(
    candidates: list[CandidateScore], doc_text: str, usage: UsageAccumulator | None = None
) -> CandidateSelection:
    """Gate 2: show the LLM the top candidates and let it pick one or none.

    A single top-1 plausibility check was tried first and confused sibling
    policies that share vocabulary (Password Protection vs. Password
    Construction scored within 0.001 of each other on one test input, and
    the wrong sibling outright won on another) -- see DECISIONS.md. Showing
    several candidates side by side lets the LLM read the document once and
    judge subject matter directly, rather than rubber-stamping whichever
    candidate retrieval happened to rank first.
    """
    lines = []
    for candidate in candidates:
        summary = _STANDARDS[candidate.standard_id].summary
        lines.append(f"- id: {candidate.standard_id}\n  title: {candidate.title}\n  summary: {summary}")
    user_message = CANDIDATE_SELECTION_USER_TEMPLATE.format(
        candidates_block="\n".join(lines),
        document_excerpt=doc_text[:PLAUSIBILITY_EXCERPT_CHARS],
    )
    return chat_json_validated(
        CANDIDATE_SELECTION_SYSTEM, user_message, CandidateSelection, purpose="plausibility", usage=usage
    )


def match_document(
    doc_text: str, doc_chunks: list[DocChunk], usage: UsageAccumulator | None = None
) -> MatchResult:
    """Apply gate 1 (score threshold) then gate 2 (LLM candidate selection)."""
    candidates = score_standards(doc_chunks, usage=usage)
    top_candidates = candidates[:CANDIDATE_POOL_SIZE]
    top = candidates[0]

    if top.score < MATCH_THRESHOLD:
        return MatchResult(
            matched=False,
            score=top.score,
            candidates=top_candidates,
            reason=f"No standard scored above the match threshold ({top.score:.3f} < {MATCH_THRESHOLD:.3f}).",
        )

    gate2_pool = candidates[:GATE2_POOL_SIZE]
    try:
        selection = select_candidate(gate2_pool, doc_text, usage=usage)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        # Validation-type failure (bad JSON shape) surviving chat_json_validated's
        # own retry -- degrade to no-match. Gateway/network errors (APIStatusError,
        # APIConnectionError, APITimeoutError) are NOT caught here; they propagate
        # to the route, which turns them into a 503.
        logger.warning("gate 2 candidate selection failed validation: %s", type(exc).__name__)
        return MatchResult(
            matched=False,
            score=top.score,
            candidates=top_candidates,
            reason="Could not confirm a matching standard: the plausibility check did not return a valid response.",
        )
    chosen = next((c for c in gate2_pool if c.standard_id == selection.standard_id), None)
    if chosen is None:
        # Either the LLM said no candidate fits, or it returned an id we
        # didn't offer it -- both are treated as "no match" rather than
        # trusting an unvalidated id.
        reason = selection.reason
        if selection.standard_id is not None:
            reason = f"LLM selected an unrecognized candidate id; treated as no match. {reason}"
        return MatchResult(
            matched=False,
            score=top.score,
            candidates=top_candidates,
            reason=reason,
        )
    return MatchResult(
        matched=True,
        standard_id=chosen.standard_id,
        title=chosen.title,
        score=chosen.score,
        candidates=top_candidates,
        reason=selection.reason,
    )
