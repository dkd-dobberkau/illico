"""
illico_canonicalize — deterministische Entity-Resolution für den Knowledge Graph.

Reine Funktionen ohne LLM-/IO-Abhängigkeit: Nodes nach Label blocken, vom LLM
gelieferte Cluster auf Nodes/Edges anwenden (Merge, Edge-Remap, Dedup).
"""

from __future__ import annotations


def apply_clusters(
    nodes: list[dict],
    edges: list[dict],
    clusters: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Wendet LLM-Cluster deterministisch an.

    - Je gültigem Cluster genau ein kanonischer Node (name=prefLabel, aliases).
    - Nodes ohne Cluster bleiben als Singleton erhalten.
    - Edges werden auf kanonische IDs umgehängt, Merge-Self-Loops verworfen,
      gleiche (src, dst, rel) dedupliziert (props.source: local > domain).
    IDs werden fortlaufend ab 1 neu vergeben.
    """
    by_old_id = {n["id"]: n for n in nodes if "id" in n}
    id_map: dict[int, int] = {}
    new_nodes: list[dict] = []
    next_id = 1

    # 1) Kanonische Nodes aus Clustern
    for cl in clusters:
        pref = cl.get("prefLabel")
        label = cl.get("label")
        members = [m for m in cl.get("member_ids", []) if m in by_old_id]
        if not pref or not label or not members:
            continue  # ungültiger Cluster → Member fallen in Singleton-Pfad
        source = "domain"
        for m in members:
            if by_old_id[m].get("props", {}).get("source") == "local":
                source = "local"
                break
        aliases = [a for a in dict.fromkeys(cl.get("aliases", []) or []) if a and a != pref]
        # props aller Member vereinen (konsistent zum Singleton-Pfad, der {**n} behält);
        # bei Konflikt gewinnt der local-Wert. `source` bleibt die oben ermittelte
        # local>domain-Wertung.
        merged_props: dict = {}
        for m in members:
            m_props = by_old_id[m].get("props", {}) or {}
            m_local = m_props.get("source") == "local"
            for k, v in m_props.items():
                if k == "source":
                    continue
                if k not in merged_props or m_local:
                    merged_props[k] = v
        merged_props["source"] = source
        new_nodes.append({
            "id": next_id, "label": label, "name": pref,
            "aliases": aliases, "props": merged_props,
        })
        for m in members:
            id_map[m] = next_id
        next_id += 1

    # 2) Singletons (nicht von einem Cluster erfasst)
    for n in nodes:
        oid = n.get("id")
        if oid is None or oid in id_map:
            continue
        node = {**n, "id": next_id, "aliases": list(n.get("aliases", []))}
        new_nodes.append(node)
        id_map[oid] = next_id
        next_id += 1

    # 3) Edges umhängen + dedup
    seen: dict[tuple, dict] = {}
    for e in edges:
        if not all(k in e for k in ("src", "dst")):
            continue
        src = id_map.get(e["src"])
        dst = id_map.get(e["dst"])
        if src is None or dst is None or src == dst:
            continue
        rel = e.get("rel")
        key = (src, dst, rel)
        cand = {**e, "src": src, "dst": dst}
        prev = seen.get(key)
        if prev is None:
            seen[key] = cand
        elif cand.get("props", {}).get("source") == "local" and prev.get("props", {}).get("source") != "local":
            seen[key] = cand

    new_edges = []
    for i, e in enumerate(seen.values(), start=1):
        new_edges.append({**e, "id": i})

    return new_nodes, new_edges


def _norm(s: str) -> str:
    """Normalisiert einen String für Vergleich: lowercase + strip."""
    return (s or "").strip().lower()


# props-Keys, die den vollständigen Namen einer Entität tragen (Marke vs.
# Rechtsname). Bewusst eng gehalten — keine mehrdeutigen Felder wie full_title.
_FULLNAME_KEYS = ("fullName", "full_name", "fullname", "longname")


def fullname_clusters(nodes: list[dict]) -> list[dict]:
    """Deterministische Cluster aus Voll-Namen in den props.

    Wenn `props.fullName` (o.ä.) eines Nodes exakt (normalisiert) dem `name`
    eines anderen Nodes **gleicher Label** entspricht, sind beide dieselbe Entität
    (z.B. „Mankiewicz" + props.fullName == „Mankiewicz Gebr. & Co. (GmbH & Co. KG)").
    Das LLM-Canonicalize sieht nur {id, label, name} — nie die props — und verfehlt
    diese Paare. Diese Regel schließt die Lücke ohne LLM und mit hoher Präzision
    (exakter Namens-Match Pflicht). prefLabel = der Voll-Name (kanonisch), der
    Kurzname wird Alias.
    """
    by_label_name: dict[tuple, dict] = {}
    for n in nodes:
        if "id" in n:
            by_label_name[(n.get("label"), _norm(n.get("name", "")))] = n

    clusters: list[dict] = []
    seen_pairs: set[frozenset] = set()
    for a in nodes:
        if "id" not in a:
            continue
        props = a.get("props") or {}
        label = a.get("label")
        a_name = a.get("name", "")
        a_norm = _norm(a_name)
        for key in _FULLNAME_KEYS:
            full = props.get(key)
            if not isinstance(full, str):
                continue
            full_norm = _norm(full)
            if not full_norm or full_norm == a_norm:
                continue
            b = by_label_name.get((label, full_norm))
            if b is None or b.get("id") == a.get("id"):
                continue
            pair = frozenset((a["id"], b["id"]))
            if pair in seen_pairs:
                break
            seen_pairs.add(pair)
            clusters.append({
                "prefLabel": b.get("name"),
                "label": label,
                "aliases": [a_name],
                "member_ids": [a["id"], b["id"]],
            })
            break
    return clusters


def unify_overlapping_clusters(clusters: list[dict]) -> list[dict]:
    """Vereint Cluster, die sich mindestens einen `member_id` teilen, zu einem.

    Nötig, weil eine fullName-Brücke (2 Member) zwei vom LLM getrennt gebildete
    Cluster (z.B. „Mankiewicz" mit 40 Membern und „…KG" mit 6) verbindet — beide
    sind dieselbe Entität. Union-Find über die Member; prefLabel des Ergebnisses
    ist das prefLabel des Clusters mit den meisten Membern (Tie → lexikografisch
    kleinstes, deterministisch), alle anderen prefLabels/Aliase werden Aliase.
    Disjunkte Cluster bleiben unverändert (im Normalfall ein No-op).
    """
    n = len(clusters)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    member_owner: dict = {}
    for idx, cl in enumerate(clusters):
        for m in cl.get("member_ids", []) or []:
            if m in member_owner:
                union(member_owner[m], idx)
            else:
                member_owner[m] = idx

    comps: dict[int, list[int]] = {}
    for idx in range(n):
        comps.setdefault(find(idx), []).append(idx)

    merged: list[dict] = []
    for idxs in comps.values():
        group = [clusters[i] for i in idxs]
        if len(group) == 1:
            merged.append(group[0])
            continue
        max_len = max(len(c.get("member_ids", []) or []) for c in group)
        pref = sorted(
            c.get("prefLabel", "") for c in group
            if len(c.get("member_ids", []) or []) == max_len
        )[0]
        member_ids: list = []
        aliases: list = []
        for c in group:
            for m in c.get("member_ids", []) or []:
                if m not in member_ids:
                    member_ids.append(m)
            for a in [c.get("prefLabel", "")] + list(c.get("aliases", []) or []):
                if a and a != pref and a not in aliases:
                    aliases.append(a)
        merged.append({
            "prefLabel": pref,
            "label": group[0].get("label"),
            "aliases": aliases,
            "member_ids": member_ids,
        })
    return merged


def block_nodes_by_label(nodes: list[dict]) -> dict[str, list[dict]]:
    """Gruppiert Nodes nach `label` (fehlendes Label → '_')."""
    blocks: dict[str, list[dict]] = {}
    for n in nodes:
        blocks.setdefault(n.get("label") or "_", []).append(n)
    return blocks


def split_block(nodes: list[dict], max_nodes: int) -> list[list[dict]]:
    """Splittet einen Block alphabetisch nach `name` in Chunks <= max_nodes."""
    if len(nodes) <= max_nodes:
        return [nodes]
    ordered = sorted(nodes, key=lambda n: _norm(n.get("name", "")))
    return [ordered[i:i + max_nodes] for i in range(0, len(ordered), max_nodes)]


def merge_clusters_by_preflabel(clusters: list[dict]) -> list[dict]:
    """Fasst Cluster mit gleichem (label, normalisiertes prefLabel) zusammen."""
    merged: dict[tuple, dict] = {}
    for cl in clusters:
        key = (cl.get("label"), _norm(cl.get("prefLabel", "")))
        if key not in merged:
            merged[key] = {
                "prefLabel": cl.get("prefLabel"),
                "label": cl.get("label"),
                "aliases": list(cl.get("aliases", []) or []),
                "member_ids": list(cl.get("member_ids", []) or []),
            }
        else:
            tgt = merged[key]
            tgt["aliases"].extend(a for a in (cl.get("aliases", []) or []) if a not in tgt["aliases"])
            tgt["member_ids"].extend(m for m in (cl.get("member_ids", []) or []) if m not in tgt["member_ids"])
    return list(merged.values())
