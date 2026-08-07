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
    """Regression zu MR !19 im Cloud-Repo: das LLM kuerzte Pfade auf Basenames,
    wodurch Artikel faelschlich als Cross-Tenant-Leak verworfen wurden."""
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
