"""Tests fuer das 429-Handling im Crawler (illico_ingest.crawl).

Kernverhalten:
- Ein Host, der dauerhaft mit HTTP 429 drosselt, darf den Crawl NICHT
  stundenlang blockieren. Nach `max_consecutive_429` aufeinanderfolgenden
  terminalen 429 wird der GANZE Crawl abgebrochen.
- Ein zwischenzeitlicher Nicht-429-Response setzt den Block-Zaehler zurueck,
  sodass verstreute 429 den Crawl nicht faelschlich beenden.

Es fliesst kein echter Netzwerk-Traffic: httpx.Client und time.sleep sind gemockt.
"""
from unittest.mock import patch

import illico_ingest as ing


class FakeResp:
    def __init__(self, status, text="", headers=None):
        self.status_code = status
        self.text = text
        self.content = text.encode()
        self.headers = headers or {}


def _sitemap_xml(n):
    urls = "".join(
        f"<url><loc>https://example.com/p{i}</loc></url>" for i in range(n)
    )
    return (
        '<?xml version="1.0"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )


_HTML_OK = (
    '<html lang="de"><head><title>Seite</title></head><body><article>'
    "<h1>Titel</h1><p>Dies ist ein ausreichend langer deutscher Absatz mit "
    "genug Text, damit trafilatura sinnvollen Markdown-Inhalt extrahiert und "
    "die 50-Zeichen-Schwelle klar ueberschritten wird.</p>"
    "</article></body></html>"
)


class Always429Client:
    """robots -> 404, sitemap -> n URLs, jede Seite -> 429 (Retry-After 60)."""

    def __init__(self, seeds=20):
        self.seeds = seeds
        self.page_gets = []

    def get(self, url, *a, **k):
        if url.endswith("/robots.txt"):
            return FakeResp(404)
        if "sitemap" in url:
            return FakeResp(200, text=_sitemap_xml(self.seeds))
        self.page_gets.append(url)
        return FakeResp(429, text="rate limited", headers={"retry-after": "60"})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Every5th200Client:
    """Jede 5. neu gesehene Seite -> 200 (setzt Zaehler zurueck), sonst 429.

    Max. 4 aufeinanderfolgende 429 -> Abbruch-Schwelle (5) wird nie erreicht.
    """

    def __init__(self):
        self.page_gets = []
        self._decided = {}
        self._order = []

    def get(self, url, *a, **k):
        if url.endswith("/robots.txt"):
            return FakeResp(404)
        if "sitemap" in url:
            return FakeResp(200, text=_sitemap_xml(20))
        self.page_gets.append(url)
        if url not in self._decided:
            idx = len(self._order)
            self._order.append(url)
            # Positionen 4, 9, 14, 19 -> 200; dazwischen max. 4x 429
            self._decided[url] = (idx % 5 == 4)
        if self._decided[url]:
            return FakeResp(200, text=_HTML_OK, headers={"content-type": "text/html"})
        return FakeResp(429, text="rate", headers={"retry-after": "60"})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_persistent_429_aborts_crawl(tmp_path):
    client = Always429Client(seeds=20)
    with patch("illico_ingest.httpx.Client", return_value=client), \
         patch("illico_ingest.time.sleep"):
        res = ing.crawl(
            "https://example.com/", tmp_path,
            max_depth=1, delay=0, max_consecutive_429=5,
        )

    # Abbruch nach genau 5 terminalen 429 — nicht alle 21 URLs abgearbeitet
    distinct_pages = set(client.page_gets)
    assert len(distinct_pages) == 5, distinct_pages
    assert len(res["failed"]) == 5
    assert all("429" in reason for _, reason in res["failed"])


def test_scattered_429_does_not_abort(tmp_path):
    client = Every5th200Client()
    with patch("illico_ingest.httpx.Client", return_value=client), \
         patch("illico_ingest.time.sleep"):
        res = ing.crawl(
            "https://example.com/", tmp_path,
            max_depth=0, delay=0, max_consecutive_429=5,
        )

    # Kein vorzeitiger Abbruch: alle 21 Seiten (start + 20 Seeds) versucht,
    # obwohl in Summe 17 URLs mit 429 antworten (aber nie 5 in Folge).
    distinct_pages = set(client.page_gets)
    assert len(distinct_pages) == 21, len(distinct_pages)
    assert len(res["success"]) >= 1


import json as _json


def test_crawl_writes_blocked_status(tmp_path):
    client = Always429Client(seeds=20)
    with patch("illico_ingest.httpx.Client", return_value=client), \
         patch("illico_ingest.time.sleep"):
        ing.crawl("https://example.com/", tmp_path,
                  max_depth=1, delay=0, max_consecutive_429=5)

    status = _json.loads((tmp_path / "_crawl-status.json").read_text(encoding="utf-8"))
    entry = status["domains"]["example.com"]
    assert entry["blocked"] is True
    assert entry["top_status"] == 429
    assert entry["ok"] == 0
    assert entry["last_crawl"]  # Zeitstempel gesetzt
