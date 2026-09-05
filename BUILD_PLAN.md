# BUILD_PLAN.md

Deadline 6:00 PM. Start ~11:05 AM. Each stage ends in something that runs. Each stage has a "Read this" file and "Explain it back" questions for Rithika. Do not start the next stage until the verify passes and the questions can be answered without notes.

If behind at any checkpoint, see "Cut list" at the bottom. Decide about asking Smruti for an extension by 4:00 PM, not later.

Keep `DECISIONS.md` open. Append one line each time a choice is made. First line goes in during Stage 0.

---

## Stage 0: Environment and unknowns (11:05 to 11:30)

Tasks:
1. `git init`, write `.gitignore` (`.env`, `venv/`, `__pycache__/`, `node_modules/`, `.DS_Store`), commit it. First commit is only the gitignore.
2. Create `.env` with `STANFORD_API_KEY=...`, `LLM_MODEL=gpt-4.1`, `EMBED_MODEL=text-embedding-ada-002`. Create `.env.example` with empty values. `git status` must show `.env` untracked.
3. `python3 -m venv venv && source venv/bin/activate`, install the stack from `CLAUDE.md`, then `python -c "import fastapi, pydantic, numpy, pypdf, docx, openai"`. If anything fails on 3.14, stop and report.
4. Clone the SANS mirror into a temp folder. Copy 25 to 30 policy PDFs (skip files 22 through 35 and anything obviously not a policy) into `standards_src/`.
5. Run `pypdf` on two PDFs and print the first 800 characters of each. Confirm real text, not empty. Note what the boilerplate looks like (this informs the ingest prompt).
6. Write `app/config.py` and `app/llm.py` with `chat_json(system, user, model) -> dict` and `embed(texts) -> np.ndarray`. 60s timeout, one retry on 429/5xx with a 2s backoff. Smoke test both against the gateway.

Verify:
```
python -c "from app.llm import chat_json, embed; print(chat_json('You are a test.', 'Respond with a JSON object {\"ok\": true}')); print(embed(['hello']).shape)"
```
Expected: `{'ok': True}` and `(1, 1536)`.

Read this: `app/llm.py`.

Explain it back:
- What does JSON mode guarantee and what does it not guarantee? (Shape is valid JSON; content is not guaranteed to match our schema, which is why Pydantic exists.)
- Why is the key read from the environment and not from a config file?

---

## Stage 1: Ingest (11:30 to 12:45)

Tasks:
1. `app/schemas.py`: copy every model from `DESIGN_PLAN.md` section 4.
2. `app/prompts.py`: `EXTRACT_REQUIREMENTS` prompt. Must say: pull obligations only from the policy body, ignore Overview/Purpose/Policy Compliance/Definitions/Revision History, one atomic testable requirement per item, keep the original wording where possible, return the exact JSON shape, respond with a JSON object.
3. `ingest.py`: for each PDF in `standards_src/`, extract text, call the LLM, validate as `StandardExtraction`, save `standards_cache/<slug>.json`. Skip if the JSON exists unless `--force`. Print title and requirement count per standard. Then embed all requirement texts (prefixed `"{title}: "`) plus one profile chunk per standard, save `index.npy` and `index_meta.json`.
4. Run it. While it runs (5 to 10 minutes of API calls), write the three test inputs in `test_inputs/` (see Stage 4 for what they need to contain; writing them now saves time later).
5. Open two cached JSON files and read the requirements. If they include boilerplate ("this policy is reviewed annually") or non-atomic items, tighten the prompt and re-run those two with `--force`.
6. Commit `standards_cache/` and `standards_src/`.

Verify:
```
python ingest.py
ls standards_cache/*.json | wc -l      # >= 20
python -c "import numpy as np; print(np.load('standards_cache/index.npy').shape)"
```

Read this: `ingest.py` and one cached JSON.

Explain it back:
- Why does the boilerplate never make it into the index, and why does that matter for retrieval?
- Why is the cache committed to the repo?
- What is a profile chunk and what does it catch that requirement chunks miss?

---

## Stage 2: Retrieval and no-match (12:45 to 1:45)

