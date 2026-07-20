"""
illico compile — liest raw/ Markdown-Dateien und lässt ein LLM daraus
eine strukturierte, verlinkte Wiki aufbauen.

Usage:
    python compile.py
    python compile.py --data ./illico-data
    python compile.py --model claude-haiku-4-5-20251001  # schneller, günstiger
    python compile.py --lint                              # nur Linting-Pass
"""

import os
import json
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
from datetime import datetime
from typing import Optional

import typer
import illico_llm
from illico_frontmatter import extract_raw_domain
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.rule import Rule
from illico_canonicalize import (
    apply_clusters,
    block_nodes_by_label,
    split_block,
    merge_clusters_by_preflabel,
    fullname_clusters,
    unify_overlapping_clusters,
)

app = typer.Typer()
console = Console()


# ─── Prompts ──────────────────────────────────────────────────────────────────

INVENTORY_PROMPT = """Du bist Illico, ein LLM-Bibliothekar. Du erhältst eine Liste von Markdown-Dateien,
die von einer Website gecrawlt wurden. Deine Aufgabe ist es, einen strukturierten Überblick zu erstellen.

Analysiere die Dateien und erstelle ein JSON-Inventar mit folgender Struktur:
{
  "domain": "domain.tld",
  "site_language": "de/en/...",
  "author_tone": "kurze Beschreibung des Schreibstils",
  "main_topics": ["Thema 1", "Thema 2", ...],
  "clusters": [
    {
      "name": "Cluster-Name",
      "slug": "cluster-slug",
      "description": "kurze Beschreibung",
      "files": ["datei1.md", "datei2.md"]
    }
  ],
  "key_entities": ["wichtige Personen/Produkte/Organisationen"],
  "suggested_articles": ["Artikel-Titel 1", "Artikel-Titel 2", ...]
}

Antworte NUR mit dem JSON, kein Markdown, keine Erklärungen.

Dateiliste und Inhalte:
"""

MERGE_PROMPT = """Du bist Illico, ein LLM-Bibliothekar. Du erhältst mehrere Teil-Inventare einer gecrawlten Website.
Fasse sie zu einem einzigen, konsistenten Inventar zusammen.

Regeln:
- Ähnliche Cluster zusammenführen (keine Duplikate)
- Alle Dateien müssen einem Cluster zugeordnet sein
- Maximal 15 Cluster
- Slugs müssen eindeutig sein (lowercase, keine Sonderzeichen)
- main_topics aus allen Teil-Inventaren zusammenführen und deduplizieren

Teil-Inventare:
{inventories}

Antworte NUR mit dem zusammengeführten JSON (gleiche Struktur wie die Eingabe), kein Markdown, keine Erklärungen.
"""

BATCH_SIZE = 30  # Dateien pro Inventar-Batch

EXTRACT_PROMPT = """Du bist Illico, ein LLM-Wissensarchitekt. Extrahiere Entitäten und Beziehungen
aus den folgenden gecrawlten Website-Inhalten als Knowledge Graph.

Node-Labels (verwende nur diese):
- Organization — Unternehmen, Agenturen, Vereine, Behörden
- Person — namentlich genannte Personen
- Product — Software, Tools, Plattformen, Extensions
- Service — Dienstleistungen, Angebote
- Technology — Programmiersprachen, Frameworks, Standards
- Location — Städte, Länder, Regionen
- Event — Konferenzen, Meetups, Workshops
- Certification — Zertifizierungen, Awards, Auszeichnungen

Edge-Typen (verwende nur diese):
- OFFERS — Organisation bietet Service/Product an
- USES — Organisation/Person nutzt Technology/Product
- LOCATED_IN — Organisation/Person/Event hat Standort
- PARTNER_OF — Organisation ist Partner von Organisation
- CERTIFIED_FOR — Organisation ist zertifiziert für Product/Technology
- WORKS_AT — Person arbeitet bei Organisation
- DEVELOPED_BY — Product wurde entwickelt von Organisation/Person
- DEPENDS_ON — Product/Technology hängt ab von Product/Technology
- SPECIALIZES_IN — Organisation/Person spezialisiert auf Technology/Service
- PRESENTED_AT — Person/Organisation präsentierte bei Event

Für jeden Node: vergib eine eindeutige ID (ganzzahlig, ab 1), Label, Name und relevante Properties.
Für jede Edge: vergib eine ID, src (Node-ID), dst (Node-ID), rel (Edge-Typ) und Properties.

Markiere die Wissensschicht in den Properties jedes Nodes und jeder Edge:
- "source": "local" — direkt aus den gecrawlten Inhalten extrahiert
- "source": "domain" — vom LLM ergänztes Branchenwissen (z.B. "TYPO3 ist ein CMS")

Antworte NUR mit JSON in diesem Format:
{
  "nodes": [{"id": 1, "label": "Organization", "name": "...", "props": {"source": "local", ...}}],
  "edges": [{"id": 1, "src": 1, "dst": 2, "rel": "OFFERS", "props": {"source": "local", ...}}]
}

Kein Markdown, keine Erklärungen. Nur JSON.

Inhalte:
"""

MERGE_GRAPH_PROMPT = """Du bist Illico, ein LLM-Wissensarchitekt. Du erhältst mehrere Teil-Graphen
einer gecrawlten Website. Fasse sie zu einem einzigen, konsistenten Knowledge Graph zusammen.

Regeln:
- Gleiche Entitäten zusammenführen (z.B. "dkd" und "dkd Internet Service GmbH" → ein Node)
- IDs neu vergeben (fortlaufend ab 1 für Nodes, ab 1 für Edges)
- Duplikat-Edges entfernen
- "source"-Property beibehalten (local > domain bei Konflikten)
- Labels und Relationstypen nicht ändern

Teil-Graphen:
{graphs}

Antworte NUR mit dem zusammengeführten JSON (gleiche Struktur), kein Markdown, keine Erklärungen.
"""

