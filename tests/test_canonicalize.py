from illico_canonicalize import (
    apply_clusters,
    block_nodes_by_label,
    split_block,
    merge_clusters_by_preflabel,
    fullname_clusters,
    unify_overlapping_clusters,
    _norm,
)


def _node(i, name, label="Location", source="local"):
    return {"id": i, "label": label, "name": name, "props": {"source": source}}


def test_apply_clusters_merges_members_into_one_node():
    nodes = [_node(12, "Frankfurt am Main"), _node(47, "Frankfurt"), _node(103, "FFM")]
    clusters = [{
        "prefLabel": "Frankfurt am Main", "label": "Location",
        "aliases": ["Frankfurt", "FFM"], "member_ids": [12, 47, 103],
    }]
    new_nodes, _ = apply_clusters(nodes, [], clusters)
    assert len(new_nodes) == 1
    n = new_nodes[0]
    assert n["name"] == "Frankfurt am Main"
    assert sorted(n["aliases"]) == ["FFM", "Frankfurt"]
    assert n["label"] == "Location"


def test_apply_clusters_preserves_unclustered_singleton():
    nodes = [_node(1, "Frankfurt"), _node(2, "Berlin")]
    clusters = [{"prefLabel": "Frankfurt", "label": "Location",
                 "aliases": [], "member_ids": [1]}]
    new_nodes, _ = apply_clusters(nodes, [], clusters)
    names = {n["name"] for n in new_nodes}
    assert names == {"Frankfurt", "Berlin"}
    berlin = next(n for n in new_nodes if n["name"] == "Berlin")
    assert berlin["aliases"] == []


def test_apply_clusters_remaps_and_dedupes_edges():
    nodes = [_node(12, "Frankfurt am Main"), _node(47, "Frankfurt"), _node(5, "dkd", "Organization")]
    clusters = [{"prefLabel": "Frankfurt am Main", "label": "Location",
                 "aliases": ["Frankfurt"], "member_ids": [12, 47]}]
    edges = [
        {"id": 1, "src": 5, "dst": 12, "rel": "LOCATED_IN", "props": {"source": "domain"}},
        {"id": 2, "src": 5, "dst": 47, "rel": "LOCATED_IN", "props": {"source": "local"}},
    ]
    _, new_edges = apply_clusters(nodes, edges, clusters)
    assert len(new_edges) == 1
    assert new_edges[0]["rel"] == "LOCATED_IN"
    assert new_edges[0]["props"]["source"] == "local"  # local schlägt domain


def test_apply_clusters_drops_self_loops_from_merge():
    nodes = [_node(12, "Frankfurt am Main"), _node(47, "Frankfurt")]
    clusters = [{"prefLabel": "Frankfurt am Main", "label": "Location",
                 "aliases": ["Frankfurt"], "member_ids": [12, 47]}]
    edges = [{"id": 1, "src": 12, "dst": 47, "rel": "PARTNER_OF", "props": {"source": "local"}}]
    _, new_edges = apply_clusters(nodes, edges, clusters)
    assert new_edges == []


def test_apply_clusters_node_source_local_wins():
    nodes = [_node(1, "dkd", "Organization", source="domain"),
             _node(2, "dkd GmbH", "Organization", source="local")]
    clusters = [{"prefLabel": "dkd GmbH", "label": "Organization",
                 "aliases": ["dkd"], "member_ids": [1, 2]}]
    new_nodes, _ = apply_clusters(nodes, [], clusters)
    assert len(new_nodes) == 1
    assert new_nodes[0]["props"]["source"] == "local"


def test_apply_clusters_merges_member_props_local_wins():
    # Regression: gemergte Cluster-Nodes verloren bisher alle props außer `source`
    # (inkonsistent zum Singleton-Pfad, der via {**n} alles behält).
    nodes = [
        {"id": 1, "label": "Organization", "name": "dkd",
         "props": {"source": "domain", "description": "Agentur (domain)", "industry": "IT"}},
        {"id": 2, "label": "Organization", "name": "dkd GmbH",
         "props": {"source": "local", "description": "dkd Internet Service GmbH", "founded": "1998"}},
    ]
    clusters = [{"prefLabel": "dkd GmbH", "label": "Organization",
                 "aliases": ["dkd"], "member_ids": [1, 2]}]
    new_nodes, _ = apply_clusters(nodes, [], clusters)
    assert len(new_nodes) == 1
    props = new_nodes[0]["props"]
    assert props["source"] == "local"                       # local schlägt domain
    assert props["description"] == "dkd Internet Service GmbH"  # local-Wert bei Konflikt
    assert props["industry"] == "IT"                        # nur im domain-Member
    assert props["founded"] == "1998"                       # nur im local-Member