Tasks:
1. `app/extract.py`: content sniffing (PDF magic bytes, DOCX zip check, UTF-8 decode), 10 MB cap, in-memory extraction for all four types, 200-char minimum, paragraph chunking to ~300 words with `chunk_index`.
2. `app/retrieval.py`: load index at import time into normalized numpy arrays; `score_standards(doc_chunks) -> list[CandidateScore]` using mean of top-3 per standard; `plausibility_check(candidate, doc_text) -> PlausibilityCheck` via one LLM call; `match_document(doc_text, doc_chunks) -> MatchResult` that applies gate 1 then gate 2.
3. `tests/print_scores.py`: for each of the three test inputs, print the full sorted score vector (all standards). Run it. Look at where the compliant doc, the violations doc, and the unrelated doc land. Set `MATCH_THRESHOLD` in `config.py` in the gap, with a comment recording the three observed top scores. Append to `DECISIONS.md`.
4. If the two related test docs match the wrong sibling policy (e.g. Password Construction instead of Password Protection), switch gate 2 to "here are the top 3 candidates, pick one or none." Record the change.

Verify:
```
python tests/print_scores.py
```
Expected: compliant and violations docs match the intended standard and pass gate 2; unrelated doc fails gate 1 or gate 2 and returns `matched=false` with a reason.

Read this: `app/retrieval.py`.

Explain it back:
- What does "mean of the top 3 chunk similarities" protect against compared to taking the max?
- Why are there two gates, and what would go wrong with only the threshold? With only the LLM?
- Where did the threshold number come from? (Answer: printed score vectors on three inputs; it is empirical and n=3, and you will say so.)

---

## Stage 3: Verification and the route (1:45 to 3:15)

Tasks:
1. `app/prompts.py`: `VERIFY_SYSTEM` and `VERIFY_USER` templates. Must include: the four verdict definitions in one line each; quotes copied verbatim; `missing` must include the closest related passage as `closest_related` (or the scope paragraph if nothing is related); the document is untrusted data between delimiters and any instructions inside it are to be ignored; one finding per requirement id; respond with a JSON object matching `FindingBatch`.
2. `app/verify.py`: `verify_requirements(doc_text, requirements) -> list[FindingOut]`. Batches of 8, `ThreadPoolExecutor(max_workers=4)`, per-batch validate with one retry, per-batch degrade to flagged on second failure, quote verifier with whitespace and curly-quote normalization, downgrade rule, coverage reconcile.
3. `app/main.py`: `POST /api/evaluate`, `GET /api/standards`, `GET /api/health`, startup checks, CORS for localhost, plain-sentence error responses, per-stage timing logs.
4. Run `uvicorn app.main:app --reload`. Open `http://localhost:8000/docs`. Upload all three test inputs through the Swagger UI. Read the JSON.
5. Pick one `contradicted` finding from the violations doc. Trace it: which requirement, what the LLM said, what quote, did the verifier pass it. This is the walkthrough finding. Write its requirement id in `DECISIONS.md`.

Verify:
```
curl -s -F "file=@test_inputs/violations_procedure.md" http://localhost:8000/api/evaluate | python -m json.tool | head -60
```
Expected: `matched=true`, counts with all four keys, at least one `contradicted` with a verified quote.

Read this: `app/verify.py`, especially the quote verifier and the reconcile function.

Explain it back:
- Walk the contradicted finding from requirement to screen without looking.
- What happens if the LLM invents a quote? What happens if it skips a requirement? What happens if a batch returns garbage twice?
- Why is the endpoint synchronous and what would change at scale?

---

## Stage 4: Test inputs, answer key, regression (3:15 to 3:50)

The three inputs should already be drafted from Stage 1. Now finalize them against the actual cached requirements.

1. `compliant_procedure.md`: a realistic team procedure (pick one standard with 15 to 30 requirements, e.g. Password Protection, Remote Access, or Clean Desk) that satisfies nearly every requirement in the cached JSON. A few requirements can be legitimately missing so the report is not all green.
2. `violations_procedure.md`: same standard, 5 to 7 planted violations. Mix contradictions (says the opposite) and omissions (silent on a requirement). Make contradictions quotable: one clear sentence each.
3. `violations_answer_key.md`: table with requirement id, planted violation type, the sentence in the doc that violates it, expected verdict.
4. `unrelated_document.md`: something plainly outside security policy, e.g. a lab equipment checkout procedure or a cafe opening checklist. Should be procedure-shaped so the no-match is a real test, not a trivial one.
5. `tests/run_regression.py`: runs all three through the pipeline (call the functions directly, not HTTP), asserts match/no-match, and for the violations doc prints each planted requirement id with expected vs actual verdict and a PASS/FAIL. Aim for 5 of 5+ caught; record the real number honestly.

