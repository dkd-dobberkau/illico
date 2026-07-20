"""Test entity matching über aliases."""

from illico_graph import build_article_entity_map


def test_alias_match_returns_canonical_name():
    nodes = [{"id": 1, "name": "Frankfurt am Main", "aliases": ["Frankfurt", "FFM"]}]
    articles = {"stadt.md": "Unser Büro sitzt in Frankfurt."}
    result = build_article_entity_map(articles, nodes)
    assert result["stadt.md"] == ["Frankfurt am Main"]


def test_no_duplicate_canonical_when_name_and_alias_both_present():
    nodes = [{"id": 1, "name": "Frankfurt am Main", "aliases": ["Frankfurt"]}]
    articles = {"a.md": "Frankfurt am Main, kurz Frankfurt."}
    result = build_article_entity_map(articles, nodes)
    assert result["a.md"] == ["Frankfurt am Main"]


def test_node_without_aliases_still_matches_by_name():
    nodes = [{"id": 1, "name": "Berlin"}]
    articles = {"a.md": "Sitz in Berlin."}
    result = build_article_entity_map(articles, nodes)
    assert result["a.md"] == ["Berlin"]