CANONICALIZE_PROMPT = """Du bist Illico, ein LLM-Wissensarchitekt. Du erhältst eine JSON-Liste
von Graph-Nodes (gleiches Label) mit id und name. Finde Gruppen von Nodes, die DIESELBE
reale Entität bezeichnen — z.B. Schreibvarianten, Abkürzungen, Namenszusätze
("Frankfurt" / "Frankfurt am Main"; "dkd" / "dkd Internet Service GmbH").

Regeln:
- Bilde nur Cluster für Nodes, die nachweislich dieselbe Entität sind. Im Zweifel NICHT clustern.
- Erfinde keine Entitäten und führe nichts zusammen, das nur thematisch verwandt ist.
- Wähle als prefLabel den vollständigsten, offiziellen Namen.
- aliases = alle übrigen Namensvarianten der Gruppe.
- Nodes ohne Synonyme müssen NICHT ausgegeben werden.

Antworte NUR mit JSON in diesem Format, keine Erklärungen, kein Markdown:
{"clusters": [{"prefLabel": "Frankfurt am Main", "label": "Location", "aliases": ["Frankfurt", "FFM"], "member_ids": [12, 47, 103]}]}

Nodes:
"""

ARTICLE_PROMPT = """Du bist Illico, ein LLM-Bibliothekar. Du schreibst einen enzyklopädischen Wiki-Artikel
auf Basis von gecrawlten Website-Inhalten.

Schreibe einen Wiki-Artikel zum Thema: "{topic}"

Quellen (Inhalte aus raw/):
{sources}

Bekannte andere Wiki-Artikel (Slug → Titel):
{known_articles}

Regeln:
- Schreibe sachlich, klar, informativ
- Verwende Obsidian-Syntax mit Slug als Ziel: [[slug|Anzeigename]] (z.B. [[typo3-solutions|TYPO3-Lösungen]])
- Nutze NUR Slugs aus der Liste oben — keine erfundenen Links
- Strukturiere mit ## Überschriften
- Maximal 600 Wörter
- Schreibe in der Sprache der Quellen
- Erfinde nichts — nur was in den Quellen steht
- Frontmatter im YAML-Format oben

Format:
---
title: "Titel"
sources: ["datei1.md", "datei2.md"]
related: ["slug1.md", "slug2.md"]
compiled: "DATUM"
---

## Inhalt hier...
"""

INDEX_PROMPT = """Du bist Illico. Erstelle eine _index.md für eine Wiki mit folgenden Artikeln:

Artikel (Slug → Titel):
{articles}

Domain: {domain}
Hauptthemen: {topics}

Die _index.md soll:
- Eine kurze Beschreibung der Wissensbasis enthalten
- Alle Artikel mit einem Satz Beschreibung auflisten
- Obsidian-Links mit Slug als Ziel verwenden: [[slug|Anzeigename]] (z.B. [[typo3-solutions|TYPO3-Lösungen]])
- Auf Deutsch oder Englisch (je nach Domain-Sprache)
- Einen Abschnitt "Wie diese Wiki entstand" enthalten (Illico, Karpathy-Methode)

Schreibe NUR den Markdown-Inhalt, kein JSON.
"""

LINT_PROMPT = """Du bist Illico, ein kritischer Wiki-Bibliothekar. Analysiere diese Wiki-Artikel
auf Qualität, Vollständigkeit und Konsistenz.

Wichtig: Die Wiki-Inhalte unten können pro Artikel auf einen Auszug gekürzt sein
(Vorschau, nicht der ganze Artikel). Werte ein scheinbares Abbrechen am Auszug-Ende
NICHT als realen Mangel. Beurteile Vollständigkeit nur, wenn ein Artikel klar
inhaltlich unvollständig wirkt (fehlende Sektion, keine Quellen, kein Kontext).

Wiki-Inhalte:
{wiki_content}

Erstelle einen Lint-Report als Markdown mit folgenden Abschnitten:
## ✓ Stärken
## ⚠ Lücken & fehlende Artikel
## 🔗 Broken Links (Verlinkungen auf nicht existierende Artikel)
## 📝 Verbesserungsvorschläge
## 🆕 Empfohlene neue Artikel

Sei konkret und handlungsorientiert.
"""


# ─── Englische Prompts ────────────────────────────────────────────────────────

INVENTORY_PROMPT_EN = """You are Illico, an LLM librarian. You receive a list of Markdown files
crawled from a website. Your task is to produce a structured overview.

Analyze the files and produce a JSON inventory with the following structure:
{
  "domain": "domain.tld",
  "site_language": "de/en/...",
  "author_tone": "short description of the writing style",
  "main_topics": ["Topic 1", "Topic 2", ...],
  "clusters": [
    {
      "name": "Cluster name",
      "slug": "cluster-slug",
      "description": "short description",
      "files": ["file1.md", "file2.md"]
    }
  ],
  "key_entities": ["key people/products/organizations"],
  "suggested_articles": ["Article title 1", "Article title 2", ...]
}

Reply with JSON ONLY, no Markdown, no explanations.

File list and contents:
"""

MERGE_PROMPT_EN = """You are Illico, an LLM librarian. You receive several partial inventories of a crawled website.
Merge them into a single, consistent inventory.

Rules:
- Combine similar clusters (no duplicates)
- Every file must be assigned to a cluster
- At most 15 clusters
- Slugs must be unique (lowercase, no special characters)
- Merge main_topics from all partial inventories and deduplicate

Partial inventories:
{inventories}

Reply with the merged JSON only (same structure as the input), no Markdown, no explanations.
"""

EXTRACT_PROMPT_EN = """You are Illico, an LLM knowledge architect. Extract entities and relationships
from the following crawled website content as a knowledge graph.

Node labels (use only these):
- Organization — companies, agencies, associations, authorities
- Person — named individuals
- Product — software, tools, platforms, extensions
- Service — services, offerings
- Technology — programming languages, frameworks, standards
- Location — cities, countries, regions
- Event — conferences, meetups, workshops
- Certification — certifications, awards, accolades

Edge types (use only these):
- OFFERS — Organization offers Service/Product
- USES — Organization/Person uses Technology/Product
- LOCATED_IN — Organization/Person/Event has a location
- PARTNER_OF — Organization is partner of Organization
- CERTIFIED_FOR — Organization is certified for Product/Technology
- WORKS_AT — Person works at Organization
- DEVELOPED_BY — Product was developed by Organization/Person
- DEPENDS_ON — Product/Technology depends on Product/Technology
- SPECIALIZES_IN — Organization/Person specializes in Technology/Service
- PRESENTED_AT — Person/Organization presented at Event

For each node: assign a unique ID (integer, starting at 1), Label, Name and relevant properties.
For each edge: assign an ID, src (Node ID), dst (Node ID), rel (edge type) and properties.

Mark the knowledge layer in the properties of every node and edge:
- "source": "local" — extracted directly from the crawled content
- "source": "domain" — domain knowledge added by the LLM (e.g. "TYPO3 is a CMS")

Reply with JSON only in this format:
{
  "nodes": [{"id": 1, "label": "Organization", "name": "...", "props": {"source": "local", ...}}],
  "edges": [{"id": 1, "src": 1, "dst": 2, "rel": "OFFERS", "props": {"source": "local", ...}}]
}

No Markdown, no explanations. JSON only.

Content:
"""