def test_fullname_clusters_merges_brand_and_legal_name():
    # Marke (Kurzname) + voller Rechtsname derselben Firma: props.fullName des
    # einen Nodes == name des anderen → ein Cluster. Das LLM-Matching sieht props
    # nicht und verfehlt diese Paare (Mankiewicz-Fall).
    nodes = [
        {"id": 11, "label": "Organization", "name": "Mankiewicz",
         "props": {"fullName": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)"}},
        {"id": 14, "label": "Organization", "name": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)",
         "props": {"type": "Subsidiary"}},
        {"id": 12, "label": "Organization", "name": "Mankiewicz Coatings LLC", "props": {}},
    ]
    clusters = fullname_clusters(nodes)
    assert len(clusters) == 1
    c = clusters[0]
    assert c["label"] == "Organization"
    assert _norm(c["prefLabel"]) == _norm("Mankiewicz Gebr. & Co. (GmbH & Co. KG)")
    assert set(c["member_ids"]) == {11, 14}
    assert "Mankiewicz" in c["aliases"]


def test_fullname_clusters_case_insensitive():
    nodes = [
        {"id": 1, "label": "Organization", "name": "ACME",
         "props": {"full_name": "acme corporation"}},
        {"id": 2, "label": "Organization", "name": "ACME Corporation", "props": {}},
    ]
    clusters = fullname_clusters(nodes)
    assert len(clusters) == 1
    assert set(clusters[0]["member_ids"]) == {1, 2}


def test_fullname_clusters_no_false_merge_without_name_match():
    # parent_company ist KEIN fullName-Key; und der Voll-Name matcht keinen
    # vorhandenen Node-Namen → keine Verschmelzung (Tochter bleibt eigenständig).
    nodes = [
        {"id": 12, "label": "Organization", "name": "Mankiewicz Coatings LLC",
         "props": {"parent_company": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)"}},
        {"id": 14, "label": "Organization", "name": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)",
         "props": {"fullName": "Etwas ganz anderes GmbH"}},
    ]
    assert fullname_clusters(nodes) == []


def test_fullname_clusters_respects_label_boundary():
    # Gleicher String, aber andere Label → kein Merge (Org-Name == Person-Name).
    nodes = [
        {"id": 1, "label": "Organization", "name": "Schmidt",
         "props": {"fullName": "Schmidt GmbH"}},
        {"id": 2, "label": "Person", "name": "Schmidt GmbH", "props": {}},
    ]
    assert fullname_clusters(nodes) == []


def test_fullname_then_apply_clusters_merges_into_one_node():
    # End-to-End deterministisch: fullname_clusters + apply_clusters → ein Node,
    # props beider Member erhalten (greift auf den props-Erhalt-Fix zurück).
    nodes = [
        {"id": 11, "label": "Organization", "name": "Mankiewicz",
         "props": {"source": "local", "fullName": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)",
                   "industry": "coatings"}},
        {"id": 14, "label": "Organization", "name": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)",
         "props": {"source": "local", "ceo": "Michael O. Grau"}},
    ]
    clusters = merge_clusters_by_preflabel(fullname_clusters(nodes))
    new_nodes, _ = apply_clusters(nodes, [], clusters)
    assert len(new_nodes) == 1
    n = new_nodes[0]
    assert "Mankiewicz" in n["aliases"]
    assert n["props"].get("industry") == "coatings"
    assert n["props"].get("ceo") == "Michael O. Grau"


def test_unify_overlapping_clusters_merges_shared_member():
    # Eine fullName-Brücke (member 237 ↔ 610) verbindet das LLM-"Mankiewicz"-Cluster
    # mit dem "...KG"-Cluster. Geteilter Member ⇒ eine Entität. prefLabel = das
    # Cluster mit den meisten Membern; die anderen prefLabels werden Aliase.
    clusters = [
        {"prefLabel": "Mankiewicz", "label": "Organization",
         "aliases": [], "member_ids": [237, 24, 43, 57]},                 # LLM, 4 Member
        {"prefLabel": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)", "label": "Organization",
         "aliases": [], "member_ids": [610, 927]},                        # LLM, 2 Member
        {"prefLabel": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)", "label": "Organization",
         "aliases": ["Mankiewicz"], "member_ids": [237, 610]},            # fullName-Brücke
    ]
    merged = unify_overlapping_clusters(clusters)
    assert len(merged) == 1
    c = merged[0]
    assert c["prefLabel"] == "Mankiewicz"                                 # meiste Member gewinnt
    assert set(c["member_ids"]) == {237, 24, 43, 57, 610, 927}
    assert "Mankiewicz Gebr. & Co. (GmbH & Co. KG)" in c["aliases"]


