"""Thin client for the Stanford AI gateway: one function for JSON chat calls,
one for embeddings. Both retry once on 429/5xx and never log request or
response content, only status codes and timings.
"""

import json
import logging
import threading
import time
from typing import TypeVar

import numpy as np
from openai import APIStatusError, OpenAI
from pydantic import BaseModel, ValidationError

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


class UsageAccumulator:
    """Thread-safe running total of LLM calls and token usage for one request.

    Optional at every call site (defaults to None, meaning "don't
    accumulate, just log"); a caller that wants a per-request total (e.g.
    the /api/evaluate route) creates one and passes it down.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def record(self, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _log_and_record_usage(purpose: str, usage_obj, usage: "UsageAccumulator | None") -> None:
    prompt_tokens = getattr(usage_obj, "prompt_tokens", 0) or 0
    completion_tokens = getattr(usage_obj, "completion_tokens", 0) or 0
    logger.info(
        "llm usage purpose=%s prompt_tokens=%d completion_tokens=%d total_tokens=%d",
        purpose,
        prompt_tokens,
        completion_tokens,
        prompt_tokens + completion_tokens,
    )
    if usage is not None:
        usage.record(prompt_tokens, completion_tokens)


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


def chat_json(
    system: str, user: str, purpose: str, model: str = LLM_MODEL, usage: UsageAccumulator | None = None
) -> dict:
    """Call the chat model in JSON mode and return the parsed object.

    JSON mode guarantees the response is valid JSON; it does not guarantee
    the shape matches any particular schema, so callers must still validate
    the result (with Pydantic) before trusting it. `purpose` labels this
    call for the usage log (e.g. "extract", "plausibility", "verify batch 2").
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
    _log_and_record_usage(purpose, response.usage, usage)
    content = response.choices[0].message.content
    if content is None:
        raise ValueError("Chat completion returned no content (possibly filtered by the gateway).")
    return json.loads(content)


ModelT = TypeVar("ModelT", bound=BaseModel)


def chat_json_validated(
    system: str,
    user: str,
    schema: type[ModelT],
    purpose: str,
    model: str = LLM_MODEL,
    usage: UsageAccumulator | None = None,
) -> ModelT:
    """Call chat_json and validate the result against `schema`.

    On a schema mismatch or a response that isn't valid JSON at all, retry
    once with the error appended to the prompt (every LLM-JSON call site
    needs this same recovery, so it lives here instead of being
    reimplemented per caller). If the retry also fails, the error
    propagates to the caller.
    """
    try:
        return schema.model_validate(chat_json(system, user, purpose, model=model, usage=usage))
    except (ValidationError, ValueError) as first_error:
        if isinstance(first_error, ValidationError):
            locs = [".".join(str(p) for p in e["loc"]) for e in first_error.errors()]
            detail = f"problem fields: {locs}"
        else:
            detail = f"the response was not valid JSON ({type(first_error).__name__})"
        retry_user = (
            user
            + f"\n\nYour previous JSON response did not match the required schema ({detail}). "
            "Return a corrected JSON object matching the shape exactly. Respond with a JSON object."
        )
        return schema.model_validate(chat_json(system, retry_user, purpose, model=model, usage=usage))


def embed(texts: list[str], purpose: str, model: str = EMBED_MODEL, usage: UsageAccumulator | None = None) -> np.ndarray:
    """Embed a list of strings in one API call, returning shape (len(texts), dims)."""

    def _do_call():
        return _client.embeddings.create(model=model, input=texts)

    response = _call_with_one_retry(_do_call, label=f"embed model={model} n={len(texts)}")
    _log_and_record_usage(purpose, response.usage, usage)
    vectors = [item.embedding for item in response.data]
    return np.array(vectors, dtype=np.float32)
