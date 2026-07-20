"""Cross-Tenant-Leak-Test: Tenant-Graph darf nie auf globales graph/ zurückfallen."""

import json
from pathlib import Path

import pytest

from illico_graph import _graph_dir_candidates, load_graph_data, load_graph_meta


TENANT = "AB3X9K"
GLOBAL_NODES = [{"id": 1, "name": "GlobalEntity"}]
GLOBAL_EDGES = [{"src": 1, "dst": 1}]
TENANT_NODES = [{"id": 2, "name": "TenantEntity"}]
TENANT_EDGES = [{"src": 2, "dst": 2}]


def _write_graph(directory: Path, nodes: list, edges: list, meta: dict | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "nodes.json").write_text(json.dumps(nodes), encoding="utf-8")
    (directory / "edges.json").write_text(json.dumps(edges), encoding="utf-8")
    if meta is not None:
        (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


# --- _graph_dir_candidates ---

def test_candidates_tenant_excludes_global(tmp_path):
    paths = _graph_dir_candidates(tmp_path, lang=None, namespace=TENANT)
    names = [p.name for p in paths]
    assert f"graph-{TENANT}" in names
    assert "graph" not in names


def test_candidates_tenant_with_lang_excludes_global(tmp_path):
    paths = _graph_dir_candidates(tmp_path, lang="de", namespace=TENANT)
    names = [p.name for p in paths]
    assert f"graph-{TENANT}-de" in names
    assert f"graph-{TENANT}" in names
    assert "graph" not in names
    assert "graph-de" not in names


def test_candidates_admin_includes_global(tmp_path):
    paths = _graph_dir_candidates(tmp_path, lang=None, namespace=None)
    names = [p.name for p in paths]
    assert "graph" in names


# --- load_graph_data ---

def test_tenant_without_own_graph_returns_empty(tmp_path):
    """Tenant ohne graph-<CODE>/ → leere Listen, kein Fallback auf graph/."""
    _write_graph(tmp_path / "graph", GLOBAL_NODES, GLOBAL_EDGES)
    nodes, edges = load_graph_data(tmp_path, lang=None, namespace=TENANT)
    assert nodes == []
    assert edges == []


def test_tenant_with_own_graph_returns_tenant_data(tmp_path):
    _write_graph(tmp_path / "graph", GLOBAL_NODES, GLOBAL_EDGES)
    _write_graph(tmp_path / f"graph-{TENANT}", TENANT_NODES, TENANT_EDGES)
    nodes, edges = load_graph_data(tmp_path, lang=None, namespace=TENANT)
    assert nodes == TENANT_NODES
    assert edges == TENANT_EDGES


def test_tenant_with_lang_graph_preferred_over_base(tmp_path):
    base_nodes = [{"id": 3, "name": "BaseEntity"}]
    lang_nodes = [{"id": 4, "name": "LangEntity"}]
    _write_graph(tmp_path / f"graph-{TENANT}", base_nodes, [])
    _write_graph(tmp_path / f"graph-{TENANT}-de", lang_nodes, [])
    nodes, edges = load_graph_data(tmp_path, lang="de", namespace=TENANT)
    assert nodes == lang_nodes


def test_admin_fallback_to_global(tmp_path):
    _write_graph(tmp_path / "graph", GLOBAL_NODES, GLOBAL_EDGES)
    nodes, edges = load_graph_data(tmp_path, lang=None, namespace=None)
    assert nodes == GLOBAL_NODES


# --- load_graph_meta ---

def test_tenant_without_own_meta_returns_empty(tmp_path):
    _write_graph(tmp_path / "graph", GLOBAL_NODES, GLOBAL_EDGES, meta={"source": "global"})
    meta = load_graph_meta(tmp_path, lang=None, namespace=TENANT)
    assert meta == {}


def test_tenant_with_own_meta_returns_tenant_meta(tmp_path):
    _write_graph(tmp_path / "graph", GLOBAL_NODES, GLOBAL_EDGES, meta={"source": "global"})
    _write_graph(tmp_path / f"graph-{TENANT}", TENANT_NODES, TENANT_EDGES, meta={"source": "tenant"})
    meta = load_graph_meta(tmp_path, lang=None, namespace=TENANT)
    assert meta == {"source": "tenant"}
