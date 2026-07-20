from pathlib import Path
from illico_context import WikiContext, single_user_provider, resolve_wiki_dir, list_wiki_languages


def test_single_user_context_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("ILLICO_DATA", str(tmp_path))
    ctx = single_user_provider()
    assert ctx.wiki_prefix == "wiki"
    assert ctx.languages_prefix == "wiki"
    assert ctx.graph_namespace is None
    assert ctx.chat_bucket == "local"
    assert ctx.chat_list_all is False
    assert ctx.unrestricted is True


def test_filter_articles_is_identity():
    ctx = single_user_provider()
    arts = {"_index": "i", "A": "a"}
    assert ctx.filter_articles(arts, {}) == arts


def test_resolve_wiki_dir_lang_fallback(tmp_path):
    (tmp_path / "wiki").mkdir()
    ctx = WikiContext(
        data_dir=tmp_path, wiki_prefix="wiki", languages_prefix="wiki",
        graph_namespace=None, chat_bucket="local", chat_list_all=False,
        label="local", unrestricted=True, filter_articles=lambda a, r: a,
    )
    assert resolve_wiki_dir(ctx, "de") == tmp_path / "wiki"   # -de fehlt → Fallback
    (tmp_path / "wiki-de").mkdir()
    assert resolve_wiki_dir(ctx, "de") == tmp_path / "wiki-de"


def test_list_languages_none_prefix_empty(tmp_path):
    ctx = WikiContext(
        data_dir=tmp_path, wiki_prefix="wiki", languages_prefix=None,
        graph_namespace=None, chat_bucket="admin", chat_list_all=True,
        label="Admin", unrestricted=True, filter_articles=lambda a, r: a,
    )
    assert list_wiki_languages(ctx) == []
