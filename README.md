# Document Compliance Checker

Upload an operating procedure (PDF, DOCX, TXT, or MD) and it's checked against the single
best-matching SANS security standard from a curated library of 30. If no standard plausibly
applies, the app says so instead of forcing a match. If one does apply, every requirement of
that standard gets one of four verdicts — aligned, contradicted, missing, or flagged for review
— and every quote shown alongside a verdict has been checked against the real document text
before it's displayed.

See `DESIGN.md` for the architecture, the reasoning behind the design decisions, and known
limitations in more depth. This file covers what you need to run it.

## Prerequisites

- Python 3.11 or newer. Built and tested on **Python 3.14, macOS**.
- A Stanford AI API Gateway key (`STANFORD_API_KEY`).
- **No Node.js or npm required** — the frontend is a single static HTML page with vanilla JS, no build step.

## Setup

```bash
git clone <this repo>
cd compliance-checker
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt
cp .env.example .env
# edit .env and fill in STANFORD_API_KEY
```

The standards library is pre-built and committed (`backend/standards_cache/`), so there's no
ingest step to run before starting the app — `python backend/ingest.py` is only needed if you
want to rebuild the cache from `standards_src/` (5-10 minutes of API calls, not required to use
the app).

## Run

```bash
cd backend
uvicorn app.main:app --reload
```

Open **http://localhost:8000** in a browser. Upload a document and click Evaluate.

## Tests

From `backend/`, with the venv active:

```bash
pytest -q                    # unit tests: chunking, quote verification, reconcile,
                              # extraction, batching -- no network calls, LLM mocked where needed
python tests/run_regression.py   # end-to-end: calls the pipeline functions directly (no HTTP),
                                  # runs every file in "test inputs" below against the real gateway
```

## Test inputs

`test_inputs/` has the documents used throughout development to exercise every outcome the app
can produce, plus an answer key and a set of PDF/DOCX fixtures that check the pipeline behaves
the same regardless of upload format:

| File | Expected result |
|---|---|
| `compliant_procedure.md` | Matches **Password Protection Policy**. 16 of 18 requirements aligned, 2 legitimately missing (the two the document never had reason to address), 0 contradicted. |
| `violations_procedure.md` | Matches **Password Protection Policy**. 3 aligned, 6 contradicted, 9 missing. Contains 7 deliberately planted violations (5 contradictions, 2 omissions) plus 2 additional correct-but-unplanned findings — see `test_inputs/violations_answer_key.md` for the full breakdown, sentence by sentence, of what was planted and why. |
| `unrelated_document.md` | A lab-equipment checkout procedure, plainly outside security policy. No standard scores above the match threshold; the app reports no match with a reason instead of forcing a guess. |
| `compliant_clean_desk_procedure.{md,pdf,docx}` | Format-regression fixtures: the same content in three formats, matching **Clean Desk Policy** (11/13 aligned, 2 legitimately missing) identically across all three — confirms upload format doesn't change the result when the matched standard has no close sibling. |
| `compliant_procedure.{pdf,docx}` | The `compliant_procedure.md` content, re-flowed with markdown headings stripped. **Known, documented exception, not a bug in the usual sense:** this flattened text stably matches the wrong sibling standard, **Password Construction Guidelines**, instead of Password Protection Policy — kept as an explicit `xfail` in `backend/tests/test_known_issues.py` rather than hidden. See `DECISIONS.md` for the investigation. |

Upload any of the `.md`/`.pdf`/`.docx` files at `http://localhost:8000` to see the corresponding
UI state; screenshots of the three core outcomes are in `screenshots/`.

## Model

- Chat: `gpt-4.1` (JSON mode, temperature 0)
- Embeddings: `text-embedding-ada-002` (1536 dimensions)
- Both via the **Stanford AI API Gateway** (`https://aiapi-dev.stanford.edu/v1`, OpenAI-compatible), configured with `LLM_MODEL` / `EMBED_MODEL` in `.env`.

## AI-assistant usage

- **Claude (chat)** was used for design planning (`DESIGN_PLAN.md`) before the build started. A
  status summary was prepared partway through the build (covering what had been implemented,
  tested, and corrected so far) for review in that same chat session, which had the original
  planning context but hadn't seen anything since.
- **Claude Code** did the implementation, stage by stage, from `BUILD_PLAN.md`, and later built out
  the `pytest` unit test suite and the PDF/DOCX format-regression fixtures.
- **A Chrome browser automation skill** was used to actually drive the UI in a real browser for
  Stage 5 (upload each test document, confirm each state renders, and adversarially test the
  escaping behavior with an injected script payload) rather than only testing the API directly.

This wasn't a rubber-stamp process. Rithika reviewed the regression outputs at each stage, made
the actual design decisions when tradeoffs came up (which fix to take, what to leave as a known
limitation, how to scope a test), and wrote `DESIGN.md` herself, in her own words, from
`DECISIONS.md` and the planning sections of `DESIGN_PLAN.md` — it was not generated. Every stage
ended with a summary of what was built, the verify output, and one thing the assistant was unsure
about or surprised by, recorded before moving to the next stage; every decision and correction —
including real mistakes the assistant caught in its own earlier work (curation errors, a
formatting bug, an error-handling gap) — is logged chronologically in `DECISIONS.md`.

## Known limitations

See `DESIGN.md` for the full list, with the reasoning behind each — including the match
threshold's empirical calibration, the standards library's curation gaps, and the sibling-policy
confusion noted in the test inputs table above. `DECISIONS.md` has the raw, chronological record
each one was drawn from.
