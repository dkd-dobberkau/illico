# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

## v0.2.3 — Collection-/Bookmark-Modus, Docker-Compose, englische README

- **Neuer Ingest-Modus `collection`:** Statt eine Domain zu crawlen, verarbeitet
  `illico-ingest collection <bookmarks.html>` eine kuratierte URL-Liste aus einem
  Browser-Bookmarks-Export (Netscape-HTML). Jede URL wird genau einmal geholt (kein
  BFS), domain-präfixiert unter `raw/<domain>/…` abgelegt. Optionen analog zu
  `ingest` (`--data`, `--delay`, `--fresh`, `--lang`, `--max-pages`). Der bestehende
  Domain-Crawl bleibt unverändert.
- **Docker-Compose:** `docker-compose.yml` + `.dockerignore` für turnkey Self-Hosting
  — die ganze Pipeline (Crawl/Collection → Compile → Web-UI) im selben Image gegen
  ein persistentes `./illico-data`, Key über `.env`.
- **Englische README** (`README.en.md`, WIP) mit Hinweis, dass die deutsche README
  die maßgebliche Fassung ist. Kleiner Bugfix im deutschen Usage-Beispiel
  (`illico-ingest` braucht den `ingest`-Subcommand).

## v0.2.2 — Fix: Compile überlebt Anthropic-Überlast (HTTP 529)

- **Fix:** Große Compiles brachen bei transienter Provider-Überlast hart ab, statt
  zu retryen. Anthropics `529 overloaded_error` kommt bei litellm als
  `InternalServerError` an — der fehlte in der Retryable-Liste (`illico_llm._RETRYABLE`),
  sodass der Fehler bis nach oben durchschlug und den Compile mittendrin killte.
  Jetzt wird `InternalServerError` mit Exponential-Backoff wiederholt (transient/
  retrybar laut Anthropic-Doku). 2 neue Tests decken Membership und Retry-Verhalten ab.

## v0.2.1 — Fix: Web-Verwaltung bei pip-Installation

- **Fix:** Die Web-Verwaltung (Crawlen/Kompilieren/Graph über die Oberfläche)
  startete die Kern-CLIs per Dateiname (`illico_ingest.py`), was nur aus einem
  Quell-Checkout funktionierte — bei `pip install illico` lagen die Module in
  site-packages und die Aufrufe brachen mit „can't open file". Jetzt paket-sicher
  über `python -m illico_ingest` / `python -m illico_compile` (CWD-unabhängig).
- Kleinere Korrektur eines veralteten Hinweis-Textes im Compiler.

## v0.2.0 — eigenes Single-Frontend + Web-Verwaltung

- **Eigenes, schlankes Single-Frontend** (`illico_index.html`): Chat, Wiki-Browsen,
  Quellen und Lint-Hinweise — ohne Login, ohne Mandanten-/Admin-Ballast.
- **Web-Verwaltung im Browser** (`illico_single`): Crawlen, Kompilieren, Graph neu
  bauen und Domains entfernen direkt aus der Oberfläche, mit Live-Job-Log.
- **Optionaler Zugangs-Token** `ILLICO_SINGLE_TOKEN`: leer = offen (localhost),
  gesetzt = `Authorization: Bearer <token>` für die Verwaltungs-Endpoints
  (konstantzeitiger Vergleich).
- **App-Factory** `create_app(frontend_path=…, management_router=…)` — Frontend und
  Verwaltungs-Router überschreibbar (Naht für private Overlays).
- **Vollständig offline**: d3 ist lokal eingebettet, keine externen CDN-Abhängigkeiten.

## v0.1.0 — erstes öffentliches Release (Illico Single)

- Erste öffentliche Version von Illico als installierbares Python-Paket.
- Pipeline: `illico-ingest` (Crawl) → `illico-compile` (LLM-Wiki-Compiler) →
  `illico-chat` (CLI-Chat) → `illico-serve` (Web-Oberfläche mit FastAPI-Backend).
- Kein RAG, keine Vektor-Datenbank — die Wissensbasis ist ein lesbares,
  Git-versionierbares Markdown-Wiki mit Obsidian-Style `[[Links]]`.