MERGE_GRAPH_PROMPT_EN = """You are Illico, an LLM knowledge architect. You receive multiple partial graphs
of a crawled website. Merge them into a single, consistent knowledge graph.

Rules:
- Merge identical entities (e.g. "dkd" and "dkd Internet Service GmbH" → one node)
- Reassign IDs (consecutive starting at 1 for nodes, at 1 for edges)
- Remove duplicate edges
- Keep the "source" property (local > domain on conflicts)
- Do not change labels or relation types

Partial graphs:
{graphs}

Reply with the merged JSON only (same structure), no Markdown, no explanations.
"""

CANONICALIZE_PROMPT_EN = """You are Illico, an LLM knowledge architect. You receive a JSON list
of graph nodes (same label) with id and name. Find groups of nodes that denote the SAME
real-world entity — e.g. spelling variants, abbreviations, name suffixes
("Frankfurt" / "Frankfurt am Main"; "dkd" / "dkd Internet Service GmbH").

Rules:
- Only cluster nodes that are provably the same entity. When in doubt, do NOT cluster.
- Do not invent entities or merge things that are merely topically related.
- Choose the most complete, official name as prefLabel.
- aliases = all remaining name variants of the group.
- Nodes without synonyms need NOT be output.

Reply ONLY with JSON in this format, no explanations, no Markdown:
{"clusters": [{"prefLabel": "Frankfurt am Main", "label": "Location", "aliases": ["Frankfurt", "FFM"], "member_ids": [12, 47, 103]}]}

Nodes:
"""

ARTICLE_PROMPT_EN = """You are Illico, an LLM librarian. You write an encyclopedic wiki article
based on crawled website content.

Write a wiki article on the topic: "{topic}"

Sources (content from raw/):
{sources}

Other known wiki articles (slug → title):
{known_articles}

Rules:
- Write factually, clearly, informatively
- Use Obsidian syntax with the slug as the link target: [[slug|Display Name]] (e.g. [[typo3-solutions|TYPO3 solutions]])
- Use ONLY slugs from the list above — do not invent links
- Structure with ## headings
- At most 600 words
- Write in the language of the sources
- Invent nothing — only what appears in the sources
- YAML frontmatter on top

Format:
---
title: "Title"
sources: ["file1.md", "file2.md"]
related: ["slug1.md", "slug2.md"]
compiled: "DATE"
---

## Content here...
"""

INDEX_PROMPT_EN = """You are Illico. Create an _index.md for a wiki with the following articles:

Articles (slug → title):
{articles}

Domain: {domain}
Main topics: {topics}

The _index.md should:
- Contain a short description of the knowledge base
- List all articles with a one-sentence description
- Use Obsidian links with slug as target: [[slug|Display Name]] (e.g. [[typo3-solutions|TYPO3 solutions]])
- In English or German (matching the domain language)
- Include a section "How this wiki was built" (Illico, Karpathy method)

Write Markdown content only, no JSON.
"""

LINT_PROMPT_EN = """You are Illico, a critical wiki librarian. Analyze these wiki articles
for quality, completeness, and consistency.

Important: The wiki content below may be truncated per article (a preview, not the full
article). Do NOT report an article as incomplete just because its preview cuts off.
Judge completeness only when an article is clearly missing structure (no sections,
no sources, no context).

Wiki content:
{wiki_content}

Produce a lint report as Markdown with the following sections:
## ✓ Strengths
## ⚠ Gaps & missing articles
## 🔗 Broken links (links to non-existent articles)
## 📝 Improvement suggestions
## 🆕 Recommended new articles

Be concrete and action-oriented.
"""

# DE-Aliase (werden von get_prompts() referenziert)
INVENTORY_PROMPT_DE = INVENTORY_PROMPT
MERGE_PROMPT_DE = MERGE_PROMPT
EXTRACT_PROMPT_DE = EXTRACT_PROMPT
MERGE_GRAPH_PROMPT_DE = MERGE_GRAPH_PROMPT
CANONICALIZE_PROMPT_DE = CANONICALIZE_PROMPT
ARTICLE_PROMPT_DE = ARTICLE_PROMPT
INDEX_PROMPT_DE = INDEX_PROMPT
LINT_PROMPT_DE = LINT_PROMPT

@dataclass(frozen=True)
class Prompts:
    inventory: str
    merge: str
    extract: str
    merge_graph: str
    canonicalize: str
    article: str
    index: str
    lint: str


def get_prompts(lang: str | None) -> Prompts:
    """Returns the Prompts set for the given language (falls back to German)."""
    effective = (lang or "").strip().lower()
    if effective not in ("de", "en"):
        effective = "de"
    if effective == "en":
        return Prompts(
            inventory=INVENTORY_PROMPT_EN,
            merge=MERGE_PROMPT_EN,
            extract=EXTRACT_PROMPT_EN,
            merge_graph=MERGE_GRAPH_PROMPT_EN,
            canonicalize=CANONICALIZE_PROMPT_EN,
            article=ARTICLE_PROMPT_EN,
            index=INDEX_PROMPT_EN,
            lint=LINT_PROMPT_EN,
        )
    return Prompts(
        inventory=INVENTORY_PROMPT_DE,
        merge=MERGE_PROMPT_DE,
        extract=EXTRACT_PROMPT_DE,
        merge_graph=MERGE_GRAPH_PROMPT_DE,
        canonicalize=CANONICALIZE_PROMPT_DE,
        article=ARTICLE_PROMPT_DE,
        index=INDEX_PROMPT_DE,
        lint=LINT_PROMPT_DE,
    )


