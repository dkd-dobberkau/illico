"""Tests für get_prompts() und Prompts-Dataclass."""
import pytest
from illico_compile import Prompts, get_prompts


def test_get_prompts_returns_prompts_instance():
    assert isinstance(get_prompts("de"), Prompts)


def test_get_prompts_de_has_german_content():
    p = get_prompts("de")
    assert "Du bist Illico" in p.inventory
    assert "Du bist Illico" in p.article
    assert "Du bist Illico" in p.lint


def test_get_prompts_en_has_english_content():
    p = get_prompts("en")
    assert "You are Illico" in p.inventory
    assert "You are Illico" in p.article
    assert "You are Illico" in p.lint


def test_get_prompts_none_equals_de():
    assert get_prompts(None) == get_prompts("de")


def test_get_prompts_unknown_lang_falls_back_to_de():
    assert get_prompts("fr") == get_prompts("de")


def test_get_prompts_is_frozen():
    p = get_prompts("de")
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        p.inventory = "hacked"


def test_get_prompts_has_all_fields():
    p = get_prompts("de")
    for field in ("inventory", "merge", "extract", "merge_graph", "canonicalize", "article", "index", "lint"):
        assert getattr(p, field), f"field {field!r} is empty"


def test_get_prompts_has_canonicalize_field():
    from illico_compile import get_prompts
    assert "Synonym" in get_prompts("de").canonicalize or "kanonisch" in get_prompts("de").canonicalize
    assert "clusters" in get_prompts("de").canonicalize
    assert "clusters" in get_prompts("en").canonicalize
