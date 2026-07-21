"""
illico_llm — thin LiteLLM adapter.

The only file in illico that imports litellm. All other modules call
call_sync() or call_stream() — never litellm directly.

Env vars:
  ILLICO_ROUTER_MODEL  — model for routing (cheap/fast).
  ILLICO_ANSWER_MODEL  — model for answering, compile, suggestions.
  ILLICO_MODEL         — legacy single-model var; fallback for both above.

Default for all: anthropic/claude-haiku-4-5-20251001
"""
import asyncio
import logging
import os
import time
from typing import AsyncGenerator

import litellm

_DEFAULT = "anthropic/claude-haiku-4-5-20251001"
_log = logging.getLogger(__name__)


class LLMAuthError(Exception):
    """LLM provider authentication failed."""


_LEGACY = os.environ.get("ILLICO_MODEL")

ROUTER_MODEL: str = os.environ.get("ILLICO_ROUTER_MODEL") or _LEGACY or _DEFAULT
ANSWER_MODEL: str = os.environ.get("ILLICO_ANSWER_MODEL") or _LEGACY or _DEFAULT

# Defensive getattr: both names verified in litellm >=1.40 but guard against
# future removals without crashing at import time.
_RETRYABLE = tuple(filter(None, [
    getattr(litellm, "RateLimitError", None),
    getattr(litellm, "APIConnectionError", None),
    getattr(litellm, "ServiceUnavailableError", None),
    getattr(litellm, "Timeout", None),
    # Anthropic 529 overloaded_error surfaces as InternalServerError (HTTP 5xx);
    # transient/retryable per Anthropic docs. Without this, large compiles die
    # mid-run on provider overload (Deutz-Tenant never compiled).
    getattr(litellm, "InternalServerError", None),
]))


def _build_messages(messages: list[dict], system: str | None) -> list[dict]:
    if system:
        return [{"role": "system", "content": system}] + list(messages)
    return list(messages)


def call_sync(
    model: str,
    messages: list[dict],
    system: str | None = None,
    max_tokens: int = 2000,
    retries: int = 3,
) -> str:
    """Synchronous LLM call with exponential-backoff retry on transient errors."""
    msgs = _build_messages(messages, system)
    for attempt in range(retries):
        try:
            response = litellm.completion(model=model, messages=msgs, max_tokens=max_tokens)
            content = response.choices[0].message.content or ""
            if response.choices[0].finish_reason == "length":
                _log.warning(
                    "LLM response truncated at token limit (model=%s, max_tokens=%d)",
                    model,
                    max_tokens,
                )
            return content
        except litellm.AuthenticationError as exc:
            raise LLMAuthError(str(exc)) from exc
        except _RETRYABLE as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt * 5)


async def call_stream(
    model: str,
    messages: list[dict],
    system: str | None = None,
    max_tokens: int = 1000,
    retries: int = 3,
) -> AsyncGenerator[str, None]:
    """Async streaming LLM call. Yields non-empty text chunks.

    Retries transient errors before the first chunk is sent; once text is
    flowing a retry would duplicate already-yielded content, so errors at
    that point are translated and re-raised.
    """
    msgs = _build_messages(messages, system)
    for attempt in range(retries):
        yielded = False
        try:
            response = await litellm.acompletion(
                model=model, messages=msgs, max_tokens=max_tokens, stream=True
            )
            async for chunk in response:
                text = chunk.choices[0].delta.content or ""
                if text:
                    yielded = True
                    yield text
            return
        except litellm.AuthenticationError as exc:
            raise LLMAuthError(str(exc)) from exc
        except _RETRYABLE as exc:
            if attempt == retries - 1 or yielded:
                raise
            await asyncio.sleep(2 ** attempt * 5)
