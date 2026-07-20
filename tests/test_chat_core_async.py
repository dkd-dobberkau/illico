"""Tests für answer_stream_async (Web-Streaming-Pfad)."""
from unittest.mock import patch

import pytest

import illico_chat_core


@pytest.mark.asyncio
async def test_answer_stream_async_yields_chunks():
    async def fake_stream(model, messages, system=None, max_tokens=1000):
        for text in ["Hallo ", "Welt"]:
            yield text

    with patch("illico_chat_core.illico_llm.call_stream", side_effect=fake_stream):
        out = []
        async for text in illico_chat_core.answer_stream_async(
            question="Was?",
            relevant_articles=["a.md"],
            articles={"a.md": "Inhalt"},
            history=[],
            system="sys",
            model="test-model",
        ):
            out.append(text)

    assert out == ["Hallo ", "Welt"]


@pytest.mark.asyncio
async def test_answer_stream_async_passes_model():
    async def fake_stream(model, messages, system=None, max_tokens=1000):
        yield "ok"

    with patch("illico_chat_core.illico_llm.call_stream", side_effect=fake_stream) as m:
        async for _ in illico_chat_core.answer_stream_async(
            question="?",
            relevant_articles=[],
            articles={},
            history=[],
            system="sys",
            model="my-model",
        ):
            pass

    assert m.call_args.args[0] == "my-model"


@pytest.mark.asyncio
async def test_answer_stream_async_includes_graph_context():
    captured = {}

    async def fake_stream(model, messages, system=None, max_tokens=1000):
        captured["messages"] = messages
        yield "ok"

    with patch("illico_chat_core.illico_llm.call_stream", side_effect=fake_stream):
        async for _ in illico_chat_core.answer_stream_async(
            question="Wer ist Partner?",
            relevant_articles=["a.md"],
            articles={"a.md": "Inhalt"},
            history=[],
            system="sys",
            model="m",
            graph_context="## Wissensgraph\n### Beziehungen\n- dkd ist Partner von b13",
        ):
            pass

    last_user = captured["messages"][-1]["content"]
    assert "dkd ist Partner von b13" in last_user