# ─── Hilfsfunktionen ──────────────────────────────────────────────────────────

def read_raw_files(raw_dir: Path) -> dict[str, str]:
    """Liest alle Markdown-Dateien aus raw/ ein."""
    files = {}
    for md_file in sorted(raw_dir.rglob("*.md")):
        rel = str(md_file.relative_to(raw_dir))
        content = md_file.read_text(encoding="utf-8")
        files[rel] = content
    return files


def _filter_raw_by_domains(raw_files: dict[str, str], allowed: set[str] | None) -> dict[str, str]:
    """Reduziert {rel: content} auf Files, deren Frontmatter-Domain in `allowed`
    liegt. `allowed=None` → unverändert. Files ohne erkennbare Domain fallen
    bei aktivem Filter raus (konservativ)."""
    if allowed is None:
        return raw_files
    out: dict[str, str] = {}
    for name, content in raw_files.items():
        d = extract_raw_domain(content)
        if d and d in allowed:
            out[name] = content
    return out


def _extract_frontmatter_language(content: str) -> Optional[str]:
    """Liest das language-Feld aus dem YAML-Frontmatter, sofern vorhanden."""
    if not content.lstrip().startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    fm = parts[1]
    for line in fm.splitlines():
        line = line.strip()
        if line.startswith("language:"):
            value = line.split(":", 1)[1].strip().strip('"').strip("'").lower()
            if value:
                return value.split("-")[0]
    return None


def filter_by_language(raw_files: dict[str, str], target_langs: list[str]) -> tuple[dict[str, str], int]:
    """Filtert raw_files nach Sprache. Bevorzugt Frontmatter `language`, fallback auf langdetect."""
    from illico_ingest import detect_language  # vermeidet zirkulaere Imports zur Modulladezeit

    kept: dict[str, str] = {}
    dropped = 0
    for path, content in raw_files.items():
        lang = _extract_frontmatter_language(content)
        if lang is None:
            lang = detect_language(None, content)
        # Unbekannte Sprache (None) wird NICHT gefiltert — konservativ.
        if lang and lang not in target_langs:
            dropped += 1
            continue
        kept[path] = content
    return kept, dropped


def call_llm(prompt: str, model: str, max_tokens: int = 2000, retries: int = 3) -> str:
    """Ruft das LLM auf und gibt den Text zurück."""
    messages = [{"role": "user", "content": prompt}]
    return illico_llm.call_sync(model, messages, max_tokens=max_tokens, retries=retries)


def truncate_for_context(files: dict[str, str], max_chars: int = 80000) -> str:
    """Fasst Dateiinhalte zusammen, respektiert Token-Limits."""
    parts = []
    total = 0
    for filename, content in files.items():
        snippet = content[:1500]  # Max 1500 Zeichen pro Datei für Inventar
        entry = f"### {filename}\n{snippet}\n"
        total += len(entry)
        if total > max_chars:
            parts.append(f"### {filename}\n[Inhalt gekürzt — {len(content)} Zeichen]\n")
        else:
            parts.append(entry)
    return "\n".join(parts)


# ─── Compile-Phasen ───────────────────────────────────────────────────────────

