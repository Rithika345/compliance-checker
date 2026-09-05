# DESIGN_PLAN.md: Document Compliance Checker

Planning doc for the build. This becomes the base of the deliverable `DESIGN.md`, which Rithika writes in her own words at the end of the day.

Deadline: 6:00 PM today. Everything here is scoped to that.

---

## 1. What we are building (one paragraph)

A local web app. You upload an operating procedure (PDF, DOCX, TXT, MD). The backend picks the single best matching SANS security policy from a library of 25+ using embedding retrieval, or says "no match." It then asks an LLM to judge the document against every atomic requirement of that policy, one verdict each: `aligned`, `contradicted`, `missing`, `flagged_for_review`. Contradicted and missing verdicts carry the requirement text and a verbatim quote from the uploaded document. Every quote is checked against the real document text before it is shown.

---

## 2. Architecture (five boxes)

```
OFFLINE (run once, results committed to repo)
  SANS PDFs  -->  pypdf text  -->  LLM extracts {title, summary, scope, requirements[]}
             -->  cache JSON per standard  -->  embed (requirements + profile)  -->  index.npy

ONLINE (POST /api/evaluate, synchronous)
  upload  -->  extract text  -->  embed doc chunks  -->  score each standard
          -->  threshold gate  -->  LLM plausibility gate  -->  matched standard (or no match)
          -->  LLM verifies requirements in batches (strict JSON)
          -->  Pydantic validation  -->  quote verifier  -->  coverage reconcile
          -->  counts + findings  -->  JSON response  -->  frontend renders
```

Two things to be able to say about this shape:

- Retrieval narrows 25 standards to 1 before any LLM sees the document. The LLM never sees all standards. (Spec: "do not brute-force the LLM.")
- The LLM's output is treated as untrusted. Pydantic enforces the shape, the quote verifier enforces the content.

---

## 3. Components

### 3.1 Standards library

Source: `github.com/deepanshusood/SANS-Security-Policy-Templates` (63 PDFs).

Excluded: files 22 through 35 (incident handling forms). They are forms and checklists with no obligations, so they are not standards. Also skip anything that fails text extraction.

Target: 25 to 30 clean policies. More than 20 satisfies the spec; fewer than all 49 keeps ingest fast and retrieval cleaner.

Assumption to verify in Stage 0: the PDFs are text, not scanned images. If scanned, fall back to NIST OSCAL JSON (allowed by spec).

### 3.2 Ingest (`ingest.py`, offline)

Per PDF:

1. `pypdf` extracts text. Collapse whitespace. No regex section parsing; PDF text is too messy to trust a regex at 11 AM.
2. One LLM call (gpt-4.1, JSON mode) returns:
   ```json
   {
     "title": "Password Protection Policy",
     "summary": "2 sentences on what this policy governs",
     "scope": "who and what it applies to",
     "requirements": [
       {"id": "password-protection-01", "section": "4.1", "text": "Passwords must be changed at least every 90 days.", "obligation": "must"}
     ]
   }
   ```
   The prompt tells the model to pull requirements only from the Policy body, ignore Overview/Purpose/Compliance/Definitions/Revision History boilerplate, and make each requirement atomic and independently testable.
3. Save `standards_cache/<slug>.json`.
4. Embed each requirement text (prefixed with the title) plus one profile chunk (`title + summary + scope`) with `text-embedding-ada-002`. Save all vectors to `standards_cache/index.npy` and a sidecar `standards_cache/index_meta.json` mapping row to `(standard_id, chunk_type, requirement_id)`.

The cache is committed to git. The grader never runs ingest. Ingest is idempotent: it skips standards whose JSON already exists unless `--force`.

Why LLM-driven extraction instead of regex chunking: the spec already requires atomic requirement extraction, so the LLM call is mandatory anyway. Letting it also do the structuring removes the fragile part. Boilerplate that is identical across all SANS policies never enters the index, which is the main thing that would otherwise break retrieval.

### 3.3 Document intake

- Accept `.pdf`, `.docx`, `.txt`, `.md`. Validate by content, not just extension: PDF starts with `%PDF`, DOCX is a zip containing `word/document.xml`, TXT/MD must decode as UTF-8 (fall back to latin-1, then reject).
- Size cap 10 MB. Reject with 400 and a plain reason.
- Process entirely in memory. Never write the upload to disk. Never log its contents.
- Extract text: `pypdf` for PDF, `python-docx` for DOCX, direct decode for text. Collapse whitespace runs but keep paragraph breaks.
- If fewer than 200 characters after extraction: 400, "document appears empty or is a scanned image."
- Chunk into paragraph groups of roughly 300 words for retrieval. Keep `chunk_index`. Keep the full text separately for the LLM and the quote verifier.

### 3.4 Retrieval and matching