def test_unify_overlapping_clusters_leaves_disjoint_untouched():
    clusters = [
        {"prefLabel": "Frankfurt", "label": "Location", "aliases": ["FFM"], "member_ids": [1, 2]},
        {"prefLabel": "Berlin", "label": "Location", "aliases": [], "member_ids": [3, 4]},
    ]
    merged = unify_overlapping_clusters(clusters)
    assert len(merged) == 2
    prefs = {c["prefLabel"] for c in merged}
    assert prefs == {"Frankfurt", "Berlin"}


def test_unify_overlapping_clusters_is_transitive():
    # A∩B über 2, B∩C über 3 ⇒ alle drei zu einem.
    clusters = [
        {"prefLabel": "A", "label": "Organization", "aliases": [], "member_ids": [1, 2]},
        {"prefLabel": "B", "label": "Organization", "aliases": [], "member_ids": [2, 3]},
        {"prefLabel": "C", "label": "Organization", "aliases": [], "member_ids": [3, 4]},
    ]
    merged = unify_overlapping_clusters(clusters)
    assert len(merged) == 1
    assert set(merged[0]["member_ids"]) == {1, 2, 3, 4}


def test_fullname_bridge_unifies_whole_llm_clusters_end_to_end():
    # Realnah: viele "Mankiewicz"-Nodes + ein "...KG"-Node, nur einer trägt fullName.
    # Nach LLM-Cluster + fullName-Brücke + unify + apply ⇒ EIN Node, kein Rest-"Mankiewicz".
    nodes = [
        {"id": 1, "label": "Organization", "name": "Mankiewicz", "props": {"source": "local"}},
        {"id": 2, "label": "Organization", "name": "Mankiewicz", "props": {"source": "domain"}},
        {"id": 3, "label": "Organization", "name": "Mankiewicz",
         "props": {"source": "local", "fullName": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)"}},
        {"id": 4, "label": "Organization", "name": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)",
         "props": {"source": "local", "vat_number": "DE 118241333"}},
    ]
    llm_clusters = [
        {"prefLabel": "Mankiewicz", "label": "Organization", "aliases": [], "member_ids": [1, 2, 3]},
        {"prefLabel": "Mankiewicz Gebr. & Co. (GmbH & Co. KG)", "label": "Organization",
         "aliases": [], "member_ids": [4]},
    ]
    clusters = llm_clusters + fullname_clusters(nodes)
    clusters = merge_clusters_by_preflabel(clusters)
    clusters = unify_overlapping_clusters(clusters)
    new_nodes, _ = apply_clusters(nodes, [], clusters)
    org_nodes = [n for n in new_nodes if n["label"] == "Organization"]
    assert len(org_nodes) == 1                                            # KEINE Dublette mehr
    n = org_nodes[0]
    assert "Mankiewicz Gebr. & Co. (GmbH & Co. KG)" in n["aliases"]
    assert n["props"].get("vat_number") == "DE 118241333"                # props erhalten


def test_block_nodes_by_label_groups_by_label():
    nodes = [_node(1, "Frankfurt", "Location"), _node(2, "dkd", "Organization"),
             _node(3, "Berlin", "Location")]
    blocks = block_nodes_by_label(nodes)
    assert set(blocks.keys()) == {"Location", "Organization"}
    assert {n["id"] for n in blocks["Location"]} == {1, 3}


def test_split_block_chunks_alphabetically():
    nodes = [_node(i, name) for i, name in enumerate(["Delta", "Alpha", "Charlie", "Bravo"], 1)]
    chunks = split_block(nodes, max_nodes=2)
    assert len(chunks) == 2
    assert [n["name"] for n in chunks[0]] == ["Alpha", "Bravo"]
    assert [n["name"] for n in chunks[1]] == ["Charlie", "Delta"]


def test_split_block_no_split_when_small():
    nodes = [_node(1, "Alpha"), _node(2, "Bravo")]
    assert split_block(nodes, max_nodes=10) == [nodes]


def test_merge_clusters_by_preflabel_unions_members():
    clusters = [
        {"prefLabel": "Frankfurt am Main", "label": "Location", "aliases": ["Frankfurt"], "member_ids": [1, 2]},
        {"prefLabel": "frankfurt am main", "label": "Location", "aliases": ["FFM"], "member_ids": [3]},
        {"prefLabel": "Berlin", "label": "Location", "aliases": [], "member_ids": [4]},
    ]
    merged = merge_clusters_by_preflabel(clusters)
    assert len(merged) == 2
    ffm = next(c for c in merged if c["label"] == "Location" and "Frankfurt" in c["aliases"])
    assert sorted(ffm["member_ids"]) == [1, 2, 3]
    assert sorted(ffm["aliases"]) == ["FFM", "Frankfurt"]
