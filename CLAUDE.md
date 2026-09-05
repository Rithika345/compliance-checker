# CLAUDE.md

Project: Document Compliance Checker (Stanford take-home). Read `DESIGN_PLAN.md` for architecture and `BUILD_PLAN.md` for the staged work order. Follow the stages in order. Do not build ahead.

## Hard rules

1. **The API key never appears in any file except `.env`.** Not in code, not in comments, not in README, not in tests, not in commit messages, not in printed output. Read it from `os.environ["STANFORD_API_KEY"]` via `python-dotenv`. If you ever see the literal key in a file, remove it and say so.
2. `.gitignore` containing `.env`, `venv/`, `__pycache__/`, `node_modules/`, `.DS_Store` is the first commit. Verify with `git status` that `.env` is untracked before every commit.
3. Never log or print document contents or API responses containing document text. Log ids, scores, counts, timings.
4. Uploaded files are processed in memory. Never write an upload to disk. Never use the user-supplied filename in a filesystem path.
5. One stage at a time. After each stage, stop, run the verify command in `BUILD_PLAN.md`, show the output, and wait. Do not start the next stage until told.
6. No new dependencies without saying why. The approved list is in "Stack" below. If something else is needed, propose it and wait.
7. When something fails, say what failed and what you actually observed. Do not guess at causes as if they were confirmed.
8. Do not write `DESIGN.md`. Rithika writes it. You may fix grammar in it when asked.

## Stack

- Python 3.14 (macOS). If a package fails to install on 3.14, report it; the fallback is Python 3.12 via Homebrew.
- Backend: `fastapi`, `uvicorn[standard]`, `python-multipart`, `pydantic>=2`, `openai` (SDK, pointed at the gateway with `base_url`), `numpy`, `pypdf`, `python-docx`, `python-dotenv`, `httpx` (already a dep of openai).
- Dev: `pytest`.
- Frontend Stage B: one static `index.html`, vanilla JS, served by FastAPI at `/`.
- Frontend Stage C (only if instructed): Vite + React.
- No LangChain, no LiteLLM client library, no vector database, no Celery, no Docker.

## LLM gateway

- Base URL: `https://aiapi-dev.stanford.edu/v1` (OpenAI-compatible, LiteLLM proxy).
- Chat model: `gpt-4.1` (env `LLM_MODEL`, default `gpt-4.1`).
- Embedding model: `text-embedding-ada-002` (env `EMBED_MODEL`), 1536 dims.
- JSON mode works: `response_format={"type": "json_object"}` and the prompt must contain the word "JSON". Always include "Respond with a JSON object" in the user message.
- `temperature=0` is accepted.
- Rate limits are unknown. Start with 4 parallel workers; if 429s appear, drop to 1 and report.
- `json_schema` response format is untested. Do not rely on it unless explicitly asked to try.

## Repo layout

```
compliance-checker/
  .env                      (untracked)
  .env.example              (STANFORD_API_KEY=, LLM_MODEL=gpt-4.1, EMBED_MODEL=text-embedding-ada-002)
  .gitignore
  README.md
  DESIGN.md                 (written by Rithika)
  DECISIONS.md              (one line per decision, appended during the build)
  backend/
    app/
      __init__.py
      main.py               FastAPI app, routes, startup checks
      config.py             env loading, thresholds, model names
      schemas.py            all Pydantic models (copy from DESIGN_PLAN.md section 4)
      llm.py                thin client: chat_json(), embed(); retries; timeouts
      extract.py            upload validation + text extraction + chunking
      retrieval.py          index loading, scoring, gates
      verify.py             batching, prompts, post-processing, quote verifier
      prompts.py            prompt strings only
    ingest.py               offline standards ingest
    standards_cache/        committed JSON + index.npy + index_meta.json
    static/index.html       Stage B UI
    tests/
      test_extract.py
      test_retrieval.py
      test_verify.py
      run_regression.py     runs the three test inputs end to end, prints pass/fail
    requirements.txt
  standards_src/            the selected SANS PDFs (committed)
  test_inputs/
    compliant_procedure.md
    violations_procedure.md
    violations_answer_key.md
    unrelated_document.md
```

## Code conventions

- Type hints everywhere. Pydantic models for anything that crosses a boundary (LLM in/out, API in/out).
- Functions small enough that Rithika can read one and explain it. Prefer a clear 15-line function over a clever 5-line one.
- Every module has a 2 to 4 line docstring at the top saying what it does and why it exists.
- Thresholds and magic numbers live in `config.py` with a comment on how they were chosen.
- Errors returned to the client are plain sentences. Never leak stack traces or file paths.
- Timing: log per-stage durations at INFO (`retrieval=0.8s verify=6.2s total=7.9s`).

## After finishing each stage

Post a short block:

```
STAGE N DONE
Files touched: ...
Verify output: (paste)
One thing that surprised me or that I am unsure about: ...
```

Then, when asked "explain X," explain the function in plain language, walking through inputs, the key decision inside, and outputs. Assume the listener will be asked about it in an interview tomorrow.
