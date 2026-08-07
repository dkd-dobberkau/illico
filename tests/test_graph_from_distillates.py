import json
from pathlib import Path

import illico_compile
from illico_compile import get_prompts, phase_graph


def _d(h, entities, edges=()):
    return {"hash": h, "title": f"T{h[-1]}", "summary": "s", "keypoints": [],
            "entities": entities, "edges": list(edges), "sources": [f"{h[-1]}.md"]}


def test_graph_merges_entities_from_all_distillates(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    distillates = {
        "sha256:1": _d("sha256:1", [{"name": "Alpha AG", "label": "Organisation", "props": {}}]),
        "sha256:2": _d("sha256:2", [{"name": "Beta GmbH", "label": "Organisation", "props": {}}]),
    }

    graph = phase_graph(distillates, tmp_path / "graph", "m", get_prompts("de"))

    names = sorted(n["name"] for n in graph["nodes"])
    assert names == ["Alpha AG", "Beta GmbH"]


def test_duplicate_entities_are_merged(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    ent = [{"name": "Alpha AG", "label": "Organisation", "props": {}}]
    distillates = {"sha256:1": _d("sha256:1", ent), "sha256:2": _d("sha256:2", ent)}

    graph = phase_graph(distillates, tmp_path / "graph", "m", get_prompts("de"))

    assert len(graph["nodes"]) == 1


def test_nodes_get_integer_ids(tmp_path: Path, monkeypatch):
    """Der gesamte Konsument-Pfad (illico_graph.restrict_to_articles,
    build_graph_context, canonicalize_graph) ist ID-basiert."""
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    distillates = {
        "sha256:1": _d("sha256:1", [{"name": "A", "label": "Organisation", "props": {}}]),
        "sha256:2": _d("sha256:2", [{"name": "B", "label": "Ort", "props": {}}]),
    }

    graph = phase_graph(distillates, tmp_path / "graph", "m", get_prompts("de"))

    ids = sorted(n["id"] for n in graph["nodes"])
    assert ids == [0, 1]


def test_edges_reference_node_ids_not_names(tmp_path: Path, monkeypatch):
    """Destillate nennen Entitaeten beim Namen — ein LLM kann keine globalen
    IDs kennen. Der Merge muss Name → ID aufloesen."""
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    distillates = {"sha256:1": _d(
        "sha256:1",
        [{"name": "A", "label": "Organisation", "props": {}},
         {"name": "B", "label": "Ort", "props": {}}],
        [{"src": "A", "rel": "liegt_in", "dst": "B"}],
    )}

    graph = phase_graph(distillates, tmp_path / "graph", "m", get_prompts("de"))

    by_name = {n["name"]: n["id"] for n in graph["nodes"]}
    assert graph["edges"] == [
        {"id": 0, "src": by_name["A"], "rel": "liegt_in", "dst": by_name["B"]}
    ]


def test_edge_with_unknown_entity_is_dropped(tmp_path: Path, monkeypatch):
    """Nennt eine Edge eine Entitaet, die nirgends deklariert ist, laesst sie
    sich nicht aufloesen — lieber verwerfen als eine kaputte Referenz."""
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    distillates = {"sha256:1": _d(
        "sha256:1",
        [{"name": "A", "label": "Organisation", "props": {}}],
        [{"src": "A", "rel": "liegt_in", "dst": "Gibt-es-nicht"}],
    )}

    graph = phase_graph(distillates, tmp_path / "graph", "m", get_prompts("de"))

    assert graph["edges"] == []


def test_edges_across_distillates_resolve(tmp_path: Path, monkeypatch):
    """Eine Seite nennt A, eine andere B — die Kante zwischen beiden muss
    trotzdem aufloesen, weil der Merge global ist."""
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    distillates = {
        "sha256:1": _d("sha256:1", [{"name": "A", "label": "Organisation", "props": {}}],
                       [{"src": "A", "rel": "liegt_in", "dst": "B"}]),
        "sha256:2": _d("sha256:2", [{"name": "B", "label": "Ort", "props": {}}]),
    }

    graph = phase_graph(distillates, tmp_path / "graph", "m", get_prompts("de"))

    assert len(graph["edges"]) == 1


def test_empty_entities_do_not_crash(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    graph = phase_graph({"sha256:1": _d("sha256:1", [])}, tmp_path / "graph", "m", get_prompts("de"))

    assert graph["nodes"] == []
    assert graph["edges"] == []


def test_graph_files_are_written(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)
    graph_dir = tmp_path / "graph"
    phase_graph(
        {"sha256:1": _d("sha256:1", [{"name": "A", "label": "Ding", "props": {}}])},
        graph_dir, "m", get_prompts("de"),
    )

    assert json.loads((graph_dir / "nodes.json").read_text(encoding="utf-8"))
    assert (graph_dir / "edges.json").exists()
    meta = json.loads((graph_dir / "meta.json").read_text(encoding="utf-8"))
    assert "1" in meta["description"]


def test_no_llm_call_for_extraction(tmp_path: Path, monkeypatch):
    """Der Graph faellt aus den Destillaten ab — der zweite Vollscan entfaellt."""
    monkeypatch.setattr(illico_compile, "canonicalize_graph", lambda g, m, p: g)

    def explode(*args, **kwargs):
        raise AssertionError("phase_graph darf nicht mehr extrahieren")

    monkeypatch.setattr(illico_compile, "call_llm", explode)
    phase_graph({"sha256:1": _d("sha256:1", [])}, tmp_path / "graph", "m", get_prompts("de"))


class CountingCanonicalize:
    """Zaehlt Aufrufe — canonicalize_graph ist der teure Teil der Graph-Phase
    (ein LLM-Call je Label-Block)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, graph, model, prompts):
        self.calls += 1
        return graph


def test_graph_is_skipped_when_distillates_unchanged(tmp_path: Path, monkeypatch):
    """Ohne diesen Skip kostet JEDER Lauf die Kanonisierung neu — bei einem
    grossen Graphen sind das dutzende LLM-Calls fuer ein identisches Ergebnis."""
    canon = CountingCanonicalize()
    monkeypatch.setattr(illico_compile, "canonicalize_graph", canon)
    distillates = {"sha256:1": _d("sha256:1", [{"name": "A", "label": "Ding", "props": {}}])}
    graph_dir = tmp_path / "graph"

    phase_graph(distillates, graph_dir, "m", get_prompts("de"))
    assert canon.calls == 1

    phase_graph(distillates, graph_dir, "m", get_prompts("de"))
    assert canon.calls == 1


def test_skipped_graph_still_returns_the_stored_graph(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(illico_compile, "canonicalize_graph", CountingCanonicalize())
    distillates = {"sha256:1": _d("sha256:1", [{"name": "A", "label": "Ding", "props": {}}])}
    graph_dir = tmp_path / "graph"

    first = phase_graph(distillates, graph_dir, "m", get_prompts("de"))
    second = phase_graph(distillates, graph_dir, "m", get_prompts("de"))

    assert second["nodes"] == first["nodes"]
    assert second["edges"] == first["edges"]


def test_graph_is_rebuilt_when_distillates_change(tmp_path: Path, monkeypatch):
    canon = CountingCanonicalize()
    monkeypatch.setattr(illico_compile, "canonicalize_graph", canon)
    graph_dir = tmp_path / "graph"

    phase_graph({"sha256:1": _d("sha256:1", [{"name": "A", "label": "Ding", "props": {}}])},
                graph_dir, "m", get_prompts("de"))
    phase_graph({"sha256:1": _d("sha256:1", [{"name": "A", "label": "Ding", "props": {}}]),
                 "sha256:2": _d("sha256:2", [{"name": "B", "label": "Ding", "props": {}}])},
                graph_dir, "m", get_prompts("de"))

    assert canon.calls == 2


def test_graph_is_rebuilt_when_nodes_file_missing(tmp_path: Path, monkeypatch):
    canon = CountingCanonicalize()
    monkeypatch.setattr(illico_compile, "canonicalize_graph", canon)
    distillates = {"sha256:1": _d("sha256:1", [{"name": "A", "label": "Ding", "props": {}}])}
    graph_dir = tmp_path / "graph"

    phase_graph(distillates, graph_dir, "m", get_prompts("de"))
    (graph_dir / "nodes.json").unlink()
    phase_graph(distillates, graph_dir, "m", get_prompts("de"))

    assert canon.calls == 2


def test_canonicalize_is_still_applied(tmp_path: Path, monkeypatch):
    seen = {}

    def fake(graph, model, prompts):
        seen["called"] = True
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(illico_compile, "canonicalize_graph", fake)
    phase_graph({"sha256:1": _d("sha256:1", [])}, tmp_path / "graph", "m", get_prompts("de"))

    assert seen.get("called")