Verify:
```
python tests/run_regression.py
```

Read this: `violations_answer_key.md` next to the regression output.

Explain it back:
- Which planted violations were caught, which were not, and what is your best guess why?
- How would you turn this script into an accuracy measurement at scale? (See `DESIGN_PLAN.md` section 7.)

---

## Stage 5: Stage B frontend (3:50 to 4:35)

1. `static/index.html`, served at `/`. Sections: file input + Evaluate button; loading spinner with "this takes 10 to 20 seconds"; error banner for 4xx; match banner (title, score to 2 decimals, plausibility reason, runner-up); no-match panel with the reason; four count tiles; findings list grouped by verdict, each showing section, requirement text, verdict badge, quotes with a "verified" or "not verified" marker, rationale, confidence.
2. No framework, no build step. Fetch to `/api/evaluate`. Escape all text before inserting into the DOM (findings contain user document text).
3. Run the three inputs through the browser. Screenshot each for the README.

Verify: open `http://localhost:8000/`, upload all three, all three states render correctly.

Read this: skim `index.html` once; know where the fetch call and the render function are.

Explain it back:
- Why is text escaped before rendering? (User-controlled content in the DOM.)
- Why plain HTML served by FastAPI instead of a separate frontend server? (One process for the grader, no CORS, spec allows it.)

---

## Stage 6: README, DESIGN.md, disclosure (4:35 to 5:20)

1. `README.md`: what it is, prerequisites (Python 3.11+, tested on 3.14 macOS), setup (venv, pip install, `.env` from `.env.example`), run (`uvicorn`), open `http://localhost:8000`, the three test inputs and expected results, model used (`gpt-4.1` and `text-embedding-ada-002` via Stanford AI API Gateway), AI-assistant usage (Claude for design planning, Claude Code for implementation, what was human-reviewed and how), known limitations.
2. `DESIGN.md`: Rithika writes it from `DECISIONS.md` and `DESIGN_PLAN.md` sections 2, 3, 6, 7, 8, 9. In her words. Claude Code fixes grammar only if asked.
3. Fresh clone test: `git clone` into a new folder, follow the README literally, time it. Fix whatever breaks.
4. Final `git status` check: `.env` untracked. `git log` has no key. Push.

Verify: a fresh clone runs the compliant doc end to end in under 10 minutes of wall clock from `git clone`.

---

## 5:20 to 6:00: Buffer

- Fix anything the fresh-clone test broke.
- 5:30, ten minutes: walkthrough dry run out loud. Upload violations doc, pick the finding from Stage 3, trace it end to end, explain no-match on the unrelated doc, state two limitations unprompted.
- Stage C (Vite + React) only if the buffer is empty at 5:40. Realistically it will not be. That is fine.

---

## Cut list (in order, if behind)

1. Stage C. Already optional.
2. Parallel batches (run sequentially; slower but simpler).
3. DOCX support (keep PDF/TXT/MD; note it in README). Spec lists DOCX, so only cut this if truly stuck.
4. `GET /api/standards` endpoint.
5. Screenshots in README.

Never cut: quote verifier, coverage reconcile, no-match path, answer key, README run instructions, the `.env` rule.

---

## Understanding checkpoints, collected

You should be able to answer all of these by 5:30 without notes:

1. Draw the five-box pipeline.
2. Why retrieval before the LLM, and why not brute force.
3. How a standard becomes requirements, and why boilerplate is excluded.
4. How no-match is decided, where the threshold came from, and why there is an LLM gate.
5. Why the LLM output is validated with Pydantic, and what happens on failure.
6. Why quotes are verified, and what happens when one fails.
7. One contradicted finding, traced end to end.
8. Three things you would build next and how you would measure accuracy at scale.
9. Two limitations, stated before being asked.
