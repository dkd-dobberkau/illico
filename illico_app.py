"""
illico app — FastAPI-Backend für das Web-Interface (Single-User-Kern).

Dies ist die offene Kern-App: kein Login, feste Sicht auf `wiki/`. Alle
Kern-Routen beziehen ihre Sicht aus einem injizierten `WikiContext`
(Default: `single_user_provider`). Login-/Admin-/Tenant-Routen können von einem
optionalen, privaten Overlay über `create_app()` (eigener `context_provider`,
`frontend_path`, `management_router`, zusätzliche Router) nachgereicht werden.

Usage:
    python illico_app.py
    python illico_app.py --data ./illico-data --port 8000
"""

import logging
import os
import re
import json
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()
from pathlib import Path
from datetime import datetime
from typing import Optional, AsyncGenerator, Callable, Sequence

import illico_llm
import uvicorn
import typer
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

CHAT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

from illico_graph import load_graph_data, load_graph_meta, expand_with_graph, restrict_to_articles, build_graph_context
from illico_chat_core import (
    SYSTEM_PROMPT,
    answer_stream_async,
    get_index,
    load_wiki as _load_wiki_core,
    route,
)
from illico_context import (
    WikiContext,
    single_user_provider,
    resolve_wiki_dir,
    list_wiki_languages,
)

cli = typer.Typer()

DATA_DIR = Path(os.environ.get("ILLICO_DATA", "./illico-data"))

ContextProvider = Callable[..., WikiContext]


# ─── Modelle ──────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    question: str
    history: list[dict] = []
    lang: Optional[str] = None

class ChatSaveRequest(BaseModel):
    id: str
    title: str
    messages: list[dict]


# ─── Wiki-Zugriff ─────────────────────────────────────────────────────────────

def get_raw_dir() -> Path:
    return DATA_DIR / "raw"


def load_wiki(ctx: WikiContext, lang: Optional[str] = None) -> dict:
    wiki_dir = resolve_wiki_dir(ctx, lang)
    if not wiki_dir.exists():
        return {}
    return _load_wiki_core(wiki_dir)


def _raw_domain_map() -> dict[str, str]:
    """Liefert {raw_filename: domain} aus den YAML-Frontmattern der Raw-Dateien.

    Nicht sicht-gefiltert: die volle Map dient sowohl der Domain-Ableitung als
    auch dem Cloud-Wachhund (filter_articles_for), um Cross-Tenant-Leaks zu
    erkennen.
    """
    from illico_frontmatter import extract_raw_domain
    raw_dir = get_raw_dir()
    if not raw_dir.exists():
        return {}
    mapping: dict[str, str] = {}
    for f in raw_dir.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        d = extract_raw_domain(text)
        if d:
            mapping[str(f.relative_to(raw_dir))] = d
    return mapping


def filtered_articles(ctx: WikiContext, lang: Optional[str] = None) -> dict[str, str]:
    """Wiki-Artikel in der Sicht des Contexts. Der Domain-Filter steckt in
    `ctx.filter_articles` (Single-User: Identität; Cloud: Tenant-Wachhund)."""
    return ctx.filter_articles(load_wiki(ctx, lang), _raw_domain_map())


