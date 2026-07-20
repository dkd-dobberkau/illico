# Illico

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
2. **Compile** — `illico-compile` lässt ein LLM die rohen Seiten thematisch
   clustern und daraus ein verlinktes Wiki unter `illico-data/wiki/`
   erzeugen, inklusive Einstiegspunkt (`_index.md`) und Qualitätsreport
   (`_lint-report.md`).
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
illico-ingest https://example.com --depth 2

# 2. Wiki aus den gecrawlten Seiten kompilieren
illico-compile
illico-compile --model claude-sonnet-4-6   # höhere Qualität
illico-compile --lint                       # nur Qualitätsprüfung

# 3. Im Terminal über das Wiki chatten
illico-chat

# 4. Web-Oberfläche starten (FastAPI + Single-File-Frontend)
illico-serve
```

Alle Befehle akzeptieren `--data ./illico-data` (Default), um das
Datenverzeichnis anzugeben.

## Docker

```bash
docker build -t illico .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... -v $(pwd)/illico-data:/app/illico-data illico
```

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
