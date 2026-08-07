from pathlib import Path

from illico_compile import _ensure_frontmatter, get_prompts, phase_articles
from illico_inventory import fingerprint


def _cluster(slug, members):
    return {"slug": slug, "name": slug.title(), "description": "",
            "members": members, "fingerprint": fingerprint(members)}


def _distillates(*hashes):
    return {h: {"hash": h, "title": f"T-{h[-1]}", "summary": f"S-{h[-1]}",
                "keypoints": [], "entities": [], "edges": [],
                "sources": [f"ordner/{h[-1]}.md"]} for h in hashes}


class Call:
    def __init__(self):
        self.calls = 0

    def __call__(self, prompt, model, max_tokens=2000):
        self.calls += 1
        return "# Artikel\n\nText.\n"


def test_first_run_writes_every_article(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"]), _cluster("b", ["sha256:2"])]}
    call = Call()

    phase_articles(_distillates("sha256:1", "sha256:2"), inv, {"clusters": []},
                   wiki, "m", get_prompts("de"), call, jobs=1)

    assert (wiki / "a.md").exists()
    assert (wiki / "b.md").exists()
    assert call.calls == 2


def test_unchanged_cluster_is_not_touched(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"])]}
    d = _distillates("sha256:1")
    phase_articles(d, inv, {"clusters": []}, wiki, "m", get_prompts("de"), Call(), jobs=1)

    before = (wiki / "a.md").read_text(encoding="utf-8")
    second = Call()
    phase_articles(d, inv, inv, wiki, "m", get_prompts("de"), second, jobs=1)

    assert second.calls == 0
    assert (wiki / "a.md").read_text(encoding="utf-8") == before


def test_changed_cluster_is_rewritten(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    previous = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"])]}
    phase_articles(_distillates("sha256:1"), previous, {"clusters": []},
                   wiki, "m", get_prompts("de"), Call(), jobs=1)

    current = {"schema": 1, "clusters": [_cluster("a", ["sha256:1", "sha256:2"])]}
    second = Call()
    phase_articles(_distillates("sha256:1", "sha256:2"), current, previous,
                   wiki, "m", get_prompts("de"), second, jobs=1)

    assert second.calls == 1


def test_missing_file_is_rebuilt_even_without_fingerprint_change(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"])]}
    d = _distillates("sha256:1")
    phase_articles(d, inv, {"clusters": []}, wiki, "m", get_prompts("de"), Call(), jobs=1)
    (wiki / "a.md").unlink()

    second = Call()
    phase_articles(d, inv, inv, wiki, "m", get_prompts("de"), second, jobs=1)

    assert second.calls == 1
    assert (wiki / "a.md").exists()


def test_other_articles_survive_a_partial_run(tmp_path: Path):
    """Kein Pauschal-Loeschen mehr: ein Abbruch darf kein Torso-Wiki
    hinterlassen."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"]), _cluster("b", ["sha256:2"])]}
    phase_articles(_distillates("sha256:1", "sha256:2"), inv, {"clusters": []},
                   wiki, "m", get_prompts("de"), Call(), jobs=1)

    changed = {"schema": 1, "clusters": [_cluster("a", ["sha256:1", "sha256:9"]),
                                         _cluster("b", ["sha256:2"])]}

    def boom(prompt, model, max_tokens=2000):
        raise RuntimeError("Abbruch")

    try:
        phase_articles(_distillates("sha256:1", "sha256:2"), changed, inv,
                       wiki, "m", get_prompts("de"), boom, jobs=1)
    except RuntimeError:
        pass

    assert (wiki / "b.md").exists()


def test_underscore_files_are_never_deleted(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "_index.md").write_text("Index", encoding="utf-8")
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"])]}

    phase_articles(_distillates("sha256:1"), inv, {"clusters": []},
                   wiki, "m", get_prompts("de"), Call(), jobs=1)

    assert (wiki / "_index.md").read_text(encoding="utf-8") == "Index"


def test_sources_are_full_relative_paths(tmp_path: Path):
    """Regression: das LLM kuerzte Quellpfade auf den blossen Dateinamen
    (`ordner/aktuelles.md` → `aktuelles.md`). Kommt derselbe Name unter mehreren
    Domains vor, laesst sich die Quelle danach nicht mehr eindeutig zuordnen —
    in einem Multi-Domain-Setup verschwanden dadurch Artikel aus der Sicht ihres
    Eigentuemers. Die Quellen sind bekannt, also werden sie gesetzt, nicht
    erraten."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"])]}

    def with_own_frontmatter(prompt, model, max_tokens=2000):
        return '---\ntitle: "Vom LLM"\nsources: ["falsch.md"]\n---\n\nText\n'

    phase_articles(_distillates("sha256:1"), inv, {"clusters": []},
                   wiki, "m", get_prompts("de"), with_own_frontmatter, jobs=1)

    text = (wiki / "a.md").read_text(encoding="utf-8")
    assert "ordner/1.md" in text
    assert "falsch.md" not in text


