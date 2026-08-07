import json
from pathlib import Path

from illico_inventory import (
    assign_new,
    assigned_hashes,
    changed_clusters,
    fingerprint,
    load_inventory,
    prune,
    save_inventory,
    unique_slug,
)


def _d(h: str, title: str) -> dict:
    return {"hash": h, "title": title, "summary": f"Zusammenfassung {title}"}


def test_load_missing_file_yields_empty_inventory(tmp_path: Path):
    inv = load_inventory(tmp_path / "fehlt.json")
    assert inv == {"schema": 1, "clusters": []}


def test_legacy_inventory_is_treated_as_empty(tmp_path: Path):
    """Alt-Inventare haben weder members noch fingerprint — der erste Lauf
    baut die Struktur einmalig neu auf."""
    path = tmp_path / "_inventory.json"
    path.write_text(json.dumps({
        "clusters": [{"name": "Alt", "slug": "alt", "files": ["a.md"]}],
        "main_topics": ["X"],
    }), encoding="utf-8")
    assert load_inventory(path) == {"schema": 1, "clusters": []}


def test_save_load_roundtrip(tmp_path: Path):
    inv = {"schema": 1, "clusters": [
        {"slug": "a", "name": "A", "description": "d", "members": ["sha256:1"],
         "fingerprint": fingerprint(["sha256:1"])}
    ]}
    path = tmp_path / "_inventory.json"
    save_inventory(path, inv)
    assert load_inventory(path) == inv


def test_fingerprint_is_order_independent():
    assert fingerprint(["sha256:b", "sha256:a"]) == fingerprint(["sha256:a", "sha256:b"])


def test_fingerprint_changes_with_membership():
    assert fingerprint(["sha256:a"]) != fingerprint(["sha256:a", "sha256:b"])


def test_unique_slug_avoids_collision():
    assert unique_slug("thema", {"thema"}) == "thema-2"
    assert unique_slug("thema", {"thema", "thema-2"}) == "thema-3"
    assert unique_slug("thema", set()) == "thema"


def test_prune_removes_vanished_hashes_and_reports_empty_clusters():
    inv = {"schema": 1, "clusters": [
        {"slug": "a", "name": "A", "description": "", "members": ["sha256:1", "sha256:2"],
         "fingerprint": fingerprint(["sha256:1", "sha256:2"])},
        {"slug": "b", "name": "B", "description": "", "members": ["sha256:3"],
         "fingerprint": fingerprint(["sha256:3"])},
    ]}
    emptied = prune(inv, {"sha256:1"})

    assert emptied == ["b"]
    assert [c["slug"] for c in inv["clusters"]] == ["a"]
    assert inv["clusters"][0]["members"] == ["sha256:1"]
    assert inv["clusters"][0]["fingerprint"] == fingerprint(["sha256:1"])


class AssignCall:
    """Fake-LLM fuer die Zuordnung: steckt alles in einen neuen Cluster."""

    def __init__(self, slug="neues-thema", name="Neues Thema"):
        self.calls = 0
        self.slug, self.name = slug, name
        self.seen_existing = []

    def __call__(self, prompt, model, max_tokens=2000):
        self.calls += 1
        self.seen_existing.append(prompt)
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### DOC ")]
        return json.dumps({
            "assignments": [],
            "new_clusters": [{
                "slug": self.slug, "name": self.name,
                "description": "d", "members": ids,
            }],
        })


def test_assign_creates_cluster_for_new_pages():
    inv = {"schema": 1, "clusters": []}
    assign_new(inv, {"sha256:1": _d("sha256:1", "Seite")}, "m", "P", AssignCall())

    assert len(inv["clusters"]) == 1
    assert inv["clusters"][0]["members"] == ["sha256:1"]
    assert inv["clusters"][0]["fingerprint"] == fingerprint(["sha256:1"])


def test_assign_skips_already_assigned_pages():
    inv = {"schema": 1, "clusters": [
        {"slug": "a", "name": "A", "description": "", "members": ["sha256:1"],
         "fingerprint": fingerprint(["sha256:1"])}
    ]}
    call = AssignCall()
    assign_new(inv, {"sha256:1": _d("sha256:1", "Seite")}, "m", "P", call)

    assert call.calls == 0


def test_assign_into_existing_cluster_keeps_slug():
    inv = {"schema": 1, "clusters": [
        {"slug": "bestand", "name": "Bestand", "description": "", "members": ["sha256:1"],
         "fingerprint": fingerprint(["sha256:1"])}
    ]}

    def call(prompt, model, max_tokens=2000):
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### DOC ")]
        return json.dumps({
            "assignments": [{"hash": i, "slug": "bestand"} for i in ids],
            "new_clusters": [],
        })

    assign_new(inv, {"sha256:2": _d("sha256:2", "Neu")}, "m", "P", call)

    assert [c["slug"] for c in inv["clusters"]] == ["bestand"]
    assert sorted(inv["clusters"][0]["members"]) == ["sha256:1", "sha256:2"]