def parse_llm_json(response: str) -> dict | None:
    """Versucht JSON aus einer LLM-Antwort zu parsen."""
    clean = response.strip()
    # ```json ... ``` Wrapper entfernen
    if "```" in clean:
        import re
        m = re.search(r"```(?:json)?\s*\n(.*?)```", clean, re.DOTALL)
        if m:
            clean = m.group(1).strip()
    # Direkt versuchen
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    # Erstes { ... letztes } extrahieren
    start = clean.find("{")
    end = clean.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(clean[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def phase_inventory(raw_files: dict, model: str, prompts: Prompts) -> dict:
    """Phase 1: LLM erstellt ein Themen-Inventar der raw/ Dateien."""
    console.print("\n[bold blue]Phase 1:[/bold blue] Inventar erstellen...")

    filenames = list(raw_files.keys())

    # Bei wenigen Dateien: ein einzelner Call
    if len(filenames) <= BATCH_SIZE:
        return _inventory_single(raw_files, model, prompts)

    # Bei vielen Dateien: in Batches aufteilen, dann mergen
    console.print(f"  [dim]{len(filenames)} Dateien → {len(filenames) // BATCH_SIZE + 1} Batches[/dim]")

    partial_inventories = []
    for i in range(0, len(filenames), BATCH_SIZE):
        batch_files = {k: raw_files[k] for k in filenames[i:i + BATCH_SIZE]}
        batch_num = i // BATCH_SIZE + 1
        console.print(f"  [dim]Batch {batch_num} ({len(batch_files)} Dateien)...[/dim]")

        context = truncate_for_context(batch_files)
        prompt = prompts.inventory + context

        with console.status(f"[dim]Batch {batch_num}...[/dim]"):
            response = call_llm(prompt, model, max_tokens=4096)

        inventory = parse_llm_json(response)
        if inventory and inventory.get("clusters"):
            partial_inventories.append(inventory)
            console.print(f"  [green]✓[/green] Batch {batch_num}: {len(inventory['clusters'])} Cluster")
        else:
            console.print(f"  [yellow]⚠[/yellow] Batch {batch_num}: JSON-Parsing fehlgeschlagen")

    if not partial_inventories:
        console.print("[yellow]⚠ Keine Inventare erstellt, verwende Fallback[/yellow]")
        return _inventory_fallback(raw_files)

    if len(partial_inventories) == 1:
        return partial_inventories[0]

    # Merge-Pass
    console.print(f"  [dim]Merge: {len(partial_inventories)} Teil-Inventare zusammenführen...[/dim]")
    inv_text = "\n\n---\n\n".join(json.dumps(inv, ensure_ascii=False, indent=2) for inv in partial_inventories)
    merge_prompt = prompts.merge.format(inventories=inv_text[:80000])

    with console.status("[dim]Inventare zusammenführen...[/dim]"):
        response = call_llm(merge_prompt, model, max_tokens=8192)

    merged = parse_llm_json(response)
    if merged:
        console.print(f"  [green]✓[/green] {len(merged.get('clusters', []))} Cluster (zusammengeführt)")
        console.print(f"  [green]✓[/green] Hauptthemen: {', '.join(merged.get('main_topics', [])[:5])}")
        return merged

    console.print("[yellow]⚠ Merge fehlgeschlagen, kombiniere alle Teil-Inventare[/yellow]")
    return _concat_inventories(partial_inventories)


def _concat_inventories(inventories: list[dict]) -> dict:
    """Kombiniert alle Teil-Inventare ohne LLM — einfache Konkatenation mit Deduplizierung."""
    all_clusters = []
    seen_slugs = set()
    all_topics = []
    for inv in inventories:
        for cluster in inv.get("clusters", []):
            slug = cluster.get("slug", "")
            if slug and slug not in seen_slugs:
                seen_slugs.add(slug)
                all_clusters.append(cluster)
            elif slug in seen_slugs:
                # Dateien zu bestehendem Cluster hinzufügen
                for existing in all_clusters:
                    if existing.get("slug") == slug:
                        existing_files = set(existing.get("files", []))
                        existing_files.update(cluster.get("files", []))
                        existing["files"] = sorted(existing_files)
                        break
        all_topics.extend(inv.get("main_topics", []))
    # Topics deduplizieren
    seen_topics = set()
    unique_topics = []
    for t in all_topics:
        t_lower = t.lower()
        if t_lower not in seen_topics:
            seen_topics.add(t_lower)
            unique_topics.append(t)
    console.print(f"  [green]✓[/green] {len(all_clusters)} Cluster (konkateniert)")
    return {"clusters": all_clusters, "main_topics": unique_topics}


def _inventory_single(raw_files: dict, model: str, prompts: Prompts) -> dict:
    """Inventar für kleine Dateimengen (ein Call)."""
    context = truncate_for_context(raw_files)
    prompt = prompts.inventory + context

    with console.status("[dim]LLM analysiert Inhalte...[/dim]"):
        response = call_llm(prompt, model, max_tokens=4096)

    inventory = parse_llm_json(response)
    if inventory and inventory.get("clusters"):
        console.print(f"  [green]✓[/green] {len(inventory['clusters'])} Cluster gefunden")
        console.print(f"  [green]✓[/green] Hauptthemen: {', '.join(inventory.get('main_topics', [])[:5])}")
        return inventory

    console.print("[yellow]⚠ JSON-Parsing fehlgeschlagen, verwende Fallback-Inventar[/yellow]")
    return _inventory_fallback(raw_files)


def _inventory_fallback(raw_files: dict) -> dict:
    """Fallback-Inventar wenn LLM-Parsing fehlschlägt."""
    return {
        "domain": "unknown",
        "main_topics": ["Allgemein"],
        "clusters": [{"name": "Allgemein", "slug": "allgemein", "description": "Alle Inhalte", "files": list(raw_files.keys())}],
        "suggested_articles": ["Übersicht"],
        "key_entities": []
    }


def _extract_graph_batch(
    batch_files: dict,
    model: str,
    label: str,
    prompts: Prompts,
    max_tokens: int = 8192,
    depth: int = 0,
) -> list[dict]:
    """Extrahiert einen Graph-Batch. Bei Fehlschlag und >1 Datei: in zwei Hälften splitten und retry."""
    context = truncate_for_context(batch_files, max_chars=30000)
    prompt = prompts.extract + context

    with console.status(f"[dim]Entities extrahieren ({label})...[/dim]"):
        response = call_llm(prompt, model, max_tokens=max_tokens)

    graph = parse_llm_json(response)
    if graph and "nodes" in graph:
        console.print(f"  [green]✓[/green] {label}: {len(graph['nodes'])} Nodes, {len(graph.get('edges', []))} Edges")
        return [graph]

    if len(batch_files) <= 1 or depth >= 4:
        console.print(f"  [yellow]⚠[/yellow] {label}: Extraktion fehlgeschlagen ({len(batch_files)} Datei(en) aufgegeben)")
        return []

    items = list(batch_files.items())
    mid = len(items) // 2
    console.print(f"  [yellow]⟳[/yellow] {label}: Split-Retry ({len(items)} → {mid} + {len(items) - mid})")
    left = dict(items[:mid])
    right = dict(items[mid:])
    return (
        _extract_graph_batch(left, model, f"{label}.A", prompts, max_tokens, depth + 1)
        + _extract_graph_batch(right, model, f"{label}.B", prompts, max_tokens, depth + 1)
    )


def canonicalize_graph(
    graph: dict,
    model: str,
    prompts: Prompts,
    max_block_nodes: int = 120,
) -> dict:
    """Entity-Resolution: führt Synonym-Nodes zu kanonischen Nodes mit aliases zusammen.

    Arbeitet nur über Node-Identitäten ({id, label, name}) — pro Label-Block ein
    enger LLM-Call. Edges werden danach deterministisch umgehängt/dedupliziert.
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if not nodes:
        return {"nodes": [], "edges": edges}

    all_clusters: list[dict] = []
    blocks = block_nodes_by_label(nodes)
    for label, block_nodes in blocks.items():
        for chunk in split_block(block_nodes, max_block_nodes):
            payload = json.dumps(
                [{"id": n["id"], "label": n.get("label"), "name": n.get("name")}
                 for n in chunk if "id" in n],
                ensure_ascii=False,
            )
            with console.status(f"[dim]Canonicalize ({label}, {len(chunk)} Nodes)...[/dim]"):
                response = call_llm(prompts.canonicalize + payload, model, max_tokens=4000)
            parsed = parse_llm_json(response)
            if parsed and isinstance(parsed.get("clusters"), list):
                all_clusters.extend(parsed["clusters"])
            else:
                console.print(f"  [yellow]⚠[/yellow] Canonicalize-Block '{label}' ohne gültige Cluster — Nodes bleiben einzeln")

    # Deterministische Voll-Namen-Cluster ergänzen (Marke vs. Rechtsname): das
    # LLM sieht nur {id,label,name}, nie die props — fullName==name-Paare ergänzen.
    all_clusters.extend(fullname_clusters(nodes))
    all_clusters = merge_clusters_by_preflabel(all_clusters)
    # Eine fullName-Brücke verbindet zwei sonst getrennte LLM-Cluster (Marke/Rechtsname)
    # über einen geteilten Member — member-überlappende Cluster zu einer Entität vereinen.
    all_clusters = unify_overlapping_clusters(all_clusters)
    new_nodes, new_edges = apply_clusters(nodes, edges, all_clusters)
    return {"nodes": new_nodes, "edges": new_edges}


def phase_graph(
    raw_files: dict,
    graph_dir: Path,
    model: str,
    prompts: Prompts,
) -> dict:
    """Phase 1b: Extrahiert einen Knowledge Graph (Nodes + Edges) aus den raw/ Dateien.

    Schreibt nodes.json/edges.json/meta.json in graph_dir (z.B. graph/ oder graph-de/).
    """
    console.print("\n[bold blue]Phase 1b:[/bold blue] Knowledge Graph extrahieren...")

    filenames = list(raw_files.keys())
    partial_graphs = []

    graph_batch_size = 15  # Kleinere Batches für Graph-Extraktion (mehr Output pro Datei)

    for i in range(0, len(filenames), graph_batch_size):
        batch_files = {k: raw_files[k] for k in filenames[i:i + graph_batch_size]}
        batch_num = i // graph_batch_size + 1
        console.print(f"  [dim]Batch {batch_num} ({len(batch_files)} Dateien)...[/dim]")
        partial_graphs.extend(_extract_graph_batch(batch_files, model, f"Batch {batch_num}", prompts))

    if not partial_graphs:
        console.print("[yellow]⚠ Keine Graphen extrahiert[/yellow]")
        return {"nodes": [], "edges": []}

    # Bei einem Batch: direkt verwenden, sonst lokal mergen (IDs offset pro Batch)
    if len(partial_graphs) == 1:
        graph = partial_graphs[0]
    else:
        # Merge: IDs neu vergeben und zusammenführen
        console.print(f"  [dim]Merge: {len(partial_graphs)} Teil-Graphen zusammenführen...[/dim]")

        # Einfacher lokaler Merge: IDs offset pro Batch
        all_nodes = []
        all_edges = []
        node_offset = 0
        edge_offset = 0

        for pg in partial_graphs:
            id_map = {}
            for node in pg.get("nodes", []):
                if "id" not in node:
                    continue
                old_id = node["id"]
                new_id = node_offset + old_id
                id_map[old_id] = new_id
                all_nodes.append({**node, "id": new_id})
            for edge in pg.get("edges", []):
                # LLM-Output kann einzelne Edges ohne Pflichtfelder liefern.
                # Bei grossen Tenants (DKD: 52 Batches) reicht eine kaputte
                # Edge, um die gesamte Merge-Phase zu killen.
                if not all(k in edge for k in ("id", "src", "dst")):
                    continue
                new_edge_id = edge_offset + edge["id"]
                all_edges.append({
                    **edge,
                    "id": new_edge_id,
                    "src": id_map.get(edge["src"], edge["src"]),
                    "dst": id_map.get(edge["dst"], edge["dst"]),
                })
            node_offset += max((n["id"] for n in pg.get("nodes", []) if "id" in n), default=0) + 1
            edge_offset += max((e["id"] for e in pg.get("edges", []) if "id" in e), default=0) + 1

        graph = {"nodes": all_nodes, "edges": all_edges}

    # Entity-Resolution: Synonyme zu kanonischen Nodes mit aliases zusammenführen
    # (immer — auch bei nur einem Batch, sonst bleiben Synonyme innerhalb einer
    #  kleinen Site / eines Batches getrennt)
    graph = canonicalize_graph(graph, model, prompts)

    # Speichern
    graph_dir.mkdir(parents=True, exist_ok=True)

    nodes_path = graph_dir / "nodes.json"
    edges_path = graph_dir / "edges.json"
    meta_path = graph_dir / "meta.json"

    nodes_path.write_text(json.dumps(graph["nodes"], ensure_ascii=False, indent=2), encoding="utf-8")
    edges_path.write_text(json.dumps(graph.get("edges", []), ensure_ascii=False, indent=2), encoding="utf-8")
    meta_path.write_text(json.dumps({
        "name": "Illico Knowledge Graph",
        "description": f"Extrahiert aus {len(raw_files)} gecrawlten Seiten",
        "compiled": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    n_local = sum(1 for n in graph["nodes"] if n.get("props", {}).get("source") == "local")
    n_domain = sum(1 for n in graph["nodes"] if n.get("props", {}).get("source") == "domain")

    console.print(f"  [green]✓[/green] {len(graph['nodes'])} Nodes ({n_local} lokal, {n_domain} domain)")
    console.print(f"  [green]✓[/green] {len(graph.get('edges', []))} Edges")
    console.print(f"  [green]✓[/green] Gespeichert in {graph_dir}/")

    return graph


def _ensure_frontmatter(content: str, slug: str, title: str, source_files: list[str]) -> str:
    """Guarantees YAML frontmatter with sources on every compiled article.

    If the LLM omitted the frontmatter block (common with weaker models), this
    function prepends a minimal one derived from the known source file list so
    that downstream domain-based filtering (e.g. Cloud's per-tenant article
    filter) always has data to work with.
    """
    text = content.lstrip()
    has_frontmatter = text.startswith("---") or text.startswith("```yaml")
    if has_frontmatter:
        return content

    title_yaml = json.dumps(title, ensure_ascii=False)
    sources_yaml = json.dumps(source_files, ensure_ascii=False)
    today = datetime.now().strftime("%Y-%m-%d")
    injected = (
        f'---\ntitle: {title_yaml}\nsources: {sources_yaml}\ncompiled: "{today}"\n---\n\n'
    )
    return injected + content


def phase_articles(
    raw_files: dict,
    inventory: dict,
    wiki_dir: Path,
    model: str,
    prompts: Prompts,
) -> list[str]:
    """Phase 2: Pro Cluster einen Wiki-Artikel generieren."""
    console.print("\n[bold blue]Phase 2:[/bold blue] Wiki-Artikel schreiben...")

    wiki_dir.mkdir(parents=True, exist_ok=True)
    # Alte Artikel aus vorigen Compile-Läufen entfernen damit keine Orphans
    # mit fehlendem Frontmatter die Tenant-Sicht verunreinigen. Underscore-
    # Dateien (_index.md, _lint-report.md) werden von späteren Phasen geschrieben.
    for old in wiki_dir.glob("*.md"):
        if not old.name.startswith("_"):
            old.unlink()

    created_articles = []
    clusters = inventory.get("clusters", [])

    for i, cluster in enumerate(clusters):
        name = cluster.get("name", f"Artikel {i+1}")
        slug = cluster.get("slug", f"artikel-{i+1}")
        source_files = cluster.get("files", [])

        console.print(f"  [dim]→ {name}[/dim]")

        # Quellen zusammenstellen
        sources_content = ""
        for fname in source_files[:5]:  # Max 5 Quelldateien pro Artikel
            if fname in raw_files:
                sources_content += f"**{fname}:**\n{raw_files[fname][:2000]}\n\n"

        if not sources_content:
            # Fallback: alle raw-Dateien anteilig
            sources_content = truncate_for_context(raw_files, max_chars=6000)

        known = [f"  {c.get('slug', 'unknown')} → {c.get('name', '?')}" for c in clusters if c.get("name") != name]
        prompt = prompts.article.format(
            topic=name,
            sources=sources_content[:6000],
            known_articles="\n".join(known[:20])
        ).replace("DATUM", datetime.now().strftime("%Y-%m-%d"))

        with console.status(f"[dim]{name}...[/dim]"):
            content = call_llm(prompt, model, max_tokens=4000)

        content = _ensure_frontmatter(content, slug, name, source_files)

        # Speichern
        article_path = wiki_dir / f"{slug}.md"
        article_path.write_text(content, encoding="utf-8")
        created_articles.append((slug, name))
        console.print(f"  [green]✓[/green] {slug}.md")

    return created_articles


def phase_index(
    inventory: dict,
    created_articles: list[str],
    wiki_dir: Path,
    model: str,
    prompts: Prompts,
) -> None:
    """Phase 3: _index.md als Einstiegspunkt der Wiki erstellen."""
    console.print("\n[bold blue]Phase 3:[/bold blue] Index erstellen...")

    article_list = "\n".join(f"  {slug} → {name}" for slug, name in created_articles)
    prompt = prompts.index.format(
        articles=article_list,
        domain=inventory.get("domain", "unbekannt"),
        topics=", ".join(inventory.get("main_topics", []))
    )

    with console.status("[dim]Index wird geschrieben...[/dim]"):
        content = call_llm(prompt, model, max_tokens=2000)

    index_path = wiki_dir / "_index.md"
    index_path.write_text(content, encoding="utf-8")
    console.print(f"  [green]✓[/green] _index.md")


def phase_lint(wiki_dir: Path, model: str, prompts: Prompts) -> None:
    """Phase 4: Linting-Pass — Wiki auf Qualität und Lücken prüfen."""
    console.print("\n[bold blue]Phase 4:[/bold blue] Linting...")

    wiki_files = list(wiki_dir.glob("*.md"))
    wiki_content = ""
    for f in wiki_files:
        wiki_content += f"### {f.name}\n{f.read_text(encoding='utf-8')[:3000]}\n\n"

    prompt = prompts.lint.format(wiki_content=wiki_content[:40000])

    with console.status("[dim]LLM analysiert Wiki...[/dim]"):
        report = call_llm(prompt, model, max_tokens=4000)

    lint_path = wiki_dir / "_lint-report.md"
    lint_path.write_text(
        f"---\ngenerated: \"{datetime.now().strftime('%Y-%m-%d %H:%M')}\"\n---\n\n" + report,
        encoding="utf-8"
    )
    console.print(f"  [green]✓[/green] _lint-report.md")

    # Preview im Terminal
    console.print()
    console.print(Panel(report[:800] + ("..." if len(report) > 800 else ""),
                        title="[bold]Lint Report (Vorschau)[/bold]",
                        border_style="yellow"))


# ─── CLI ──────────────────────────────────────────────────────────────────────

@app.command()
def compile(
    data: Path = typer.Option(Path(os.environ.get("ILLICO_DATA", "./illico-data")), "--data", "-d", help="Illico-Datenverzeichnis"),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="LLM-Modell (default: ILLICO_ANSWER_MODEL env)"),
    lint_only: bool = typer.Option(False, "--lint", help="Nur Linting-Pass ausführen"),
    graph_only: bool = typer.Option(False, "--graph-only", help="Nur Knowledge-Graph neu extrahieren (Phase 1b)"),
    canonicalize_only: bool = typer.Option(False, "--canonicalize-only", help="Nur Entity-Resolution über bestehenden Graph laufen lassen (kein Re-Compile)"),
    only_domains: Optional[str] = typer.Option(None, "--only-domains", help="Nur Raw-Dateien dieser Domains (Komma-getrennt) kompilieren."),
    lang: Optional[str] = typer.Option(None, "--lang", help="Nur raw/-Dateien dieser Sprache(n) ins Wiki uebernehmen, ISO 639-1 kommagetrennt (z.B. 'de' oder 'de,en')"),
    wiki_dir_name: Optional[str] = typer.Option(None, "--wiki-dir", help="Name des Wiki-Verzeichnisses (default: 'wiki' bzw. 'wiki-<lang>' wenn genau eine Sprache via --lang gesetzt ist)"),
):
    """
    Kompiliert raw/ Markdown-Dateien zu einer strukturierten Wiki.
    """
    console.print()
    console.rule("[bold blue]ILLICO COMPILE[/bold blue]")

    effective_model = model or illico_llm.ANSWER_MODEL

    # Verzeichnisse — Wiki-/Graph-Ordner abhaengig von --lang/--wiki-dir
    raw_dir = data / "raw"
    single_lang_suffix = (
        f"-{lang.strip().lower()}" if (lang and "," not in lang) else ""
    )

    if wiki_dir_name:
        wiki_dir = data / wiki_dir_name
        # Graph-/Inventory-Namen parallel zum Wiki ableiten:
        # wiki-de → graph-de/_inventory-de.json, wiki → graph/_inventory.json
        if wiki_dir_name.startswith("wiki"):
            graph_dir = data / ("graph" + wiki_dir_name[len("wiki"):])
            inv_path_name = "_inventory" + wiki_dir_name[len("wiki"):] + ".json"
        else:
            graph_dir = data / "graph"
            inv_path_name = "_inventory.json"
    else:
        wiki_dir = data / f"wiki{single_lang_suffix}"
        graph_dir = data / f"graph{single_lang_suffix}"
        inv_path_name = "_inventory.json"

    if not raw_dir.exists() or not any(raw_dir.rglob("*.md")):
        console.print(f"[red]✗ Keine Dateien in {raw_dir} gefunden.[/red]")
        console.print("  Zuerst ausführen: [cyan]python ingest.py <url>[/cyan]")
        raise typer.Exit(1)

    # Raw-Dateien einlesen
    raw_files = read_raw_files(raw_dir)

    # Domain-Filter (optional) VOR Sprachfilter, damit Inventory nur die
    # gewünschten Domains sieht
    allowed_domains = (
        {d.strip().lower() for d in only_domains.split(",") if d.strip()}
        if only_domains else None
    )
    if allowed_domains is not None:
        before = len(raw_files)
        raw_files = _filter_raw_by_domains(raw_files, allowed_domains)
        console.print(
            f"  Domains: [cyan]{', '.join(sorted(allowed_domains))}[/cyan] — "
            f"{len(raw_files)}/{before} Raw-Dateien"
        )
        if not raw_files:
            console.print(
                "[red]✗ Keine Raw-Dateien fuer diese Domains. "
                "Erst illico_ingest.py fuer diese Domains laufen lassen.[/red]"
            )
            raise typer.Exit(1)

    # Sprachfilter (optional): Frontmatter `language` bevorzugt, Fallback langdetect.
    if lang:
        target_langs = [l.strip().lower() for l in lang.split(",") if l.strip()]
        total_before = len(raw_files)
        raw_files, dropped = filter_by_language(raw_files, target_langs)
        console.print(f"  Sprache: [cyan]{', '.join(target_langs)}[/cyan] — {dropped}/{total_before} Dateien gefiltert")
        if not raw_files:
            console.print(f"[red]✗ Nach Sprachfilter keine Dateien mehr uebrig.[/red]")
            raise typer.Exit(1)

    # Prompts auf die Sprache umschalten (Fallback: Deutsch)
    prompt_lang = target_langs[0] if (lang and len(target_langs) == 1) else None
    prompts = get_prompts(prompt_lang)
    effective_prompt_lang = prompt_lang if prompt_lang in ("de", "en") else "de"
    console.print(f"  Prompts: [cyan]{effective_prompt_lang}[/cyan]")

    console.print(f"  Modell:  [cyan]{effective_model}[/cyan]")
    console.print(f"  Dateien: [cyan]{len(raw_files)} raw/*.md[/cyan]")
    console.print(f"  Output:  [cyan]{wiki_dir}[/cyan]")

    if sum([lint_only, graph_only, canonicalize_only]) > 1:
        console.print("[red]✗ --lint, --graph-only und --canonicalize-only sind nicht kombinierbar.[/red]")
        raise typer.Exit(2)

    try:
        if canonicalize_only:
            nodes_path = graph_dir / "nodes.json"
            edges_path = graph_dir / "edges.json"
            if not nodes_path.exists() or not edges_path.exists():
                console.print(f"[red]✗ Kein Graph in {graph_dir} gefunden. Erst compile/--graph-only ausführen.[/red]")
                raise typer.Exit(1)
            console.print(f"  Graph:   [cyan]{graph_dir}[/cyan]")
            nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
            edges = json.loads(edges_path.read_text(encoding="utf-8"))
            console.print("\n[bold blue]Canonicalize:[/bold blue] Entity-Resolution über bestehenden Graph...")
            result = canonicalize_graph({"nodes": nodes, "edges": edges}, effective_model, prompts)
            nodes_path.write_text(json.dumps(result["nodes"], ensure_ascii=False, indent=2), encoding="utf-8")
            edges_path.write_text(json.dumps(result["edges"], ensure_ascii=False, indent=2), encoding="utf-8")
            console.print(f"  [green]✓[/green] {len(result['nodes'])} Nodes, {len(result['edges'])} Edges (kanonisiert)")
        elif lint_only:
            if not wiki_dir.exists():
                console.print(f"[red]✗ Kein wiki/ Verzeichnis gefunden. Erst compile ausführen.[/red]")
                raise typer.Exit(1)
            phase_lint(wiki_dir, effective_model, prompts)
        elif graph_only:
            console.print(f"  Graph:   [cyan]{graph_dir}[/cyan]")
            phase_graph(raw_files, graph_dir, effective_model, prompts)
        else:
            # Vollständiger Compile-Durchlauf
            inventory = phase_inventory(raw_files, effective_model, prompts)

            # Inventar speichern (für Debugging)
            inv_path = data / inv_path_name
            inv_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")

            # Knowledge Graph extrahieren (sprachabhaengig — graph-<lang>/ bzw. graph/)
            graph = phase_graph(raw_files, graph_dir, effective_model, prompts)

            created = phase_articles(raw_files, inventory, wiki_dir, effective_model, prompts)
            phase_index(inventory, created, wiki_dir, effective_model, prompts)
            phase_lint(wiki_dir, effective_model, prompts)
    except illico_llm.LLMAuthError as exc:
        console.print(f"[red]✗ LLM authentication failed: {exc}[/red]")
        console.print("  Check your provider API key and ILLICO_ANSWER_MODEL.")
        raise typer.Exit(1)

    # Zusammenfassung
    console.print()
    console.rule("[bold green]Fertig[/bold green]")
    wiki_files = list(wiki_dir.glob("*.md")) if wiki_dir.exists() else []
    console.print(f"  [green]{len(wiki_files)} Wiki-Dateien[/green] in {wiki_dir}")
    if graph_dir.exists() and (graph_dir / "nodes.json").exists():
        nodes = json.loads((graph_dir / "nodes.json").read_text())
        edges = json.loads((graph_dir / "edges.json").read_text())
        console.print(f"  [green]{len(nodes)} Nodes, {len(edges)} Edges[/green] in {graph_dir}")
    console.print()
    console.print("[bold blue]Nächster Schritt:[/bold blue] [cyan]python chat.py[/cyan]")


if __name__ == "__main__":
    app()
