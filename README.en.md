# Illico

> **Note:** [README.md](README.md) (German) is the authoritative version. This
> translation tracks it, but the German file is the one to check if the two ever
> disagree.

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
2. **Compile** — `illico-compile` condenses each raw page into a distillate
   (summary + entities), clusters those thematically, and generates an
   interlinked wiki under `illico-data/wiki/` from them, including an entry point
   (`_index.md`), a quality report (`_lint-report.md`) and a knowledge graph.
   The compile is **incremental**: distillates live under `illico-data/distill/`
   and are addressed by page content, so a second run only processes pages that
   changed. A re-crawl without content changes costs zero LLM calls.
3. **Chat (CLI)** — `illico-chat` is an interactive terminal chat over the
   compiled wiki: a router LLM call selects the relevant articles, a second call
   answers the question using that context.
4. **Serve (Web)** — `illico-serve` starts a FastAPI web interface with streaming
   chat (SSE), a wiki browser, and ingest/compile controls via the REST API.

## Installation

```bash
pip install .
# or directly from GitHub:
pip install git+https://github.com/dkd-dobberkau/illico@v0.3.4
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
illico-compile --model claude-sonnet-4-6     # higher quality
illico-compile --jobs 8                      # more parallel LLM calls (default 4)
illico-compile --lint                        # quality check only
illico-compile --graph-only                  # rebuild only the graph from the distillates
illico-compile --canonicalize-only           # only entity resolution over the existing graph
illico-compile --only-domains example.com    # compile pages from these domains only

# 3. Chat over the wiki in the terminal
illico-chat

# 4. Start the web interface (FastAPI + single-file frontend)
illico-serve
```

All commands accept `--data ./illico-data` (default) to set the data directory.

### Collection mode (bookmarks instead of crawl)

Instead of crawling an entire domain, Illico can process a curated URL list from
a browser bookmarks export (Netscape HTML, Chrome/Firefox/Safari) — each URL is
fetched exactly once, without following links:

```bash
illico-ingest collection bookmarks.html
illico-ingest collection bookmarks.html --lang en   # keep English pages only
```

Pages are stored domain-prefixed under `raw/<domain>/…` and then compiled into
the wiki with `illico-compile` as usual. Options mirror `ingest`: `--data`,
`--delay`, `--fresh`, `--lang`, `--max-pages`.

### Multilingual wikis

`--lang` filters the raw pages by language (frontmatter `language:`, falling back
to `langdetect`) and switches the compile prompts at the same time: with exactly
one language the German or English prompt set runs, otherwise German as the
fallback. Wiki, graph and distillate store get a language suffix:

```bash
illico-compile --lang de   # → wiki-de/, graph-de/, distill-de/, _inventory-de.json
illico-compile --lang en   # → wiki-en/, graph-en/, distill-en/, _inventory-en.json
```

This lets both languages be maintained **side by side**: each gets its own set
of directories, and a run never touches the other's. `--wiki-dir` names the wiki
directory freely; graph, distillates and inventory follow that name
(`--wiki-dir wiki-intern` → `graph-intern/`, `distill-intern/`,
`_inventory-intern.json`).

Raw pages from older runs do not carry the `language:` frontmatter field yet.
`illico-ingest migrate-lang` backfills it — idempotent, and `--dry-run` shows
what would happen first.

### Force a full rebuild

The compile only rewrites articles whose sources changed and keeps the slugs it
once assigned — which keeps `[[links]]` and bookmarks valid across runs, but lets
the topic structure age over time. Two levers against that, both just deleting:

- delete `illico-data/distill/` → every page is distilled again (e.g. after a
  model switch, which deliberately does *not* invalidate the cache)
- delete `illico-data/_inventory.json` → the topic clusters are cut from
  scratch, losing the old slugs in the process

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
  raw/                  ← crawled pages as Markdown (with frontmatter)
  distill/              ← distillates, addressed by page content (cache)
  wiki/                 ← compiled wiki
    _index.md           ← entry point
    _lint-report.md     ← quality report
  graph/                ← knowledge graph: nodes.json, edges.json, meta.json
  _inventory.json       ← topic clusters with stable slugs and fingerprints
  _crawl-history.json   ← what the crawler has already seen
  _crawl-status.json    ← status of the last crawl run
```

With `--lang`/`--wiki-dir`, `wiki/`, `distill/`, `graph/` and `_inventory.json`
all carry the same suffix (`wiki-de/`, `distill-de/`, `graph-de/`,
`_inventory-de.json`) — each language gets a fully separate set.

## Design

- **No RAG**: The wiki is plain Markdown — readable, editable, Git-versionable.
  Chat routing is explicit (the LLM picks relevant files by name), not
  embedding-based.
- **Default model**: `claude-haiku-4-5-20251001` for cost efficiency.
  `claude-sonnet-4-6` for complex sites.
- **Prompt language**: The compile prompts exist in German and English,
  `--lang de`/`--lang en` switches between them; without the flag the German set
  runs. The articles themselves are always written in the language of the
  sources — the prompt language governs the instructions, not the output.

## License

MIT — see [LICENSE](LICENSE).
