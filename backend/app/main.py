"""FastAPI app: ties extraction, retrieval, and verification into the
/api/evaluate route. Runs synchronously and entirely in memory -- an upload
is never written to disk, and its filename is never used in a filesystem
path (only echoed back in the JSON response).
"""

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import APIConnectionError, APIStatusError, APITimeoutError

from app.config import LLM_MODEL, MAX_UPLOAD_BYTES, UPLOAD_READ_CHUNK_BYTES
from app.extract import UnsupportedDocument, chunk_text, extract_text
from app.llm import UsageAccumulator
from app.retrieval import get_requirements, list_standards_summary, match_document
from app.schemas import EvaluateResponse, HealthStatus, StandardSummary
from app.verify import build_counts, verify_requirements

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _log_usage_summary(usage: UsageAccumulator) -> None:
    logger.info(
        "evaluate token summary calls=%d prompt_tokens=%d completion_tokens=%d total_tokens=%d",
        usage.calls,
        usage.prompt_tokens,
        usage.completion_tokens,
        usage.total_tokens,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Standards and the API key are already loaded/validated at import time
    # (app.retrieval, app.config each fail loudly on their own); this just
    # confirms and logs it once the app is actually up.
    standards = list_standards_summary()
    logger.info("startup standards_loaded=%d model=%s", len(standards), LLM_MODEL)
    yield


app = FastAPI(title="Document Compliance Checker", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5173",  # Stage C (Vite), if it happens
        "http://127.0.0.1:5173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> HealthStatus:
    standards = list_standards_summary()
    return HealthStatus(ok=True, standards_loaded=len(standards), model=LLM_MODEL)


@app.get("/api/standards")
def standards() -> list[StandardSummary]:
    return [StandardSummary(**summary) for summary in list_standards_summary()]


async def _read_upload(file: UploadFile) -> bytes:
    """Read the upload in bounded chunks so an oversized file is rejected
    before it is ever fully buffered in memory.
    """
    data = bytearray()
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="File exceeds the 10 MB upload limit.")
    return bytes(data)


@app.post("/api/evaluate")
async def evaluate(file: UploadFile = File(...)) -> EvaluateResponse:
    start = time.monotonic()
    document_name = file.filename or "uploaded document"
    data = await _read_upload(file)
    usage = UsageAccumulator()

    # extract_text/chunk_text/match_document/verify_requirements are all
    # synchronous and can each take seconds (network calls to the LLM
    # gateway); running them in a worker thread keeps this route from
    # blocking every other concurrent request on the event loop.
    try:
        doc_text = await run_in_threadpool(extract_text, data)

        doc_chunks = await run_in_threadpool(chunk_text, doc_text)

        retrieval_start = time.monotonic()
        match = await run_in_threadpool(match_document, doc_text, doc_chunks, usage)
        retrieval_elapsed = time.monotonic() - retrieval_start

        if not match.matched:
            logger.info(
                "evaluate matched=false retrieval=%.2fs total=%.2fs",
                retrieval_elapsed,
                time.monotonic() - start,
            )
            _log_usage_summary(usage)
            return EvaluateResponse(
                document_name=document_name,
                match=match,
                counts=build_counts([]),
                findings=[],
                model=LLM_MODEL,
            )

        requirements = get_requirements(match.standard_id)
        verify_start = time.monotonic()
        findings = await run_in_threadpool(verify_requirements, doc_text, requirements, usage)
        verify_elapsed = time.monotonic() - verify_start

        logger.info(
            "evaluate matched=true standard=%s retrieval=%.2fs verify=%.2fs total=%.2fs",
            match.standard_id,
            retrieval_elapsed,
            verify_elapsed,
            time.monotonic() - start,
        )
        _log_usage_summary(usage)
        return EvaluateResponse(
            document_name=document_name,
            match=match,
            counts=build_counts(findings),
            findings=findings,
            model=LLM_MODEL,
        )
    except UnsupportedDocument as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except (APIConnectionError, APITimeoutError) as exc:
        logger.warning("evaluate failed due to LLM gateway error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="The language model service is unavailable. Try again in a moment."
        ) from exc
    except APIStatusError as exc:
        if exc.status_code == 429 or exc.status_code >= 500:
            logger.warning(
                "evaluate failed due to LLM gateway error: %s status=%s", type(exc).__name__, exc.status_code
            )
            raise HTTPException(
                status_code=503, detail="The language model service is unavailable. Try again in a moment."
            ) from exc
        logger.warning(
            "evaluate failed due to LLM gateway rejection: %s status=%s message=%s",
            type(exc).__name__,
            exc.status_code,
            exc.message,
        )
        raise HTTPException(
            status_code=422, detail="The document could not be processed by the language model service."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - never leak an internal error to the client
        logger.info("evaluate failed error=%s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="An internal error occurred while evaluating the document.") from exc
