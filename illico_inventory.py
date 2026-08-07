"""Persistiertes Inventar: welches Destillat gehoert zu welchem Artikel.

Slugs sind unveraenderlich, Fingerprints deterministisch. Zusammen entscheiden
sie, welche Artikel ein Lauf ueberhaupt anfassen muss.
"""
import hashlib
import json
import os
from pathlib import Path

SCHEMA = 1


def fingerprint(members: list[str]) -> str:
    """SHA-256 ueber die sortierte Mitgliederliste — deterministisch und
    unabhaengig davon, was das LLM in den Artikel schreibt."""
    payload = "\n".join(sorted(members))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_inventory(path: Path) -> dict:
    """Laedt das Inventar. Fehlt es oder stammt es aus der Zeit vor der
    Inkrementalitaet (kein `schema`), wird die Struktur einmalig neu gebaut."""
    path = Path(path)
    if not path.exists():
        return {"schema": SCHEMA, "clusters": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema": SCHEMA, "clusters": []}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return {"schema": SCHEMA, "clusters": []}
    return data


def save_inventory(path: Path, inventory: dict) -> None:
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def assigned_hashes(inventory: dict) -> set[str]:
    return {h for c in inventory.get("clusters", []) for h in c.get("members", [])}


_UMLAUTS = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "à": "a", "á": "a", "â": "a", "å": "a",
    "è": "e", "é": "e", "ê": "e", "ë": "e",
    "ì": "i", "í": "i", "î": "i", "ï": "i",
    "ò": "o", "ó": "o", "ô": "o", "õ": "o",
    "ù": "u", "ú": "u", "û": "u",
    "ç": "c", "ñ": "n",
}


def slugify(text: str) -> str:
    """Macht aus beliebigem LLM-Text einen sicheren Dateinamen-Slug.

    Der Slug landet als `wiki/<slug>.md` auf der Platte. Ein "/" darin schriebe
    in ein Verzeichnis, das es nicht gibt, ".." liefe aus dem Wiki heraus — und
    weder Cluster-Vorschlag noch Titel kommen aus vertrauenswuerdiger Quelle,
    auch wenn der Prompt saubere Slugs verlangt.
    """
    lowered = (text or "").strip().lower()
    for source, target in _UMLAUTS.items():
        lowered = lowered.replace(source, target)
    cleaned = "".join(char if char.isalnum() else "-" for char in lowered)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-")[:60].strip("-")
    return cleaned or "thema"


def unique_slug(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


def _refresh(cluster: dict) -> None:
    cluster["members"] = sorted(set(cluster.get("members", [])))
    cluster["fingerprint"] = fingerprint(cluster["members"])


def prune(inventory: dict, known: set[str]) -> list[str]:
    """Entfernt verschwundene Hashes. Liefert die Slugs leer gewordener
    Cluster, deren Artikel geloescht werden muessen."""
    emptied = []
    kept = []
    for cluster in inventory.get("clusters", []):
        cluster["members"] = [h for h in cluster.get("members", []) if h in known]
        if cluster["members"]:
            _refresh(cluster)
            kept.append(cluster)
        else:
            emptied.append(cluster["slug"])
    inventory["clusters"] = kept
    return emptied


def changed_clusters(inventory: dict, previous: dict) -> list[dict]:
    """Cluster, deren Fingerprint sich gegenueber `previous` geaendert hat
    (inklusive neu hinzugekommener)."""
    old = {c["slug"]: c.get("fingerprint") for c in previous.get("clusters", [])}
    return [
        c for c in inventory.get("clusters", [])
        if old.get(c["slug"]) != c.get("fingerprint")
    ]


def _build_assign_prompt(prompt: str, existing: list[dict], batch: list[dict]) -> str:
    parts = [prompt, "", "Bestehende Cluster:"]
    if existing:
        for cluster in existing:
            parts.append(f"- {cluster['slug']} — {cluster['name']}: {cluster.get('description', '')}")
    else:
        parts.append("(noch keine)")
    parts.extend(["", "Neue Dokumente:"])
    for distillate in batch:
        parts.append(f"### DOC {distillate['hash']}")
        parts.append(distillate.get("title", ""))
        parts.append(distillate.get("summary", ""))
        parts.append("")
    return "\n".join(parts)


def assign_new(
    inventory: dict,
    distillates: dict[str, dict],
    model: str,
    prompt: str,
    call,
    batch_size: int = 25,
) -> None:
    """Ordnet noch nicht zugeordnete Destillate Clustern zu.

    Bewusst SERIELL ueber die Batches: liefen zwei Batches parallel, wuerden
    sie unabhaengig voneinander je einen neuen Cluster fuer dasselbe Thema
    vorschlagen. Der Schritt betrifft nur neue Seiten und ist entsprechend
    billig.
    """
    from illico_compile import parse_llm_json  # lokal: vermeidet Import-Zyklus

    known = assigned_hashes(inventory)
    todo = [distillates[h] for h in sorted(distillates) if h not in known]
    if not todo:
        return

    by_slug = {c["slug"]: c for c in inventory["clusters"]}

    for start in range(0, len(todo), batch_size):
        batch = todo[start:start + batch_size]
        try:
            response = call(
                _build_assign_prompt(prompt, inventory["clusters"], batch), model, 4096
            )
            data = parse_llm_json(response) or {}
        except Exception:
            data = {}

        placed: set[str] = set()

        for item in data.get("assignments", []):
            cluster = by_slug.get(item.get("slug"))
            digest = item.get("hash")
            if cluster is not None and digest in distillates:
                cluster["members"].append(digest)
                placed.add(digest)

        for proposal in data.get("new_clusters", []):
            members = [h for h in proposal.get("members", []) if h in distillates]
            if not members:
                continue
            # Slug-Kollision: bestehende Cluster behalten ihren Namen, der neue
            # bekommt einen freien Slug.
            slug = unique_slug(slugify(proposal.get("slug") or ""), set(by_slug))
            cluster = {
                "slug": slug,
                "name": proposal.get("name") or slug,
                "description": proposal.get("description", ""),
                "members": members,
                "fingerprint": "",
            }
            inventory["clusters"].append(cluster)
            by_slug[slug] = cluster
            placed.update(members)

        # Was das LLM nicht untergebracht hat, bekommt einen eigenen Cluster —
        # besser ein grober Artikel als eine stillschweigend verlorene Seite.
        for distillate in batch:
            if distillate["hash"] in placed:
                continue
            slug = unique_slug(slugify(distillate.get("title") or ""), set(by_slug))
            cluster = {
                "slug": slug,
                "name": distillate.get("title") or slug,
                "description": "",
                "members": [distillate["hash"]],
                "fingerprint": "",
            }
            inventory["clusters"].append(cluster)
            by_slug[slug] = cluster

    for cluster in inventory["clusters"]:
        _refresh(cluster)
