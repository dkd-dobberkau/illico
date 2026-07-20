"""Tests für build_graph_context — Graph-Relationen im Answer-Kontext."""

from illico_graph import REL_DE, build_graph_context


def _fixture():
    nodes = [
        {"id": 1, "name": "dkd", "label": "Organization",
         "props": {"description": "Digitalagentur aus Frankfurt"}},
        {"id": 2, "name": "b13", "label": "Organization",
         "props": {"description": "TYPO3-Agentur"}},
        {"id": 3, "name": "TYPO3", "label": "Technology", "props": {}},
        {"id": 4, "name": "Geheim AG", "label": "Organization",
         "props": {"description": "Nicht im Artikel"}},
    ]
    edges = [
        {"id": 1, "src": 1, "dst": 2, "rel": "PARTNER_OF", "props": {}},
        {"id": 2, "src": 2, "dst": 3, "rel": "USES", "props": {}},
        {"id": 3, "src": 4, "dst": 1, "rel": "PARTNER_OF", "props": {}},
    ]
    articles = {"firma.md": "dkd und b13 arbeiten mit TYPO3.", "_index.md": "x"}
    return nodes, edges, articles


def test_renders_relations_as_german_sentences():
    nodes, edges, articles = _fixture()
    out = build_graph_context(["firma.md"], articles, nodes, edges)
    assert "dkd ist Partner von b13" in out
    assert "b13 nutzt TYPO3" in out


def test_includes_entity_descriptions():
    nodes, edges, articles = _fixture()
    out = build_graph_context(["firma.md"], articles, nodes, edges)
    assert "**dkd** (Organization): Digitalagentur aus Frankfurt" in out


def test_isolation_entity_not_in_nodes_never_appears():
    nodes, edges, _ = _fixture()
    # Artikel erwähnt eine Entität, die NICHT als Node existiert → darf nie erscheinen
    articles = {"firma.md": "dkd, b13 und die Konkurrent GmbH nutzen TYPO3."}
    out = build_graph_context(["firma.md"], articles, nodes, edges)
    assert "Konkurrent GmbH" not in out


def test_one_hop_neighbor_relation_included():
    nodes, edges, articles = _fixture()
    out = build_graph_context(["firma.md"], articles, nodes, edges)
    # Geheim AG ist nur 1-Hop-Nachbar von dkd (nicht im Artikel-Text),
    # die Beziehung muss dennoch erscheinen
    assert "Geheim AG ist Partner von dkd" in out


def test_empty_when_no_entities():
    nodes, edges, _ = _fixture()
    out = build_graph_context(["firma.md"], {"firma.md": "Nichts Bekanntes."}, nodes, edges)
    assert out == ""


def test_unknown_rel_falls_back_to_raw_token():
    nodes = [{"id": 1, "name": "A", "label": "X", "props": {}},
             {"id": 2, "name": "B", "label": "X", "props": {}}]
    edges = [{"id": 1, "src": 1, "dst": 2, "rel": "FOOBAR", "props": {}}]
    articles = {"a.md": "A und B."}
    out = build_graph_context(["a.md"], articles, nodes, edges)
    assert "A FOOBAR B" in out
    assert "FOOBAR" not in REL_DE


def test_max_facts_cap_respected():
    nodes = [{"id": i, "name": f"N{i}", "label": "X", "props": {}} for i in range(1, 12)]
    # 10 Kanten von N1 zu N2..N11
    edges = [{"id": i, "src": 1, "dst": i + 1, "rel": "USES", "props": {}} for i in range(1, 11)]
    articles = {"a.md": " ".join(f"N{i}" for i in range(1, 12))}
    out = build_graph_context(["a.md"], articles, nodes, edges, max_facts=3)
    # nur 3 Beziehungs-Zeilen ("- ... nutzt ...")
    rel_lines = [l for l in out.splitlines() if " nutzt " in l]
    assert len(rel_lines) == 3
