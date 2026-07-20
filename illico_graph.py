"""
illico_graph — shared graph utilities for entity-aware article routing.

Loads a knowledge graph (nodes + edges) and uses it to:
- annotate articles with the entities they mention,
- expand a set of selected articles by walking one hop in the graph.
"""

import json
from pathlib import Path


def _graph_dir_candidates(data_dir: Path, lang: str | None, namespace: str | None = None) -> list[Path]:
    """Liefert Kandidaten-Ordner in Praeferenz-Reihenfolge.

    Namespace: graph-<CODE>-<lang>/ → graph-<CODE>/   (kein globaler Fallback)
    Global:    graph-<lang>/        → graph/
    """
    candidates: list[Path] = []
    if namespace:
        if lang:
            candidates.append(data_dir / f"graph-{namespace}-{lang.strip().lower()}")
        candidates.append(data_dir / f"graph-{namespace}")
    else:
        if lang:
            candidates.append(data_dir / f"graph-{lang.strip().lower()}")
        candidates.append(data_dir / "graph")
    return candidates


def load_graph_data(data_dir: Path, lang: str | None = None, namespace: str | None = None) -> tuple[list, list]:
    """Load nodes.json und edges.json — bevorzugt graph-<namespace>/, Fallback graph/.

    Returns (nodes, edges). Returns ([], []) wenn nichts gefunden.
    """
    for graph_dir in _graph_dir_candidates(data_dir, lang, namespace):
        nodes_path = graph_dir / "nodes.json"
        edges_path = graph_dir / "edges.json"
        if not nodes_path.exists() or not edges_path.exists():
            continue
        with open(nodes_path, encoding="utf-8") as f:
            nodes = json.load(f)
        with open(edges_path, encoding="utf-8") as f:
            edges = json.load(f)
        return nodes, edges
    return [], []


def load_graph_meta(data_dir: Path, lang: str | None = None, namespace: str | None = None) -> dict:
    """Load meta.json — bevorzugt graph-<namespace>/, Fallback graph/."""
    for graph_dir in _graph_dir_candidates(data_dir, lang, namespace):
        meta_path = graph_dir / "meta.json"
        if meta_path.exists():
            with open(meta_path, encoding="utf-8") as f:
                return json.load(f)
    return {}


def build_article_entity_map(articles: dict, nodes: list) -> dict[str, list[str]]:
    """For each article (skip _-prefixed), find which graph entities appear in
    the content (case-insensitive). Matcht über Node-`name` UND `aliases`;
    in die Ergebnisliste kommt immer der kanonische `name`.

    Returns dict[article_name, list[entity_name]].
    """
    # (term_lower, canonical_name) je Node-Name und je Alias
    terms: list[tuple[str, str]] = []
    for n in nodes:
        name = n.get("name")
        if not name:
            continue
        terms.append((name.lower(), name))
        for alias in n.get("aliases", []) or []:
            if alias:
                terms.append((alias.lower(), name))

    result: dict[str, list[str]] = {}
    for article_name, content in articles.items():
        if article_name.startswith("_"):
            continue
        content_lower = content.lower()
        found: list[str] = []
        seen: set[str] = set()
        for term_lower, canonical in terms:
            if term_lower in content_lower and canonical not in seen:
                seen.add(canonical)
                found.append(canonical)
        if found:
            result[article_name] = found

    return result


def restrict_to_articles(
    articles: dict,
    nodes: list,
    edges: list,
) -> tuple[list, list]:
    """Beschränkt nodes/edges auf Entitäten, die in den gegebenen Artikeln vorkommen.

    Verwendet build_article_entity_map (substring-match), kann also bei Namens-
    Überlappungen Entitäten aus anderen Artikeln einschließen — siehe Spec
    'Sicherheitsüberlegungen'.
    """
    entity_map = build_article_entity_map(articles, nodes)
    allowed_entities = {e for ents in entity_map.values() for e in ents}
    allowed_ids = {n["id"] for n in nodes if n.get("name") in allowed_entities}
    fnodes = [n for n in nodes if n["id"] in allowed_ids]
    fedges = [e for e in edges if e["src"] in allowed_ids and e["dst"] in allowed_ids]
    return fnodes, fedges


