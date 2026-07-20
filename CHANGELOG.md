# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in dieser Datei dokumentiert.

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
