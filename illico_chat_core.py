"""
illico chat core — shared logic between CLI (illico_chat.py) and web (illico_app.py).

Contains:
- Wiki loading and index generation
- Router LLM call (with optional graph enrichment)
- Answer LLM call, sync (CLI) and async-streaming (web)
"""

from pathlib import Path
from typing import AsyncGenerator, Optional

import illico_llm
from illico_graph import build_article_entity_map


ROUTER_PROMPT = """Gegeben diese Nutzerfrage: "{question}"

Verfügbare Wiki-Artikel:
{articles}

Welche Artikel sind relevant? Antworte NUR mit kommaseparierten Dateinamen (z.B.: typo3.md, solr.md).
Maximal 4. Wenn keiner passt: "none"
"""

SYSTEM_PROMPT = """Du bist Illico, ein präziser Wissensassistent. Du antwortest ausschließlich auf Basis der bereitgestellten Wiki-Artikel.

Regeln:
- Nur Informationen aus den Wiki-Artikeln verwenden
- Am Ende der Antwort die genutzten Quellen nennen als: *Quellen: Artikelname*
- Zusätzlich zu den Wiki-Artikeln erhältst du strukturierte Beziehungen aus dem Wissensgraph. Du darfst sie nutzen und als Quelle "Wissensgraph" nennen
- Wenn die Antwort nicht in der Wiki steht, klar sagen
- Antworte in der Sprache der Frage
- Präzise und direkt

Wiki-Index:
{index}
"""


def load_wiki(wiki_dir: Path) -> dict[str, str]:
    """Lädt alle Wiki-Artikel."""
    articles: dict[str, str] = {}
    for md_file in sorted(wiki_dir.glob("*.md")):
        articles[md_file.name] = md_file.read_text(encoding="utf-8")
    return articles


def get_index(articles: dict) -> str:
    """Erstellt eine kompakte Übersicht aller Artikel für den System-Prompt."""
    if "_index.md" in articles:
        return articles["_index.md"][:2000]

    lines = []
    for name, content in articles.items():
        if name.startswith("_"):
            continue
        first_line = next(
            (l for l in content.split("\n")
             if l.strip() and not l.startswith(("#", "---", "title:"))),
            "",
        )
        lines.append(f"- [[{name.replace('.md', '')}]]: {first_line[:100]}")
    return "\n".join(lines)


def route(
    question: str,
    articles: dict,
    model: str,
    nodes: Optional[list] = None,
    max_tokens: int = 200,
) -> list[str]:
    """Entscheidet welche Wiki-Artikel für eine Frage relevant sind."""
    entity_map = build_article_entity_map(articles, nodes) if nodes else {}

    lines = []
    for name in articles.keys():
        if name.startswith("_"):
            continue
        entities = entity_map.get(name, [])
        if entities:
            lines.append(f"- {name}  (Entitäten: {', '.join(entities[:5])})")
        else:
            lines.append(f"- {name}")
    article_list = "\n".join(lines)

    prompt = ROUTER_PROMPT.format(question=question, articles=article_list)
    messages = [{"role": "user", "content": prompt}]

    result = illico_llm.call_sync(model, messages, max_tokens=max_tokens)
    result = result.strip()
    if result.lower() == "none":
        return []

    names = [n.strip() for n in result.split(",")]
    return [n for n in names if n in articles]


def build_messages(
    question: str,
    relevant_articles: list[str],
    articles: dict,
    history: list,
    max_history: int = 0,
    graph_context: str = "",
) -> list[dict]:
    """Baut die Messages-Liste mit Artikel-Kontext.

    max_history=0 bedeutet: gesamte history beibehalten (CLI-Verhalten).
    max_history>0 schneidet auf die letzten N Einträge (Web-Verhalten).
    graph_context (optional) wird hinter den Artikel-Kontext gehängt.
    """
    context = ""
    if relevant_articles:
        context = "\n\n---\nVerfügbare Wiki-Artikel für diese Frage:\n\n"
        for name in relevant_articles:
            context += f"### {name}\n{articles.get(name, '')}\n\n"

    if graph_context:
        context += f"\n\n---\n{graph_context}\n"

    user_message = question if not context else f"{question}\n\n{context}"

    base = history[-max_history:] if max_history else history
    return base + [{"role": "user", "content": user_message}]


def answer_sync(
    question: str,
    relevant_articles: list[str],
    articles: dict,
    history: list,
    system: str,
    model: str,
    max_tokens: int = 1000,
    graph_context: str = "",
) -> str:
    """Generiert eine vollständige Antwort (CLI-Pfad)."""
    messages = build_messages(
        question, relevant_articles, articles, history, graph_context=graph_context
    )
    return illico_llm.call_sync(model, messages, system=system, max_tokens=max_tokens)


async def answer_stream_async(
    question: str,
    relevant_articles: list[str],
    articles: dict,
    history: list,
    system: str,
    model: str,
    max_tokens: int = 1000,
    max_history: int = 10,
    graph_context: str = "",
) -> AsyncGenerator[str, None]:
    """Streamt die Antwort als Text-Chunks (Web-Pfad, echt nebenläufig)."""
    messages = build_messages(
        question, relevant_articles, articles, history,
        max_history=max_history, graph_context=graph_context,
    )
    async for text in illico_llm.call_stream(model, messages, system=system, max_tokens=max_tokens):
        yield text
