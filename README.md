# Illico

> **English:** [README.en.md](README.en.md) (work in progress). Diese deutsche
> Fassung ist die maßgebliche Version.

Illico verwandelt Websites in eine abfragbare Wissensbasis, die von einem LLM
beantwortet wird — **kein RAG, keine Vektor-Datenbank**. Illico crawlt eine
Site, speichert die Seiten als Markdown und lässt ein LLM daraus ein
strukturiertes, verlinktes Markdown-Wiki mit Obsidian-Style `[[Links]]`
kompilieren.

Inspiriert von Andrej Karpathys LLM-Knowledge-Base-Architektur (April 2026).

## Pipeline

```
URL → ingest → raw/*.md → compile → wiki/*.md → chat
```

1. **Crawl** — `illico-ingest` crawlt eine Website und schreibt jede Seite als
   Markdown-Datei (mit Frontmatter) nach `illico-data/raw/`.
2. **Compile** — `illico-compile` verdichtet jede Rohseite zu einem Destillat
   (Kurzfassung + Entitäten), clustert diese thematisch und erzeugt daraus ein
   verlinktes Wiki unter `illico-data/wiki/`, inklusive Einstiegspunkt
   (`_index.md`), Qualitätsreport (`_lint-report.md`) und Wissensgraph.
   Der Compile ist **inkrementell**: Destillate liegen unter
   `illico-data/distill/` und sind über den Seiteninhalt adressiert, ein
   erneuter Lauf verarbeitet also nur geänderte Seiten. Ein Nachcrawl ohne
   inhaltliche Änderung kostet null LLM-Aufrufe.
3. **Chat (CLI)** — `illico-chat` ist ein interaktiver Terminal-Chat über das
   kompilierte Wiki: eine Router-LLM-Anfrage wählt relevante Artikel aus,
   eine zweite beantwortet die Frage mit diesem Kontext.
4. **Serve (Web)** — `illico-serve` startet eine FastAPI-Web-Oberfläche mit
   Streaming-Chat (SSE), Wiki-Browser und Ingest/Compile-Steuerung über die
   REST-API.

## Installation

```bash
pip install .
# oder direkt von GitHub:
pip install git+https://github.com/dkd-dobberkau/illico@v0.2.0
```

Mit Test-Extra (für die Test-Suite / Downstream-Fixtures):

```bash
pip install .[test]
```

### API-Key

Illico nutzt die Anthropic-API zum Kompilieren und Chatten:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Verwendung

```bash
# 1. Website crawlen
illico-ingest ingest https://example.com --depth 2

# 2. Wiki aus den gecrawlten Seiten kompilieren
illico-compile
illico-compile --model claude-sonnet-4-6   # höhere Qualität
illico-compile --jobs 8                     # mehr parallele LLM-Calls (Default 4)
illico-compile --lint                       # nur Qualitätsprüfung
illico-compile --graph-only                 # nur den Graph aus den Destillaten neu bauen

# 3. Im Terminal über das Wiki chatten
illico-chat

# 4. Web-Oberfläche starten (FastAPI + Single-File-Frontend)
illico-serve
```

Alle Befehle akzeptieren `--data ./illico-data` (Default), um das
Datenverzeichnis anzugeben.

### Collection-Modus (Bookmarks statt Crawl)

Statt eine ganze Domain zu crawlen, kann Illico eine kuratierte URL-Liste aus
einem Browser-Bookmarks-Export (Netscape-HTML, Chrome/Firefox/Safari) verarbeiten
— jede URL wird genau einmal geholt, ohne den Links zu folgen:

```bash
illico-ingest collection lesezeichen.html
illico-ingest collection lesezeichen.html --lang de   # nur deutschsprachige Seiten
```

Die Seiten werden domain-präfixiert unter `raw/<domain>/…` abgelegt und
anschließend wie gewohnt mit `illico-compile` zum Wiki kompiliert. Optionen
analog zu `ingest`: `--data`, `--delay`, `--fresh`, `--lang`, `--max-pages`.

### Vollneubau erzwingen

Der Compile schreibt nur Artikel neu, deren Quellen sich geändert haben, und
behält die einmal vergebenen Slugs bei — dadurch bleiben `[[links]]` und
Bookmarks über Läufe hinweg gültig, die Themenstruktur altert aber mit der
Zeit. Zwei Schalter dagegen, beides einfach Löschen:

- `illico-data/distill/` löschen → alle Seiten werden neu destilliert
  (z.B. nach einem Modellwechsel, denn der invalidiert den Cache bewusst nicht)
- `illico-data/_inventory.json` löschen → die Themencluster werden komplett neu
  geschnitten, alte Slugs gehen dabei verloren

## Docker

Illico Single läuft komplett über Docker Compose — die ganze Pipeline
(Crawl → Compile → Web-UI) im selben Image gegen ein persistentes
Datenverzeichnis `./illico-data`, ohne lokale Python-Installation:

```bash
cp .env.example .env        # ANTHROPIC_API_KEY eintragen
mkdir -p illico-data        # auf Linux: muss für uid 1000 schreibbar sein

# 1. Eine Site crawlen (One-Shot)
docker compose run --rm illico illico-ingest ingest https://example.com --depth 1

# 2. Wiki kompilieren (One-Shot)
docker compose run --rm illico illico-compile

# 3. Web-UI starten
docker compose up -d        # → http://localhost:8000
```

`ingest` und `compile` sind einmalige Jobs (`run --rm`), `up -d` startet den
langlaufenden Web-Server. Das Wiki liegt als lesbares Markdown unter
`./illico-data/wiki/` — direkt editier- und git-versionierbar.

**Hinweise:**
- **Sicherheit:** Illico Single ist login-frei. Binde den Port nur an localhost
  oder stelle die App hinter einen Reverse-Proxy mit Zugriffsschutz, wenn sie
  öffentlich erreichbar sein soll.
- **Linux:** Der Container läuft als uid 1000 — stelle sicher, dass `./illico-data`
  für diese uid schreibbar ist (auf macOS/Docker Desktop irrelevant).

## Datenverzeichnis

```
illico-data/
  raw/                ← gecrawlte Seiten als Markdown (mit Frontmatter)
  wiki/                ← kompiliertes Wiki
    _index.md          ← Einstiegspunkt
    _lint-report.md     ← Qualitätsreport
```

## Design

- **Kein RAG**: Das Wiki ist reines Markdown — lesbar, editierbar,
  Git-versionierbar. Chat-Routing ist explizit (das LLM wählt relevante
  Dateien anhand des Namens), nicht Embedding-basiert.
- **Default-Modell**: `claude-haiku-4-5-20251001` für Kosteneffizienz.
  `claude-sonnet-4-6` für komplexe Sites.
- Alle Prompts sind auf Deutsch (das Projekt richtet sich primär an
  deutschsprachige Inhalte).

## Lizenz

MIT — siehe [LICENSE](LICENSE).
