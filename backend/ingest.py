"""Offline standards ingest: turns each SANS PDF in standards_src/ into a
cached StandardExtraction JSON, then embeds every requirement (plus one
profile chunk per standard) into a combined index. Run once; results are
committed so the grader never needs an API key to start the app.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
from pydantic import ValidationError
from pypdf import PdfReader

from app.config import MIN_EXTRACTED_CHARS
from app.llm import chat_json, embed
from app.prompts import EXTRACT_REQUIREMENTS_SYSTEM, EXTRACT_REQUIREMENTS_USER_TEMPLATE
from app.schemas import StandardExtraction

BACKEND_ROOT = Path(__file__).resolve().parent
STANDARDS_SRC = BACKEND_ROOT.parent / "standards_src"
STANDARDS_CACHE = BACKEND_ROOT / "standards_cache"
EMBED_BATCH_SIZE = 200  # keeps each embeddings call well under gateway request-size limits


def slugify(filename_stem: str) -> str:
    """'44. password_protection_policy' -> 'password-protection-policy'."""
    name = re.sub(r"^\d+\.\s*", "", filename_stem)
    name = name.lower().replace("_", "-").replace(" ", "-")
    name = re.sub(r"[^a-z0-9-]", "", name)
    return re.sub(r"-+", "-", name).strip("-")


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def describe_error(exc: Exception) -> str:
    """Stringify an exception for printing without leaking document text.

    A ValidationError's default str() embeds the offending input value,
    which can be a chunk of extracted document text, so only field paths
    are reported for that case.
    """
    if isinstance(exc, ValidationError):
        locs = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        return f"ValidationError at fields {locs}"
    return f"{type(exc).__name__}: {exc}"


def extract_with_retry(system: str, user: str) -> StandardExtraction:
    try:
        return StandardExtraction.model_validate(chat_json(system, user))
    except ValidationError as first_error:
        locs = [".".join(str(p) for p in e["loc"]) for e in first_error.errors()]
        retry_user = (
            user
            + f"\n\nYour previous JSON response did not match the required schema "
            f"(problem fields: {locs}). Return a corrected JSON object matching the "
            "shape exactly. Respond with a JSON object."
        )
        return StandardExtraction.model_validate(chat_json(system, retry_user))


def load_or_extract(pdf_path: Path, slug: str, force: bool) -> StandardExtraction:
    cache_path = STANDARDS_CACHE / f"{slug}.json"
    if cache_path.exists() and not force:
        return StandardExtraction.model_validate_json(cache_path.read_text())

    text = extract_pdf_text(pdf_path)
    if len(text) < MIN_EXTRACTED_CHARS:
        raise ValueError(f"extracted text too short ({len(text)} chars); possibly a scanned PDF")

    user_message = EXTRACT_REQUIREMENTS_USER_TEMPLATE.format(document_text=text)
    extraction = extract_with_retry(EXTRACT_REQUIREMENTS_SYSTEM, user_message)

    # Ids are assigned here, not trusted from the LLM: the cache-skip check
    # above has to key off a slug computed before any LLM call is made, so
    # the LLM's own ids (see prompts.py) are only for its internal ordering.
    for i, req in enumerate(extraction.requirements, start=1):
        req.id = f"{slug}-{i:02d}"

    cache_path.write_text(extraction.model_dump_json(indent=2))
    return extraction


def ingest(force: bool, only: set[str] | None) -> list[str]:
    STANDARDS_CACHE.mkdir(parents=True, exist_ok=True)
    pdf_paths = sorted(STANDARDS_SRC.glob("*.pdf"))
    if not pdf_paths:
        print(f"No PDFs found in {STANDARDS_SRC}")
        return []

    slugs = []
    for pdf_path in pdf_paths:
        slug = slugify(pdf_path.stem)
        should_force = force and (only is None or slug in only)
        try:
            extraction = load_or_extract(pdf_path, slug, should_force)
        except Exception as exc:  # noqa: BLE001 - one bad standard must not abort the run
            print(f"FAILED {slug}: {describe_error(exc)}")
            continue
        print(f"{extraction.title} ({slug}): {len(extraction.requirements)} requirements")
        slugs.append(slug)
    return slugs


def build_index(slugs: list[str]) -> None:
    rows_meta = []
    texts = []
    for slug in slugs:
        extraction = StandardExtraction.model_validate_json((STANDARDS_CACHE / f"{slug}.json").read_text())
        for req in extraction.requirements:
            texts.append(f"{extraction.title}: {req.text}")
            rows_meta.append({"standard_id": slug, "chunk_type": "requirement", "requirement_id": req.id})
        texts.append(f"{extraction.title}. {extraction.summary} {extraction.scope}")
        rows_meta.append({"standard_id": slug, "chunk_type": "profile", "requirement_id": None})

    vectors = [
        embed(texts[start : start + EMBED_BATCH_SIZE]) for start in range(0, len(texts), EMBED_BATCH_SIZE)
    ]
    index = np.vstack(vectors)

    np.save(STANDARDS_CACHE / "index.npy", index)
    (STANDARDS_CACHE / "index_meta.json").write_text(json.dumps(rows_meta, indent=2))
    print(f"index: {index.shape[0]} rows, {index.shape[1]} dims")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest SANS PDFs into standards_cache/")
    parser.add_argument("--force", action="store_true", help="re-run LLM extraction even if cached")
    parser.add_argument("--only", type=str, default=None, help="comma-separated slugs to force re-extract")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None

    start = time.monotonic()
    slugs = ingest(force=args.force, only=only)
    if not slugs:
        print("No standards ingested; aborting index build.")
        sys.exit(1)
    build_index(slugs)
    print(f"ingest total={time.monotonic() - start:.1f}s")


if __name__ == "__main__":
    main()