def test_assign_never_renames_existing_slugs():
    """Slug-Stabilitaet traegt die ganze Inkrementalitaet — und haelt
    [[links]], Chat-Historien und Bookmarks am Leben."""
    inv = {"schema": 1, "clusters": [
        {"slug": "bestand", "name": "Bestand", "description": "", "members": ["sha256:1"],
         "fingerprint": fingerprint(["sha256:1"])}
    ]}
    assign_new(inv, {"sha256:2": _d("sha256:2", "Neu")}, "m", "P",
               AssignCall(slug="bestand", name="Anderer Name"))

    bestand = next(c for c in inv["clusters"] if c["slug"] == "bestand")
    assert bestand["name"] == "Bestand"


def test_assign_deduplicates_slug_collisions():
    inv = {"schema": 1, "clusters": [
        {"slug": "thema", "name": "Thema", "description": "", "members": ["sha256:1"],
         "fingerprint": fingerprint(["sha256:1"])}
    ]}

    def call(prompt, model, max_tokens=2000):
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### DOC ")]
        return json.dumps({
            "assignments": [],
            "new_clusters": [{"slug": "thema", "name": "Anderes Thema",
                              "description": "", "members": ids}],
        })

    assign_new(inv, {"sha256:2": _d("sha256:2", "Neu")}, "m", "P", call)

    slugs = [c["slug"] for c in inv["clusters"]]
    assert slugs == ["thema", "thema-2"]


def test_assign_runs_batches_sequentially():
    """Seriell, damit nicht zwei Batches unabhaengig denselben Cluster erfinden."""
    inv = {"schema": 1, "clusters": []}
    distillates = {f"sha256:{i}": _d(f"sha256:{i}", f"S{i}") for i in range(5)}
    call = AssignCall()

    assign_new(inv, distillates, "m", "P", call, batch_size=2)

    assert call.calls == 3
    # Spaetere Batches sehen den vom ersten Batch erzeugten Cluster.
    assert "neues-thema" in call.seen_existing[-1]


def test_assigned_hashes_collects_all_members():
    inv = {"schema": 1, "clusters": [
        {"slug": "a", "name": "A", "description": "", "members": ["sha256:1", "sha256:2"],
         "fingerprint": ""},
        {"slug": "b", "name": "B", "description": "", "members": ["sha256:3"], "fingerprint": ""},
    ]}
    assert assigned_hashes(inv) == {"sha256:1", "sha256:2", "sha256:3"}


def test_changed_clusters_detects_only_real_changes():
    before = {"schema": 1, "clusters": [
        {"slug": "a", "name": "A", "description": "", "members": ["sha256:1"],
         "fingerprint": fingerprint(["sha256:1"])},
        {"slug": "b", "name": "B", "description": "", "members": ["sha256:2"],
         "fingerprint": fingerprint(["sha256:2"])},
    ]}
    after = json.loads(json.dumps(before))
    after["clusters"][1]["members"].append("sha256:3")
    after["clusters"][1]["fingerprint"] = fingerprint(["sha256:2", "sha256:3"])

    changed = changed_clusters(after, before)
    assert [c["slug"] for c in changed] == ["b"]


# --- Slugs muessen Dateinamen sein duerfen ---

def test_slugify_strips_path_separators():
    """Der Slug wird zu `wiki/<slug>.md`. Ein "/" darin schreibt in ein
    Unterverzeichnis, das es nicht gibt — oder schlimmer."""
    from illico_inventory import slugify
    assert "/" not in slugify("TYPO3 / Neos")
    assert "\\" not in slugify("a\\b")
    assert slugify("../../etc/passwd").strip("-") not in ("", ".", "..")
    assert ".." not in slugify("../../etc/passwd")


def test_slugify_transliterates_umlauts():
    from illico_inventory import slugify
    assert slugify("Über uns") == "ueber-uns"
    assert slugify("Grüße & Größe") == "gruesse-groesse"


def test_slugify_is_lowercase_and_hyphenated():
    from illico_inventory import slugify
    assert slugify("TYPO3 Camp Vienna 2026") == "typo3-camp-vienna-2026"


def test_slugify_never_returns_empty():
    from illico_inventory import slugify
    assert slugify("///") == "thema"
    assert slugify("") == "thema"


def test_llm_proposed_slug_is_sanitised():
    """Der Prompt verlangt saubere Slugs — verlassen darf man sich nicht darauf."""
    inv = {"schema": 1, "clusters": []}

    def call(prompt, model, max_tokens=2000):
        ids = [line.split()[2] for line in prompt.splitlines() if line.startswith("### DOC ")]
        return json.dumps({"assignments": [], "new_clusters": [
            {"slug": "Böse/Slug", "name": "N", "description": "", "members": ids}]})

    assign_new(inv, {"sha256:1": _d("sha256:1", "S")}, "m", "P", call)

    assert inv["clusters"][0]["slug"] == "boese-slug"


def test_fallback_slug_from_title_is_sanitised():
    inv = {"schema": 1, "clusters": []}

    def call(prompt, model, max_tokens=2000):
        return json.dumps({"assignments": [], "new_clusters": []})

    assign_new(inv, {"sha256:1": _d("sha256:1", "TYPO3 / Neos: Überblick!")}, "m", "P", call)

    slug = inv["clusters"][0]["slug"]
    assert "/" not in slug and ":" not in slug and "!" not in slug
    assert slug == slug.lower()