def _raw_language_counts() -> dict[str, int]:
    """Liefert {lang: count} aus den language-Feldern der raw-Dateien."""
    raw_dir = get_raw_dir()
    if not raw_dir.exists():
        return {}
    counts: dict[str, int] = {}
    for f in raw_dir.rglob("*.md"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if not text.startswith("---"):
                continue
            end = text.index("---", 3)
            for line in text[3:end].split("\n"):
                stripped = line.strip()
                if stripped.startswith("language:"):
                    val = stripped.split(":", 1)[1].strip().strip('"').strip("'").lower()
                    if val:
                        lang = val.split("-")[0]
                        counts[lang] = counts.get(lang, 0) + 1
                    break
        except (ValueError, OSError):
            continue
    return counts


def _article_domains(articles: dict, raw_domains: dict) -> dict[str, list[str]]:
    """Liefert {wiki_article: [domains]} basierend auf sources-Frontmatter."""
    from illico_frontmatter import extract_sources
    result = {}
    for name, content in articles.items():
        if name.startswith("_"):
            continue
        domains = set()
        for src in extract_sources(content):
            if src in raw_domains:
                domains.add(raw_domains[src])
        result[name] = sorted(domains)
    return result


# ─── Chat-Logik (SSE-Wrapping um illico_chat_core) ────────────────────────────

_log = logging.getLogger(__name__)


async def stream_answer(
    question: str,
    history: list,
    lang: Optional[str],
    ctx: WikiContext,
) -> AsyncGenerator[str, None]:
    try:
        articles = filtered_articles(ctx, lang)

        if not articles or all(k.startswith("_") for k in articles):
            yield "data: " + json.dumps({"type": "error", "text": "Keine Wiki-Artikel für diese Sicht verfügbar."}) + "\n\n"
            return

        nodes, edges = load_graph_data(DATA_DIR, lang, namespace=ctx.graph_namespace)
        if not ctx.unrestricted:
            # Graph ebenfalls auf erlaubte Entitäten beschränken (siehe api_graph)
            nodes, edges = restrict_to_articles(articles, nodes, edges)

        # route() ist synchron (blockiert sonst den Loop) → in Worker-Thread auslagern.
        relevant = await run_in_threadpool(
            route,
            question,
            articles,
            illico_llm.ROUTER_MODEL,
            nodes=nodes,
            max_tokens=150,
        )
        graph_context = ""
        if nodes and edges and relevant:
            relevant = expand_with_graph(relevant, articles, nodes, edges)
            graph_context = build_graph_context(relevant, articles, nodes, edges)

        yield "data: " + json.dumps({"type": "sources", "files": relevant}) + "\n\n"

        system = SYSTEM_PROMPT.format(index=get_index(articles))
        async for text in answer_stream_async(
            question, relevant, articles, history, system, illico_llm.ANSWER_MODEL,
            graph_context=graph_context,
        ):
            yield "data: " + json.dumps({"type": "text", "text": text}) + "\n\n"

        yield "data: " + json.dumps({"type": "done"}) + "\n\n"
    except Exception:
        _log.exception("stream_answer error")
        yield "data: " + json.dumps({"type": "error", "text": "Error generating response."}) + "\n\n"
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"


# ─── Chat-Beispielfragen (Suggestions) ────────────────────────────────────────

def _parse_suggestions(raw: str) -> list[str]:
    """Parst die LLM-Antwort zu max. 4 Fragen. Robust gegen Markdown-Fences
    und Zusatztext; bei Fehler -> []."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = [s.strip() for s in data if isinstance(s, str) and s.strip() and len(s.strip()) <= 120]
    return out[:4]


def _generate_suggestions(articles: dict, lang: Optional[str]) -> list[str]:
    """Erzeugt 3-4 Beispielfragen per LLM aus den Artikel-Titeln. Fehler -> [].

    `lang` ist reserviert: die Cache-Partitionierung erfolgt bereits sprach-
    getrennt über `resolve_wiki_dir(ctx, lang)` im Aufrufer; die Prompt-Sprache
    ist vorerst fest Deutsch.
    """
    prompt = (
        "Hier ist die Inhaltsübersicht einer Wissensbasis:\n\n"
        f"{get_index(articles)}\n\n"
        "Formuliere 3-4 kurze, konkrete Beispielfragen auf Deutsch, die ein "
        "Besucher dieser Wissensbasis stellen könnte. Antworte ausschließlich "
        "mit einem JSON-Array von Strings, ohne Nummerierung, jede Frage unter "
        "80 Zeichen."
    )
    try:
        result = illico_llm.call_sync(
            illico_llm.ANSWER_MODEL,
            [{"role": "user", "content": prompt}],
            max_tokens=300,
        )
        return _parse_suggestions(result)
    except Exception:
        return []


def _chat_suggestions(ctx: WikiContext, lang: Optional[str]) -> list[str]:
    """Gecachte Beispielfragen je Sicht/Sprache; regeneriert, wenn der Cache
    aelter als das Wiki (_index.md) ist."""
    wiki_dir = resolve_wiki_dir(ctx, lang)
    if not wiki_dir.exists():
        return []
    articles = filtered_articles(ctx, lang)
    if not any(not a.startswith("_") for a in articles):
        return []
    cache = wiki_dir / "_suggestions.json"
    index_md = wiki_dir / "_index.md"
    if cache.exists():
        fresh = (not index_md.exists()) or cache.stat().st_mtime >= index_md.stat().st_mtime
        if fresh:
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                if isinstance(data, list) and all(isinstance(x, str) for x in data):
                    return data
            except (json.JSONDecodeError, OSError):
                pass  # defekt -> neu generieren
    suggestions = _generate_suggestions(articles, lang)
    if suggestions:
        try:
            cache.write_text(json.dumps(suggestions, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return suggestions


# ─── Chat-History ────────────────────────────────────────────────────────────

def _chats_root() -> Path:
    d = DATA_DIR / "chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _chats_dir(ctx: WikiContext) -> Path:
    d = _chats_root() / ctx.chat_bucket
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_chat_path(chat_id: str, ctx: WikiContext) -> Path | None:
    root = _chats_root()
    if ctx.chat_list_all:
        for bucket in root.iterdir():
            if not bucket.is_dir():
                continue
            p = bucket / f"{chat_id}.json"
            if p.exists():
                return p
        return None
    p = _chats_dir(ctx) / f"{chat_id}.json"
    return p if p.exists() else None


# ─── Favicon ──────────────────────────────────────────────────────────────────

# Inline-Favicon: „i"-Monogramm auf der App-Akzentfarbe (--green #1D9E75).
# Als SVG ausgeliefert — kein Binaer-Asset, kein StaticFiles-Mount noetig.
_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#1D9E75"/>'
    '<circle cx="16" cy="8.5" r="2.6" fill="#fff"/>'
    '<rect x="13.4" y="13" width="5.2" height="12.5" rx="2.6" fill="#fff"/>'
    '</svg>'
)


# ─── Kern-Routen ──────────────────────────────────────────────────────────────

def _register_core_routes(application: FastAPI, context_provider: ContextProvider,
                          frontend_path: Path | None = None) -> None:
    """Registriert die Kern-Routen auf einem eigenen Router. Der
    `context_provider` wird per Closure als FastAPI-Dependency eingehängt."""
    core = APIRouter()
    _frontend = frontend_path or (Path(__file__).parent / "illico_index.html")

    @core.get("/api/health")
    def api_health():
        """Auth-freier Healthcheck-Endpoint für Docker / Reverse-Proxy."""
        return {"ok": True}

    @core.get("/api/languages")
    def api_languages(ctx: WikiContext = Depends(context_provider)):
        return {"languages": list_wiki_languages(ctx)}

    @core.get("/api/stats")
    def api_stats(lang: Optional[str] = None, ctx: WikiContext = Depends(context_provider)):
        articles = filtered_articles(ctx, lang)
        raw_domains_all = _raw_domain_map()

        # Domain-Sicht anhand der erlaubten Quell-Domains (None = unbeschränkt),
        # entspricht dem alten `tenant_allowed_domains`-Pfad.
        allowed = ctx.allowed_domains
        if allowed is None:
            raw_for_view = raw_domains_all
        else:
            raw_for_view = {rel: d for rel, d in raw_domains_all.items() if d in allowed}

        art_domains = _article_domains(articles, raw_domains_all)
        if allowed is not None:
            art_domains = {k: [d for d in v if d in allowed] for k, v in art_domains.items()}
        all_domains = sorted({d for ds in art_domains.values() for d in ds})

        wiki_dir = resolve_wiki_dir(ctx, lang)
        lint_path = wiki_dir / "_lint-report.md"
        lint_hints: list[str] = []
        if lint_path.exists() and ctx.unrestricted:
            # Lint-Report enthält Cross-Domain-Hints — nur die volle Sicht sieht ihn.
            content = lint_path.read_text(encoding="utf-8")
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("- ") and len(line) > 4:
                    lint_hints.append(line[2:])
        compiled = "—"
        idx = wiki_dir / "_index.md"
        if idx.exists():
            compiled = datetime.fromtimestamp(idx.stat().st_mtime).strftime("%d.%m.%Y")

        return {
            "raw_count": len(raw_for_view),
            "article_count": len([a for a in articles if not a.startswith("_")]),
            "compiled": compiled,
            "model": illico_llm.ANSWER_MODEL,
            "articles": [a for a in articles if not a.startswith("_")],
            "lint_hints": lint_hints[:5],
            "domains": all_domains,
            "article_domains": art_domains,
            "languages": list_wiki_languages(ctx),
            "active_lang": lang or "",
            "raw_languages": _raw_language_counts() if ctx.unrestricted else {},
        }

    @core.get("/api/graph")
    def api_graph(lang: Optional[str] = None, ctx: WikiContext = Depends(context_provider)):
        nodes, edges = load_graph_data(DATA_DIR, lang, namespace=ctx.graph_namespace)
        meta = load_graph_meta(DATA_DIR, lang, namespace=ctx.graph_namespace)
        if ctx.unrestricted:
            return {"nodes": nodes, "edges": edges, "meta": meta}
        # Eingeschränkte Sicht: nur Knoten behalten, die in mind. einem
        # erlaubten Artikel vorkommen.
        articles = filtered_articles(ctx, lang)
        fnodes, fedges = restrict_to_articles(articles, nodes, edges)
        return {"nodes": fnodes, "edges": fedges, "meta": meta}

    @core.get("/api/articles")
    def api_articles(lang: Optional[str] = None, ctx: WikiContext = Depends(context_provider)):
        articles = filtered_articles(ctx, lang)
        return {name: content for name, content in articles.items() if not name.startswith("_")}

    @core.get("/api/article/{name}")
    def api_article(name: str, lang: Optional[str] = None, ctx: WikiContext = Depends(context_provider)):
        articles = filtered_articles(ctx, lang)
        if name not in articles:
            raise HTTPException(404, "Artikel nicht gefunden")
        return {"name": name, "content": articles[name]}

    @core.post("/api/chat")
    async def api_chat(req: ChatRequest, ctx: WikiContext = Depends(context_provider)):
        return StreamingResponse(
            stream_answer(req.question, req.history, req.lang, ctx),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @core.get("/api/chat/suggestions")
    def api_chat_suggestions(lang: Optional[str] = None, ctx: WikiContext = Depends(context_provider)):
        return {"suggestions": _chat_suggestions(ctx, lang)}

    @core.get("/api/chats")
    async def api_list_chats(ctx: WikiContext = Depends(context_provider)):
        root = _chats_root()
        chats: list[dict] = []
        if ctx.chat_list_all:
            buckets = [d for d in root.iterdir() if d.is_dir()]
        else:
            buckets = [root / ctx.chat_bucket] if (root / ctx.chat_bucket).exists() else []
        for bucket in buckets:
            for f in sorted(bucket.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    entry = {"id": data["id"], "title": data["title"], "updated": data.get("updated", "")}
                    if ctx.chat_list_all:
                        entry["tenant"] = bucket.name
                    chats.append(entry)
                except (json.JSONDecodeError, KeyError):
                    continue
        return {"chats": chats}

    @core.post("/api/chats")
    async def api_save_chat(req: ChatSaveRequest, ctx: WikiContext = Depends(context_provider)):
        if not CHAT_ID_RE.match(req.id):
            raise HTTPException(400, "Ungültige Chat-ID")
        path = _chats_dir(ctx) / f"{req.id}.json"
        data = {"id": req.id, "title": req.title, "messages": req.messages, "updated": datetime.now().isoformat()}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True}

    @core.get("/api/chats/{chat_id}")
    async def api_get_chat(chat_id: str, ctx: WikiContext = Depends(context_provider)):
        p = _find_chat_path(chat_id, ctx)
        if p is None:
            raise HTTPException(404, "Chat nicht gefunden")
        return json.loads(p.read_text(encoding="utf-8"))

    @core.delete("/api/chats/{chat_id}")
    async def api_delete_chat(chat_id: str, ctx: WikiContext = Depends(context_provider)):
        p = _find_chat_path(chat_id, ctx)
        if p is not None:
            p.unlink()
        return {"ok": True}

    @core.get("/api/domains")
    def api_domains(ctx: WikiContext = Depends(context_provider)):
        """Liefert alle Domains mit Dateianzahl aus raw/ (sicht-gefiltert).

        Filterung über `ctx.allowed_domains` (None = unbeschränkt) — identisch
        zum alten `tenant_allowed_domains`-Pfad.
        """
        raw_domains = _raw_domain_map()
        allowed = ctx.allowed_domains
        counts: dict[str, int] = {}
        for d in raw_domains.values():
            if allowed is not None and d not in allowed:
                continue
            counts[d] = counts.get(d, 0) + 1
        return {"domains": [{"name": d, "files": c} for d, c in sorted(counts.items())]}

    @core.get("/favicon.svg")
    @core.get("/favicon.ico")
    async def favicon():
        # Beide Pfade liefern dasselbe SVG; moderne Browser rendern es auch unter
        # /favicon.ico korrekt (Content-Type image/svg+xml). Verhindert den 404.
        return Response(content=_FAVICON_SVG, media_type="image/svg+xml")

    @core.get("/", response_class=HTMLResponse)
    async def index():
        if _frontend.exists():
            return HTMLResponse(_frontend.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Illico</h1><p>Frontend nicht gefunden.</p>")

    application.include_router(core)


# ─── App-Factory ──────────────────────────────────────────────────────────────

# Sentinel: "Kern-Default verwenden" vs. None = "keiner". MUSS identitäts-
# gleich (`is`) verglichen werden. Zukunftssicher: Tests, die illico_app per
# `importlib.reload` neu laden, dürfen NICHT via `from illico_app import
# create_app` eine stale Referenz halten — nach dem Reload gehört der
# Default-Arg noch zum alten Modul-Objekt, der Modul-Global `_DEFAULT_MGMT`
# zum neuen, und `is` vergliche zwei verschiedene Objekte. Immer frisch über
# `import illico_app` + `illico_app.create_app(...)` zugreifen (Konvention in
# tests/core/conftest.py).
_DEFAULT_MGMT = object()


def create_app(
    context_provider: ContextProvider = single_user_provider,
    extra_routers: Sequence[APIRouter] = (),
    on_startup: Sequence[Callable[[], None]] = (),
    frontend_path: Path | None = None,
    management_router=_DEFAULT_MGMT,
) -> FastAPI:
    """Baut eine Illico-App. Der Kern registriert die WikiContext-Routen und
    (per Default) den Single-Management-Router; Overlays reichen `extra_routers`,
    `on_startup`, ein eigenes `frontend_path` und/oder `management_router` nach.
    `management_router=None` unterdrückt den Single-Management-Router."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for hook in on_startup:
            hook()
        yield

    application = FastAPI(title="Illico", version="0.2.0", lifespan=lifespan)
    _register_core_routes(application, context_provider, frontend_path)

    if management_router is _DEFAULT_MGMT:
        from illico_single import single_management_router  # lazy: bricht Import-Zyklus
        application.include_router(single_management_router)
    elif management_router is not None:
        application.include_router(management_router)

    for r in extra_routers:
        application.include_router(r)
    return application


app = create_app()


# ─── CLI ──────────────────────────────────────────────────────────────────────

@cli.command()
def serve(
    data: Path = typer.Option(Path(os.environ.get("ILLICO_DATA", "./illico-data")), "--data", "-d"),
    port: int = typer.Option(8000, "--port", "-p"),
):
    """Startet das Illico Web-Interface."""
    global DATA_DIR
    DATA_DIR = data
    # Env angleichen, damit der single_user_provider (liest ILLICO_DATA) und die
    # Modul-Helfer (nutzen DATA_DIR) auf dasselbe Verzeichnis zeigen.
    os.environ["ILLICO_DATA"] = str(data)

    typer.echo(f"\n  ILLICO läuft auf http://localhost:{port}")
    typer.echo(f"  Data:    {data}")
    typer.echo(f"  Router:  {illico_llm.ROUTER_MODEL}")
    typer.echo(f"  Antwort: {illico_llm.ANSWER_MODEL}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning", proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    cli()
