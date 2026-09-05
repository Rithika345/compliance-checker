"""Thin client for the Stanford AI gateway: one function for JSON chat calls,
one for embeddings. Both retry once on 429/5xx and never log request or
response content, only status codes and timings.
"""

import json
import logging
import time

import numpy as np
from openai import APIStatusError, OpenAI

from app.config import (
    EMBED_MODEL,
    LLM_BASE_URL,
    LLM_MODEL,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    STANFORD_API_KEY,
)

logger = logging.getLogger(__name__)

_client = OpenAI(
    api_key=STANFORD_API_KEY,
    base_url=LLM_BASE_URL,
    timeout=REQUEST_TIMEOUT_SECONDS,
    max_retries=0,  # we implement one explicit retry ourselves
)


def _is_retryable(exc: Exception) -> bool:
    if not isinstance(exc, APIStatusError):
        return False
    return exc.status_code == 429 or exc.status_code >= 500


def _call_with_one_retry(fn, label: str):
    start = time.monotonic()
    try:
        result = fn()
        logger.info("%s ok elapsed=%.2fs", label, time.monotonic() - start)
        return result
    except Exception as exc:  # noqa: BLE001 - deciding retry vs raise below
        if not _is_retryable(exc):
            logger.info("%s failed elapsed=%.2fs error=%s", label, time.monotonic() - start, type(exc).__name__)
            raise
        logger.info(
            "%s retryable failure status=%s elapsed=%.2fs, retrying after %ss",
            label,
            getattr(exc, "status_code", "?"),
            time.monotonic() - start,
            RETRY_BACKOFF_SECONDS,
        )
        time.sleep(RETRY_BACKOFF_SECONDS)
        start = time.monotonic()
        result = fn()
        logger.info("%s ok on retry elapsed=%.2fs", label, time.monotonic() - start)
        return result


def chat_json(system: str, user: str, model: str = LLM_MODEL) -> dict:
    """Call the chat model in JSON mode and return the parsed object.

    JSON mode guarantees the response is valid JSON; it does not guarantee
    the shape matches any particular schema, so callers must still validate
    the result (with Pydantic) before trusting it.
    """

    def _do_call():
        return _client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

    response = _call_with_one_retry(_do_call, label=f"chat_json model={model}")
    content = response.choices[0].message.content
    return json.loads(content)


def embed(texts: list[str], model: str = EMBED_MODEL) -> np.ndarray:
    """Embed a list of strings in one API call, returning shape (len(texts), dims)."""

    def _do_call():
        return _client.embeddings.create(model=model, input=texts)

    response = _call_with_one_retry(_do_call, label=f"embed model={model} n={len(texts)}")
    vectors = [item.embedding for item in response.data]
    return np.array(vectors, dtype=np.float32)