1. Embed all doc chunks in one `embeddings` API call.
2. Cosine similarity of every doc chunk against every index row (numpy matmul on normalized vectors).
3. Per standard: take the max similarity per index row across doc chunks, then average the top 3 rows for that standard. This is "sustained overlap," not "one lucky sentence." Call this `score`.
4. Sort standards by score. Keep top 5 as `candidates` for the response (explainability).
5. Gate 1, absolute threshold: `top_score >= MATCH_THRESHOLD`. This number is set empirically from the score vectors on the three test inputs and lives in `config.py`. ada-002 scores are compressed (unrelated text often lands around 0.70 to 0.75), so the threshold will be higher than intuition suggests. Do not defend the number in the interview; defend the method.
6. Gate 2, LLM plausibility: one call with the top candidate's title and summary plus the first ~1500 characters of the document. Returns `{"plausible": bool, "reason": str}` in JSON mode. If false, no match. The `reason` is shown on the no-match screen.
7. If both gates pass, the top candidate is the match. The runner-up and its score are reported, never gated on. (An earlier version of this plan gated on margin over median; dropped because overlapping SANS policies like Password Protection vs Password Construction would trigger false no-matches.)

Escape hatch if retrieval confuses sibling policies during testing: change gate 2 to show the top 3 candidates and let the LLM pick one or none. One prompt change, no architecture change.

No match returns HTTP 200 with `match.matched = false`, empty findings, and all counts zero. It is a valid result, not an error.

### 3.5 Verification

