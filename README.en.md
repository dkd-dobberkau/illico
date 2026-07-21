# Illico

> **⚠️ Work in progress.** The authoritative documentation is currently German —
> see [README.md](README.md). This English translation is being brought up to
> date and may lag behind the German version.

Illico turns websites into a queryable knowledge base answered by an LLM —
**no RAG, no vector database**. Illico crawls a site, stores the pages as
Markdown, and has an LLM compile them into a structured, interlinked Markdown
wiki with Obsidian-style `[[links]]`.

Inspired by Andrej Karpathy's LLM knowledge base architecture (April 2026).

## Pipeline

```
URL → ingest → raw/*.md → compile → wiki/*.md → chat
```

1. **Crawl** — `illico-ingest` crawls a website and writes every page as a
   Markdown file (with frontmatter) to `illico-data/raw/`.
2. **Compile** — `illico-compile` has an LLM cluster the raw pages thematically
   and generate an interlinked wiki under `illico-data/wiki/`, including an entry
   point (`_index.md`) and a quality report (`_lint-report.md`).
3. **Chat (CLI)** — `illico-chat` is an interactive terminal chat over the
   compiled wiki: a router LLM call selects the relevant articles, a second call
   answers the question using that context.
4. **Serve (Web)** — `illico-serve` starts a FastAPI web interface with streaming
   chat (SSE), a wiki browser, and ingest/compile controls via the REST API.

## Installation

```bash
pip install .
# or directly from GitHub:
pip install git+https://github.com/dkd-dobberkau/illico@v0.2.0
```

With the test extra (for the test suite / downstream fixtures):

```bash
pip install .[test]
```

### API key

Illico uses the Anthropic API for compiling and chatting:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

## Usage

```bash
# 1. Crawl a website
illico-ingest ingest https://example.com --depth 2

# 2. Compile a wiki from the crawled pages
illico-compile
illico-compile --model claude-sonnet-4-6   # higher quality
illico-compile --lint                       # quality check only

# 3. Chat over the wiki in the terminal
illico-chat

# 4. Start the web interface (FastAPI + single-file frontend)
illico-serve
```

All commands accept `--data ./illico-data` (default) to set the data directory.

## Docker

Illico Single runs entirely via Docker Compose — the whole pipeline
(crawl → compile → web UI) in the same image against a persistent data
directory `./illico-data`, without a local Python install:

```bash
cp .env.example .env        # set ANTHROPIC_API_KEY
mkdir -p illico-data        # on Linux: must be writable by uid 1000

# 1. Crawl a site (one-shot)
docker compose run --rm illico illico-ingest ingest https://example.com --depth 1

# 2. Compile the wiki (one-shot)
docker compose run --rm illico illico-compile

# 3. Start the web UI
docker compose up -d        # → http://localhost:8000
```

`ingest` and `compile` are one-shot jobs (`run --rm`), `up -d` starts the
long-running web server. The wiki lives as readable Markdown under
`./illico-data/wiki/` — directly editable and version-controllable.

**Notes:**
- **Security:** Illico Single is login-free. Bind the port to localhost only, or
  put the app behind a reverse proxy with access control if it needs to be
  publicly reachable.
- **Linux:** The container runs as uid 1000 — make sure `./illico-data` is
  writable by that uid (irrelevant on macOS/Docker Desktop).

## Data directory

```
illico-data/
  raw/                ← crawled pages as Markdown (with frontmatter)
  wiki/                ← compiled wiki
    _index.md          ← entry point
    _lint-report.md     ← quality report
```

## Design

- **No RAG**: The wiki is plain Markdown — readable, editable, Git-versionable.
  Chat routing is explicit (the LLM picks relevant files by name), not
  embedding-based.
- **Default model**: `claude-haiku-4-5-20251001` for cost efficiency.
  `claude-sonnet-4-6` for complex sites.
- All prompts are in German (the project primarily targets German-language
  content).

## License

MIT — see [LICENSE](LICENSE).
