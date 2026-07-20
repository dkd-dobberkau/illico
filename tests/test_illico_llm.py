"""Unit tests for illico_llm — mocks litellm, never makes real API calls."""
from unittest.mock import MagicMock, patch

import pytest

import illico_llm


def _ok_response(text="Hallo"):
    r = MagicMock()
    r.choices[0].message.content = text
    r.choices[0].finish_reason = "stop"
    return r


# ── call_sync ────────────────────────────────────────────────────────────────

def test_call_sync_returns_content():
    with patch("illico_llm.litellm.completion", return_value=_ok_response("Antwort")) as m:
        result = illico_llm.call_sync("test-model", [{"role": "user", "content": "hi"}])
    assert result == "Antwort"
    m.assert_called_once()


def test_call_sync_prepends_system_message():
    with patch("illico_llm.litellm.completion", return_value=_ok_response()) as m:
        illico_llm.call_sync("model", [{"role": "user", "content": "hi"}], system="sys")
    _, kwargs = m.call_args
    msgs = kwargs["messages"]
    assert msgs[0] == {"role": "system", "content": "sys"}
    assert msgs[1] == {"role": "user", "content": "hi"}


def test_call_sync_no_system_passes_messages_unchanged():
    with patch("illico_llm.litellm.completion", return_value=_ok_response()) as m:
        illico_llm.call_sync("model", [{"role": "user", "content": "hi"}])
    _, kwargs = m.call_args
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_call_sync_retries_on_transient_error():
    class FakeRateLimitError(Exception):
        pass

    with patch("illico_llm._RETRYABLE", (FakeRateLimitError,)), \
         patch("illico_llm.litellm.completion") as mock_comp, \
         patch("illico_llm.time.sleep"):
        mock_comp.side_effect = [FakeRateLimitError("rate limit"), _ok_response("ok")]
        result = illico_llm.call_sync("test-model", [{"role": "user", "content": "hi"}])

    assert result == "ok"
    assert mock_comp.call_count == 2


def test_call_sync_does_not_retry_non_retryable():
    with patch("illico_llm.litellm.completion", side_effect=ValueError("bad request")), \
         patch("illico_llm.time.sleep") as mock_sleep:
        with pytest.raises(ValueError):
            illico_llm.call_sync("test-model", [{"role": "user", "content": "hi"}])
    mock_sleep.assert_not_called()


def test_call_sync_raises_after_all_retries_exhausted():
    class FakeRateLimitError(Exception):
        pass

    with patch("illico_llm._RETRYABLE", (FakeRateLimitError,)), \
         patch("illico_llm.litellm.completion", side_effect=FakeRateLimitError("rate limit")), \
         patch("illico_llm.time.sleep"):
        with pytest.raises(FakeRateLimitError):
            illico_llm.call_sync("test-model", [{"role": "user", "content": "hi"}], retries=2)


def test_call_sync_raises_llm_auth_error_on_auth_failure():
    class FakeAuthError(Exception):
        pass

    with patch("illico_llm.litellm.AuthenticationError", FakeAuthError), \
         patch("illico_llm.litellm.completion", side_effect=FakeAuthError("invalid key")):
        with pytest.raises(illico_llm.LLMAuthError):
            illico_llm.call_sync("test-model", [{"role": "user", "content": "hi"}])


# ── call_stream ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_call_stream_yields_chunks():
    async def fake_acompletion(**kwargs):
        async def gen():
            for text in ["Hallo ", "Welt"]:
                chunk = MagicMock()
                chunk.choices[0].delta.content = text
                yield chunk
        return gen()

    with patch("illico_llm.litellm.acompletion", side_effect=fake_acompletion):
        out = []
        async for text in illico_llm.call_stream(
            "test-model", [{"role": "user", "content": "hi"}]
        ):
            out.append(text)

    assert out == ["Hallo ", "Welt"]


@pytest.mark.asyncio
async def test_call_stream_skips_empty_chunks():
    async def fake_acompletion(**kwargs):
        async def gen():
            for text in ["A", "", None, "B"]:
                chunk = MagicMock()
                chunk.choices[0].delta.content = text
                yield chunk
        return gen()

    with patch("illico_llm.litellm.acompletion", side_effect=fake_acompletion):
        out = []
        async for text in illico_llm.call_stream("model", []):
            out.append(text)

    assert out == ["A", "B"]


# ── model env vars ────────────────────────────────────────────────────────────

def test_model_constants_are_strings():
    assert isinstance(illico_llm.ROUTER_MODEL, str)
    assert isinstance(illico_llm.ANSWER_MODEL, str)
    assert len(illico_llm.ROUTER_MODEL) > 0
    assert len(illico_llm.ANSWER_MODEL) > 0
