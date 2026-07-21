"""Tests für den Collection-/Bookmark-Ingest-Modus."""
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

import illico_ingest


BOOKMARKS_HTML = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3>Ordner A</H3>
    <DL><p>
        <DT><A HREF="https://example.com/a">Seite A</A>
        <DT><A HREF="https://example.com/a">Duplikat A</A>
        <DT><A HREF="javascript:void(0)">JS</A>
    </DL><p>
    <DT><A HREF="http://foo.org/b">Foo B</A>
    <DT><A HREF="place:type=6&sort=1">Firefox place</A>
    <DT><A HREF="chrome://bookmarks">Chrome intern</A>
</DL><p>
"""


def test_parse_bookmarks_extracts_http_urls_deduped_in_order():
    urls = illico_ingest.parse_bookmarks_html(BOOKMARKS_HTML)
    assert urls == ["https://example.com/a", "http://foo.org/b"]


def test_parse_bookmarks_empty_on_no_links():
    assert illico_ingest.parse_bookmarks_html("<html><body>nix</body></html>") == []


def _html_response(body_html):
    r = MagicMock()
    r.status_code = 200
    r.headers = {"content-type": "text/html; charset=utf-8"}
    r.text = body_html
    return r


def _page(title, paragraph):
    # genug Text, damit html_to_markdown > 50 Zeichen liefert
    return f"<html><head><title>{title}</title></head><body><article><h1>{title}</h1><p>{paragraph}</p></article></body></html>"


def test_collect_saves_pages_domain_prefixed(tmp_path):
    urls = ["https://example.com/a", "http://foo.org/b"]
    responses = {
        "https://example.com/a": _html_response(_page("A", "Dies ist ein ausreichend langer Absatz über Thema A für den Test.")),
        "http://foo.org/b": _html_response(_page("B", "Dies ist ein ausreichend langer Absatz über Thema B für den Test.")),
    }
    client = MagicMock()
    client.get.side_effect = lambda u: responses[u]
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("illico_ingest.httpx.Client", return_value=client), \
         patch("illico_ingest.time.sleep"):
        results = illico_ingest.collect(urls, tmp_path)

    assert len(results["success"]) == 2
    assert (tmp_path / "raw" / "example.com" / "a.md").exists()
    assert (tmp_path / "raw" / "foo.org" / "b.md").exists()
    # Frontmatter trägt die jeweils eigene Domain
    assert 'domain: "example.com"' in (tmp_path / "raw" / "example.com" / "a.md").read_text()
    assert 'domain: "foo.org"' in (tmp_path / "raw" / "foo.org" / "b.md").read_text()


def test_collect_skips_cached_urls(tmp_path):
    url = "https://example.com/a"
    client = MagicMock()
    client.get.side_effect = lambda u: _html_response(_page("A", "Ein ausreichend langer Absatz über Thema A für den Cache-Test hier."))
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("illico_ingest.httpx.Client", return_value=client), \
         patch("illico_ingest.time.sleep"):
        illico_ingest.collect([url], tmp_path)          # erster Lauf → speichert
        results2 = illico_ingest.collect([url], tmp_path)  # zweiter Lauf → cached

    assert results2["cached"] == [url]
    assert results2["success"] == []


def test_collect_records_non_200_as_failed(tmp_path):
    url = "https://example.com/tot"
    bad = MagicMock()
    bad.status_code = 404
    bad.headers = {"content-type": "text/html"}
    bad.text = ""
    client = MagicMock()
    client.get.side_effect = lambda u: bad
    client.__enter__.return_value = client
    client.__exit__.return_value = False

    with patch("illico_ingest.httpx.Client", return_value=client), \
         patch("illico_ingest.time.sleep"):
        results = illico_ingest.collect([url], tmp_path)

    assert results["failed"] and results["failed"][0][0] == url
    assert results["success"] == []


def test_collection_command_ingests_bookmarks(tmp_path, monkeypatch):
    bm = tmp_path / "lesezeichen.html"
    bm.write_text(BOOKMARKS_HTML, encoding="utf-8")

    captured = {}

    def fake_collect(urls, output_dir, **kwargs):
        captured["urls"] = urls
        captured["output_dir"] = output_dir
        return {"success": [(u, "x.md") for u in urls], "failed": [], "skipped": [], "cached": []}

    monkeypatch.setattr(illico_ingest, "collect", fake_collect)

    result = CliRunner().invoke(
        illico_ingest.app,
        ["collection", str(bm), "--data", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    # Parser + Command reichen genau die http(s)-Bookmarks an collect weiter
    assert captured["urls"] == ["https://example.com/a", "http://foo.org/b"]
    assert captured["output_dir"] == tmp_path


def test_collection_command_errors_on_missing_file(tmp_path):
    result = CliRunner().invoke(
        illico_ingest.app,
        ["collection", str(tmp_path / "gibtsnicht.html")],
    )
    assert result.exit_code != 0
