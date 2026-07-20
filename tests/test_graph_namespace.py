import json
from pathlib import Path
from illico_graph import load_graph_data


def _write_graph(dir_: Path, node_id: str):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "nodes.json").write_text(json.dumps([{"id": node_id}]), encoding="utf-8")
    (dir_ / "edges.json").write_text("[]", encoding="utf-8")


def test_namespace_selects_prefixed_dir(tmp_path: Path):
    _write_graph(tmp_path / "graph-ABCDEF", "ns")
    _write_graph(tmp_path / "graph", "global")
    nodes, _ = load_graph_data(tmp_path, namespace="ABCDEF")
    assert nodes == [{"id": "ns"}]


def test_no_namespace_uses_global(tmp_path: Path):
    _write_graph(tmp_path / "graph", "global")
    nodes, _ = load_graph_data(tmp_path, namespace=None)
    assert nodes == [{"id": "global"}]