def test_ensure_frontmatter_overwrites_llm_sources():
    out = _ensure_frontmatter(
        '---\ntitle: "X"\nsources: ["kurz.md"]\n---\n\nText\n',
        "slug", "Titel", ["ordner/lang.md"],
    )
    assert "ordner/lang.md" in out
    assert "kurz.md" not in out


def test_ensure_frontmatter_still_injects_when_missing():
    out = _ensure_frontmatter("Nur Text\n", "slug", "Titel", ["a/b.md"])
    assert out.startswith("---")
    assert "a/b.md" in out


def _distillates_lang(lang: str):
    return {"sha256:1": {"hash": "sha256:1", "title": "T", "summary": "S",
                         "keypoints": [], "entities": [], "edges": [],
                         "language": lang, "sources": ["ordner/1.md"]}}


class CapturingCall:
    def __init__(self):
        self.prompts = []

    def __call__(self, prompt, model, max_tokens=2000):
        self.prompts.append(prompt)
        return "# Artikel\n\nText.\n"


def test_article_prompt_states_the_source_language(tmp_path: Path):
    """Der Artikel-Prompt ist deutsch und der Cluster-Name kommt aus einem
    deutschen Zuordnungsschritt — ohne explizite Ansage schreibt das Modell
    deutsche Artikel ueber englische Quellen."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"])]}
    call = CapturingCall()

    phase_articles(_distillates_lang("en"), inv, {"clusters": []},
                   wiki, "m", get_prompts("de"), call, jobs=1)

    # Exakte Direktive, nicht bloss "en" irgendwo — das steckt in jedem
    # deutschen Wort wie "Regeln" und waere ein falsch gruener Test.
    assert "Sprache der Quellen: en" in call.prompts[0]


def test_article_prompt_without_language_stays_silent(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"])]}
    call = CapturingCall()

    phase_articles(_distillates_lang(""), inv, {"clusters": []},
                   wiki, "m", get_prompts("de"), call, jobs=1)

    assert "Sprache der Quellen:" not in call.prompts[0]


# --- Migration: verwaiste Artikel aus der Zeit vor dem Inventar-Neuschnitt ---

def test_orphan_articles_are_removed(tmp_path: Path):
    """Beim ersten Lauf nach der Umstellung wird das Inventar neu geschnitten
    und vergibt neue Slugs. Die Artikel unter den ALTEN Slugs gehoeren keinem
    Cluster mehr — ohne Aufraeumen stuenden alte und neue nebeneinander."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "alter-slug.md").write_text("Aus der alten Pipeline", encoding="utf-8")
    (wiki / "noch-einer.md").write_text("Auch alt", encoding="utf-8")
    inv = {"schema": 1, "clusters": [_cluster("neuer-slug", ["sha256:1"])]}

    phase_articles(_distillates("sha256:1"), inv, {"clusters": []},
                   wiki, "m", get_prompts("de"), Call(), jobs=1)

    assert (wiki / "neuer-slug.md").exists()
    assert not (wiki / "alter-slug.md").exists()
    assert not (wiki / "noch-einer.md").exists()


def test_orphan_cleanup_spares_underscore_files(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "_index.md").write_text("Index", encoding="utf-8")
    (wiki / "_lint-report.md").write_text("Lint", encoding="utf-8")
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"])]}

    phase_articles(_distillates("sha256:1"), inv, {"clusters": []},
                   wiki, "m", get_prompts("de"), Call(), jobs=1)

    assert (wiki / "_index.md").exists()
    assert (wiki / "_lint-report.md").exists()


def test_empty_inventory_never_wipes_the_wiki(tmp_path: Path):
    """Ein leeres Inventar heisst "etwas ist schiefgegangen", nicht "loesch
    alles". Sonst raeumt ein gescheiterter Zuordnungsschritt das Wiki leer."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "bestand.md").write_text("Wichtig", encoding="utf-8")

    phase_articles({}, {"schema": 1, "clusters": []}, {"clusters": []},
                   wiki, "m", get_prompts("de"), Call(), jobs=1)

    assert (wiki / "bestand.md").exists()


def test_removed_orphans_count_as_a_change(tmp_path: Path):
    """Verschwundene Artikel muessen Index und Lint ausloesen — sonst zeigt
    der Index weiter auf Dateien, die es nicht mehr gibt."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "a.md").write_text("x", encoding="utf-8")
    (wiki / "verwaist.md").write_text("alt", encoding="utf-8")
    inv = {"schema": 1, "clusters": [_cluster("a", ["sha256:1"])]}
    d = _distillates("sha256:1")
    phase_articles(d, inv, {"clusters": []}, wiki, "m", get_prompts("de"), Call(), jobs=1)

    # zweiter Lauf: nichts geaendert, aber ein Waisenkind taucht auf
    (wiki / "wieder-verwaist.md").write_text("alt", encoding="utf-8")
    _created, changed = phase_articles(d, inv, inv, wiki, "m", get_prompts("de"),
                                       Call(), jobs=1)

    assert changed  # nicht leer → Index und Lint laufen
    assert not (wiki / "wieder-verwaist.md").exists()
