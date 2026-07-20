# tests/test_compile_canonicalize.py
import json as _json
from pathlib import Path

from illico_compile import canonicalize_graph, get_prompts, phase_graph
from typer.testing import CliRunner
from illico_compile import app as compile_app

runner = CliRunner()


def test_canonicalize_graph_merges_via_mocked_llm(monkeypatch):
    graph = {
        "nodes": [
            {"id": 1, "label": "Location", "name": "Frankfurt am Main", "props": {"source": "local"}},
            {"id": 2, "label": "Location", "name": "Frankfurt", "props": {"source": "domain"}},
            {"id": 3, "label": "Organization", "name": "dkd", "props": {"source": "local"}},
        ],
        "edges": [
            {"id": 1, "src": 3, "dst": 1, "rel": "LOCATED_IN", "props": {"source": "local"}},
            {"id": 2, "src": 3, "dst": 2, "rel": "LOCATED_IN", "props": {"source": "domain"}},
        ],
    }

    def fake_llm(prompt, model, max_tokens=2000, retries=3):
        # Nur der Location-Block enthält beide Frankfurt-Nodes
        if "Frankfurt" in prompt:
            return _json.dumps({"clusters": [{
                "prefLabel": "Frankfurt am Main", "label": "Location",
                "aliases": ["Frankfurt"], "member_ids": [1, 2]}]})
        return _json.dumps({"clusters": []})

    monkeypatch.setattr("illico_compile.call_llm", fake_llm)
    out = canonicalize_graph(graph, "test-model", get_prompts("de"))

    loc = [n for n in out["nodes"] if n["label"] == "Location"]
    assert len(loc) == 1
    assert loc[0]["name"] == "Frankfurt am Main"
    assert loc[0]["aliases"] == ["Frankfurt"]
    assert len(out["edges"]) == 1  # zwei LOCATED_IN dedupliziert
    assert out["edges"][0]["props"]["source"] == "local"


def test_canonicalize_graph_survives_bad_llm_json(monkeypatch):
    graph = {"nodes": [{"id": 1, "label": "Location", "name": "Berlin", "props": {"source": "local"}}], "edges": []}
    monkeypatch.setattr("illico_compile.call_llm", lambda *a, **k: "not json")
    out = canonicalize_graph(graph, "test-model", get_prompts("de"))
    assert len(out["nodes"]) == 1
    assert out["nodes"][0]["name"] == "Berlin"
    assert out["nodes"][0]["aliases"] == []


def test_phase_graph_canonicalizes_across_batches(monkeypatch, tmp_path: Path):
    # Zwei Batches liefern denselben Ort in zwei Schreibweisen
    batches = iter([
        [{"nodes": [{"id": 1, "label": "Location", "name": "Frankfurt am Main", "props": {"source": "local"}}], "edges": []}],
        [{"nodes": [{"id": 1, "label": "Location", "name": "Frankfurt", "props": {"source": "local"}}], "edges": []}],
    ])
    monkeypatch.setattr("illico_compile._extract_graph_batch", lambda *a, **k: next(batches))

    def fake_llm(prompt, model, max_tokens=2000, retries=3):
        # IDs aus dem Payload extrahieren (Canonicalize sendet [{id, label, name}, ...])
        try:
            payload_start = prompt.rfind("[{")
            nodes_payload = _json.loads(prompt[payload_start:])
            ids = [n["id"] for n in nodes_payload]
        except Exception:
            ids = [1, 2]
        return _json.dumps({"clusters": [{
            "prefLabel": "Frankfurt am Main", "label": "Location",
            "aliases": ["Frankfurt"], "member_ids": ids}]})
    monkeypatch.setattr("illico_compile.call_llm", fake_llm)

    # 16+ Dummy-Dateien erzwingen >1 Batch (graph_batch_size=15)
    raw = {f"f{i}.md": "x" for i in range(16)}
    graph_dir = tmp_path / "graph-AB3X9K"
    phase_graph(raw, graph_dir, "test-model", get_prompts("de"))

    nodes = _json.loads((graph_dir / "nodes.json").read_text(encoding="utf-8"))
    locs = [n for n in nodes if n["label"] == "Location"]
    assert len(locs) == 1
    assert locs[0]["name"] == "Frankfurt am Main"
    assert locs[0]["aliases"] == ["Frankfurt"]


def test_phase_graph_canonicalizes_single_batch(monkeypatch, tmp_path: Path):
    # Ein einziger Batch mit zwei Schreibweisen desselben Orts
    monkeypatch.setattr("illico_compile._extract_graph_batch", lambda *a, **k: [
        {"nodes": [
            {"id": 1, "label": "Location", "name": "Frankfurt am Main", "props": {"source": "local"}},
            {"id": 2, "label": "Location", "name": "Frankfurt", "props": {"source": "local"}},
        ], "edges": []}
    ])

    def fake_llm(prompt, model, max_tokens=2000, retries=3):
        return _json.dumps({"clusters": [{
            "prefLabel": "Frankfurt am Main", "label": "Location",
            "aliases": ["Frankfurt"], "member_ids": [1, 2]}]})
    monkeypatch.setattr("illico_compile.call_llm", fake_llm)

    raw = {"f0.md": "x"}  # eine Datei → ein Batch
    graph_dir = tmp_path / "graph-AB3X9K"
    phase_graph(raw, graph_dir, "test-model", get_prompts("de"))

    nodes = _json.loads((graph_dir / "nodes.json").read_text(encoding="utf-8"))
    locs = [n for n in nodes if n["label"] == "Location"]
    assert len(locs) == 1
    assert locs[0]["name"] == "Frankfurt am Main"
    assert locs[0]["aliases"] == ["Frankfurt"]


def test_canonicalize_only_rewrites_existing_graph(monkeypatch, data_dir):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    # Bestehender Graph mit zwei Frankfurt-Nodes
    code = "AB3X9K"
    (data_dir / "raw" / "x.md").write_text("---\ndomain: www.x.de\n---\nx", encoding="utf-8")
    gdir = data_dir / f"graph-{code}"
    gdir.mkdir()
    (gdir / "nodes.json").write_text(_json.dumps([
        {"id": 1, "label": "Location", "name": "Frankfurt am Main", "props": {"source": "local"}},
        {"id": 2, "label": "Location", "name": "Frankfurt", "props": {"source": "local"}},
    ]), encoding="utf-8")
    (gdir / "edges.json").write_text("[]", encoding="utf-8")

    monkeypatch.setattr("illico_compile.call_llm", lambda *a, **k: _json.dumps({"clusters": [{
        "prefLabel": "Frankfurt am Main", "label": "Location",
        "aliases": ["Frankfurt"], "member_ids": [1, 2]}]}))

    r = runner.invoke(compile_app, ["--data", str(data_dir), "--wiki-dir", f"wiki-{code}", "--canonicalize-only"])
    assert r.exit_code == 0, r.stdout
    nodes = _json.loads((gdir / "nodes.json").read_text(encoding="utf-8"))
    assert len([n for n in nodes if n["label"] == "Location"]) == 1
    assert nodes[0]["aliases"] == ["Frankfurt"]


def test_canonicalize_only_errors_without_graph(monkeypatch, data_dir):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    code = "AB3X9K"
    (data_dir / "raw" / "x.md").write_text("---\ndomain: www.x.de\n---\nx", encoding="utf-8")
    r = runner.invoke(compile_app, ["--data", str(data_dir), "--wiki-dir", f"wiki-{code}", "--canonicalize-only"])
    assert r.exit_code != 0
    assert "No such option" not in r.output
    assert "Kein Graph" in r.output
