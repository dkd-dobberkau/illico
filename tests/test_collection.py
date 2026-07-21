"""Tests für den Collection-/Bookmark-Ingest-Modus."""
from pathlib import Path
from unittest.mock import MagicMock, patch

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