def expand_with_graph(
    selected_articles: list[str],
    articles: dict,
    nodes: list,
    edges: list,
) -> list[str]:
    """Walk one hop from entities in selected articles via edges, find
    additional articles that mention at least 2 neighbor entities.

    Returns selected + max 1 extra article.
    """
    if not nodes or not edges or not selected_articles:
        return list(selected_articles)

    # Build article-entity map
    entity_map = build_article_entity_map(articles, nodes)

    # Collect entity names from selected articles
    selected_entities: set[str] = set()
    for art in selected_articles:
        selected_entities.update(entity_map.get(art, []))

    if not selected_entities:
        return list(selected_articles)

    # Build node-id lookup by name
    name_to_id: dict[str, int] = {}
    id_to_name: dict[int, str] = {}
    for n in nodes:
        name_to_id[n["name"]] = n["id"]
        id_to_name[n["id"]] = n["name"]

    # Find entity ids for selected entities
    selected_ids = {name_to_id[e] for e in selected_entities if e in name_to_id}

    # Walk one hop: collect neighbor node ids
    neighbor_ids: set[int] = set()
    for edge in edges:
        if edge["src"] in selected_ids:
            neighbor_ids.add(edge["dst"])
        if edge["dst"] in selected_ids:
            neighbor_ids.add(edge["src"])

    # Exclude the original entity ids — we want new neighbors only
    neighbor_ids -= selected_ids

    # Convert neighbor ids back to names
    neighbor_names = {id_to_name[nid] for nid in neighbor_ids if nid in id_to_name}

    if not neighbor_names:
        return list(selected_articles)

    # Find candidate articles (not already selected) mentioning >= 2 neighbor entities
    selected_set = set(selected_articles)
    best_candidate: str | None = None
    best_count = 0

    for art_name, art_entities in entity_map.items():
        if art_name in selected_set or art_name.startswith("_"):
            continue
        overlap = len(neighbor_names.intersection(art_entities))
        if overlap >= 2 and overlap > best_count:
            best_count = overlap
            best_candidate = art_name

    result = list(selected_articles)
    if best_candidate:
        result.append(best_candidate)

    return result


REL_DE: dict[str, str] = {
    "OFFERS": "bietet an",
    "USES": "nutzt",
    "DEPENDS_ON": "hängt ab von",
    "WORKS_AT": "arbeitet bei",
    "SPECIALIZES_IN": "spezialisiert sich auf",
    "LOCATED_IN": "ist ansässig in",
    "PARTNER_OF": "ist Partner von",
    "PRESENTED_AT": "präsentierte bei",
    "CERTIFIED_FOR": "ist zertifiziert für",
    "DEVELOPED_BY": "wird entwickelt von",
}


def build_graph_context(
    selected_articles: list[str],
    articles: dict,
    nodes: list,
    edges: list,
    max_facts: int = 40,
    max_descriptions: int = 15,
) -> str:
    """Baut einen Markdown-Block mit Entity-Beschreibungen und lesbaren
    Relations-Sätzen für die in den ausgewählten Artikeln vorkommenden
    Entitäten. Liefert "" wenn nichts vorhanden.

    Priorisiert Kanten, deren beide Endpunkte ausgewählt sind (interne
    Struktur), vor 1-Hop-Kanten. Deckelt auf max_facts / max_descriptions.
    Baut nur auf den übergebenen nodes/edges auf (Tenant-Isolation).
    """
    if not nodes or not selected_articles:
        return ""

    entity_map = build_article_entity_map(articles, nodes)
    selected_entities: list[str] = []
    seen_ent: set[str] = set()
    for art in selected_articles:
        for ent in entity_map.get(art, []):
            if ent not in seen_ent:
                seen_ent.add(ent)
                selected_entities.append(ent)

    if not selected_entities:
        return ""

    name_to_id: dict[str, int] = {}
    id_to_name: dict[int, str] = {}
    id_to_node: dict[int, dict] = {}
    for n in nodes:
        name = n.get("name")
        if name is None:
            continue
        name_to_id[name] = n["id"]
        id_to_name[n["id"]] = name
        id_to_node[n["id"]] = n

    selected_ids = {name_to_id[e] for e in selected_entities if e in name_to_id}

    # Inzidente Kanten sammeln, intern (beide Endpunkte ausgewählt) zuerst
    internal: list[tuple[str, str, str]] = []
    onehop: list[tuple[str, str, str]] = []
    seen_triples: set[tuple[int, str, int]] = set()
    for e in edges:
        src, dst, rel = e.get("src"), e.get("dst"), e.get("rel", "")
        if src not in selected_ids and dst not in selected_ids:
            continue
        key = (src, rel, dst)
        if key in seen_triples:
            continue
        seen_triples.add(key)
        if src not in id_to_name or dst not in id_to_name:
            continue
        triple = (id_to_name[src], REL_DE.get(rel, rel), id_to_name[dst])
        if src in selected_ids and dst in selected_ids:
            internal.append(triple)
        else:
            onehop.append(triple)

    facts = (internal + onehop)[:max_facts]

    # Beschreibungen für ausgewählte Entitäten
    descriptions: list[str] = []
    for ent in selected_entities:
        if len(descriptions) >= max_descriptions:
            break
        node = id_to_node.get(name_to_id.get(ent, -1))
        if not node:
            continue
        desc = (node.get("props") or {}).get("description")
        if not desc:
            continue
        label = node.get("label", "")
        suffix = f" ({label})" if label else ""
        descriptions.append(f"- **{ent}**{suffix}: {desc}")

    if not facts and not descriptions:
        return ""

    parts: list[str] = ["## Wissensgraph"]
    if descriptions:
        parts.append("\n### Entitäten\n" + "\n".join(descriptions))
    if facts:
        rel_lines = "\n".join(f"- {s} {r} {d}" for s, r, d in facts)
        parts.append("\n### Beziehungen\n" + rel_lines)
    return "\n".join(parts)
