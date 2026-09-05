"""Pydantic models for everything that crosses a boundary: offline standard
extraction, online LLM verdicts, and the API request/response shapes. Copied
from DESIGN_PLAN.md section 4.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    ALIGNED = "aligned"
    CONTRADICTED = "contradicted"
    MISSING = "missing"
    FLAGGED = "flagged_for_review"


# offline
class Requirement(BaseModel):
    id: str
    section: str
    text: str
    obligation: Literal["must", "should", "may"]


class IngestMeta(BaseModel):
    source_filename: str
    ingested_at: str
    extraction_model: str
    embedding_model: str
    requirement_count: int


class StandardExtraction(BaseModel):
    title: str
    summary: str
    scope: str
    requirements: list[Requirement]
    meta: IngestMeta | None = None


# online, LLM output
class Evidence(BaseModel):
    quote: str
    relation: Literal["supports", "contradicts", "closest_related"]


class Finding(BaseModel):
    requirement_id: str
    verdict: Verdict
    evidence: list[Evidence] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class FindingBatch(BaseModel):
    findings: list[Finding]


# Stage 2 escape hatch (DESIGN_PLAN 3.4): retrieval alone confused sibling
# policies (e.g. Password Protection vs. Password Construction), so gate 2
# shows the LLM the top-3 candidates and lets it pick one or none, instead
# of only judging the top-1 candidate's plausibility.
class CandidateSelection(BaseModel):
    standard_id: str | None = None
    reason: str


# API response
class CandidateScore(BaseModel):
    standard_id: str
    title: str
    score: float


class MatchResult(BaseModel):
    matched: bool
    standard_id: str | None = None
    title: str | None = None
    score: float
    candidates: list[CandidateScore]
    reason: str


class FindingOut(Finding):
    requirement_text: str
    section: str
    obligation: str
    quotes_verified: bool


class EvaluateResponse(BaseModel):
    document_name: str
    match: MatchResult
    counts: dict[Verdict, int]
    findings: list[FindingOut]
    model: str


# Not in DESIGN_PLAN section 4 (added in Stage 3 for the two GET routes,
# which also cross the API boundary and so also get a Pydantic model).
class StandardSummary(BaseModel):
    standard_id: str
    title: str
    requirement_count: int


class HealthStatus(BaseModel):
    ok: bool
    standards_loaded: int
    model: str
