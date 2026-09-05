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

## The three test inputs

`test_inputs/` has three documents used throughout development to exercise the three possible
outcomes:

| File | Expected result |
|---|---|
| `compliant_procedure.md` | Matches **Password Protection Policy**. 16 of 18 requirements aligned, 2 legitimately missing (the two the document never had reason to address), 0 contradicted. |
| `violations_procedure.md` | Matches **Password Protection Policy**. 3 aligned, 6 contradicted, 9 missing. Contains 7 deliberately planted violations (5 contradictions, 2 omissions) plus 2 additional correct-but-unplanned findings — see `test_inputs/violations_answer_key.md` for the full breakdown, sentence by sentence, of what was planted and why. |
| `unrelated_document.md` | A lab-equipment checkout procedure, plainly outside security policy. No standard scores above the match threshold; the app reports no match with a reason instead of forcing a guess. |

Upload any of the three at `http://localhost:8000` to see the corresponding UI state; screenshots
of all three are in `screenshots/`.

## Model

- Chat: `gpt-4.1` (JSON mode, temperature 0)
- Embeddings: `text-embedding-ada-002` (1536 dimensions)
- Both via the **Stanford AI API Gateway** (`https://aiapi-dev.stanford.edu/v1`, OpenAI-compatible), configured with `LLM_MODEL` / `EMBED_MODEL` in `.env`.

## AI-assistant usage

Claude was used for design planning (`DESIGN_PLAN.md`) before the build started, and Claude Code
did the implementation, stage by stage, from `BUILD_PLAN.md`.

Every stage ended with a summary of what was built, the verify output, and one thing the
assistant was unsure about or surprised by, recorded before moving to the next stage. Decisions
made along the way — including corrections to its own earlier work — are logged chronologically
in `DECISIONS.md`. This wasn't a rubber-stamp process: over the course of the build, the
assistant was asked to double-check its own assumptions, and that surfaced two real mistakes in
the standards library curation (one file wrongly excluded, one wrongly kept over a better
alternative) that got caught and fixed before they became invisible bugs. It flagged tradeoffs
it made unilaterally (like how to handle a finding with no supporting quote at all) rather than
silently deciding and moving on, and it asked before taking actions with consequences outside the
repo itself — killing an unrelated stray process occupying port 8000, and clarifying who should
actually write this project's design document versus its own build log.

`DESIGN.md` was written by hand, from `DECISIONS.md` and the planning sections of
`DESIGN_PLAN.md` — not generated.

## Known limitations

- **The match threshold is calibrated on 3 test documents.** `MATCH_THRESHOLD = 0.86` sits in a
  gap only 0.0097 wide between the lowest score that must pass and the highest that must fail. A
  fourth real-world document could easily land inside that gap and get the wrong match/no-match
  outcome. This is empirical, not a principled number.
- **Upload content-sniffing can't reject arbitrary binary garbage** that isn't a PDF or a DOCX
  zip file. The intended fallback (decode as UTF-8, then latin-1, then reject) can never actually
  reject anything, because latin-1 can decode any byte value — this is true of any correct
  implementation of that rule, not just this one. In practice, the 200-character minimum and then
  retrieval's no-match gate still catch nonsense uploads, just less cleanly than an explicit
  "unrecognized file type" error would.
- **The standards library was curated by hand from filenames**, mostly without reading full PDF
  content. Two of the ~49 candidate files were checked directly and both turned out to be
  mis-categorized (one wrongly excluded as a form, one wrongly kept over a better alternative);
  the other ~19 excluded files were not checked the same way, so it's possible more of them are
  mis-categorized too.
- **Requirement extraction is one uncorrected LLM pass per standard.** The 30 cached
  extractions were spot-checked, not exhaustively reviewed by a human.
- Prompt injection mitigations are partial: the verification prompt tells the model to treat the
  uploaded document as untrusted data, not instructions, but this is a mitigation, not a
  guarantee. Separately, the DOM-escaping side of this (an injected `<script>` or similar payload
  appearing in a quote never becoming executable markup in the browser) has been adversarially
  tested and confirmed safe.
- Whole-document-per-batch verification limits practical document length; a top-k-chunks
  fallback for very long documents (>~60k characters) is designed but not built.
- Tested on macOS with Python 3.14 only.
- Non-English documents are out of scope.