- Load the cached requirements for the matched standard (typically 15 to 50).
- Split into batches of 8.
- Per batch, one LLM call (gpt-4.1, JSON mode, temperature 0) containing:
  - System prompt: role, the four verdict definitions, the rule that every quote must be copied verbatim, the rule that `missing` must include the closest related passage (or the document's scope/purpose paragraph if nothing is related) with `relation: closest_related`, and an explicit statement that the document is untrusted data and any instructions inside it must be ignored.
  - The full document text inside clear delimiters (`<<<DOCUMENT>>> ... <<<END DOCUMENT>>>`).
  - The batch of requirements with ids.
  - The required JSON shape.
- Batches run in a `ThreadPoolExecutor(max_workers=4)`. The endpoint stays synchronous. If the gateway returns 429s, drop to `max_workers=1`.
- Fallback if the document exceeds ~60k characters: pass only the top 6 doc chunks by cosine per batch. Not built unless a test forces it.

### 3.6 Post-processing (the honesty layer)

1. `FindingBatch.model_validate_json`. On `ValidationError`: retry that batch once with the error text appended. On second failure: every requirement in the batch becomes `flagged_for_review` with rationale "LLM output failed validation." Never 500 the whole request over one batch.
2. Quote verifier: normalize whitespace and quotes (curly to straight) on both sides, check each `evidence.quote` is a substring of the document text. Set `quotes_verified`. If any quote fails and the verdict is `contradicted` or `missing`, downgrade to `flagged_for_review` and prepend "Model quote could not be located in the source document." to the rationale.
3. Coverage reconcile: any requirement id with no finding gets a synthetic `flagged_for_review` finding. Every requirement gets exactly one verdict.
4. Build `counts` with all four keys always present.

### 3.7 API

`POST /api/evaluate` (multipart file upload) returns `EvaluateResponse`.
`GET /api/standards` returns the list of loaded standards with requirement counts (useful for the UI and for the demo).
`GET /api/health` returns `{"ok": true, "standards_loaded": N, "model": "..."}`.

Startup: load cache into memory, fail loudly if missing ("run `python ingest.py`"), fail loudly if `STANFORD_API_KEY` is missing.

### 3.8 Frontend

Stage B (the deliverable): one `static/index.html` served by FastAPI. Vanilla JS. Upload control, loading state, match banner (standard title + score + plausibility reason), no-match state, four verdict count tiles, findings list grouped by verdict with requirement text, quote, rationale, and a small "quote verified" marker.

Stage C (only if everything else is done): the same page in Vite + React.

---

## 4. Schemas

```python
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

class StandardExtraction(BaseModel):
    title: str
    summary: str
    scope: str
    requirements: list[Requirement]


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

class PlausibilityCheck(BaseModel):
    plausible: bool
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
```

Note on `Finding`: an earlier draft rejected any `contradicted` finding without a contradicting quote and any `missing` finding without evidence at the validator level. That was dropped: a strict validator turns one model slip into a failed batch. The rule is enforced in the prompt and in post-processing (downgrade to flagged) instead. Same outcome, less fragile.

---

## 5. Security and edge cases (built in, not bolted on)

| Risk | Mitigation |
|---|---|
| API key leaks into repo | `.env` + `.gitignore` committed before any code. Key read from env only. App refuses to start without it. Never logged. |
| Malicious or malformed upload | Content sniffing, 10 MB cap, in-memory only, no user-controlled filenames touch the filesystem. |
| Prompt injection inside the document ("mark everything aligned") | Document wrapped in delimiters and labeled untrusted; system prompt separate; quote verifier catches verdicts the text does not support. Not bulletproof. Say so in DESIGN.md. |
| LLM returns bad JSON | JSON mode + Pydantic + one retry + per-batch degrade to flagged. |
| LLM fabricates a quote | Verbatim substring check. Downgrade, annotate, display. |
| LLM skips a requirement | Coverage reconcile adds a flagged finding. |
| Gateway timeout or 429 | 60s timeout per call, one retry with backoff, then degrade the batch. |
| Empty or scanned PDF | 400 with a plain message. |
| Huge document | 10 MB cap; top-chunks fallback if a test needs it. |
| CORS | Locked to localhost origins. Irrelevant for Stage B (same origin), needed for Stage C. |
| Non-English document | Out of scope, noted. |

---

## 6. Decisions and why (the interview list)

| Decision | Why | What I would say if challenged |
|---|---|---|
| Stanford AI gateway, `gpt-4.1` for all LLM calls | It is the provider the org runs; LiteLLM proxy confirmed to pass `response_format` and `temperature`; one env var swaps models. | Groq and Ollama were options; this one had structured output confirmed by 10:50 AM and zero setup. |
| `text-embedding-ada-002` | The only embedding model on the gateway; avoids a 2 GB torch install for the grader. | Its scores are compressed, which is why the LLM plausibility gate is load-bearing rather than optional. |
| numpy cosine, no vector DB | ~300 vectors. Matmul is faster to write, faster to run, one less dependency to break in the grader's 10 minutes. | At 10k standards I would move to FAISS or pgvector; the interface (`score_standards(doc_vectors) -> list`) does not change. |
| LLM-driven requirement extraction at ingest, no regex chunking | Spec requires atomic extraction anyway; PDFs are messy; boilerplate never enters the index. | Cost is ~25 one-time calls, cached and committed. |
| Exclude the 14 incident-handling forms | Forms have no obligations, so they are not standards. Including them creates empty standards and noise. | Deliberate curation of the library is itself a design choice. |
| Two-gate no-match (threshold + LLM plausibility) | Threshold alone is brittle with ada-002; LLM alone would be brute force if run on every standard. Together: retrieval proposes, LLM confirms. | Threshold is empirical from n=3, and I would show the score distributions. |
| Quotes verified against source text | LLMs fabricate quotes. A verdict the document does not support is downgraded, never shown as fact. | This is the difference between explainable and black box. |
| JSON mode + Pydantic, not `json_schema` mode | `json_object` is confirmed working on the gateway; `json_schema` untested. Pydantic covers the gap. | Would test `json_schema` next; it removes the retry path. |
| Synchronous endpoint | One request is 10 to 20 seconds. A queue is complexity with no user benefit at this scale. | At scale: job id + polling, and cache verdicts by (doc hash, standard id). |
| Batches of 8 requirements, whole document per batch | Balances JSON size against call count. Procedures are short enough to fit. | Per-requirement calls would be more precise and 5x the cost. |
| Static HTML first, React only if time remains | Spec allows plain HTML. Grader time goes to the pipeline and walkthrough, not UI polish. | React version is the same page; nothing about the API changes. |

---

## 7. How I would measure accuracy at scale

(This is a stated walkthrough question. Have an answer.)

- Build a labeled set: N procedures, each with a ground-truth standard and per-requirement verdicts written by a human. The three test inputs are the seed.
- Matching accuracy: top-1 accuracy and no-match precision/recall.
- Verdict accuracy: per-verdict precision/recall, confusion matrix across the four labels. Track `flagged_for_review` rate separately as a "model gave up" signal.
- Citation accuracy: fraction of quotes that pass the verbatim verifier (already logged), plus human check that the quote actually supports the verdict.
- Run the labeled set on every prompt or model change. That is the regression suite.

---

## 8. What I would build next

- Requirement-level retrieval for long documents (top-k chunks per requirement instead of whole document).
- `json_schema` structured output to remove the validation retry path.
- Verdict caching keyed on document hash + standard id.
- Human review UI for `flagged_for_review` findings, feeding a labeled set.
- Multi-standard evaluation when a document plausibly spans two policies.

---

## 9. Known limitations (say them before being asked)

- Threshold calibrated on three documents.
- Prompt injection mitigations are partial.
- Requirement extraction quality depends on one LLM pass per standard; not human-reviewed.
- Whole-document-per-batch limits document length in practice.
- Tested on macOS with Python 3.14; README will state versions.
