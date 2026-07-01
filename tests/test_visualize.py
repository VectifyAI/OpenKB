from pathlib import Path

from openkb.visualize import build_graph, render_html


def _wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    for sub in ("summaries", "concepts", "entities", "reports", "sources"):
        (wiki / sub).mkdir(parents=True)
    (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    return wiki


def test_build_graph_nodes_edges_types(tmp_path):
    wiki = _wiki(tmp_path)
    (wiki / "summaries" / "paper.md").write_text(
        '---\ntype: "Summary"\ndescription: "A paper."\nfull_text: "sources/paper.json"\n---\n\n'
        "Discusses [[concepts/attention]] and [[entities/anthropic]].\n",
        encoding="utf-8",
    )
    (wiki / "concepts" / "attention.md").write_text(
        '---\ntype: "Concept"\ndescription: "Focus."\nsources: ["summaries/paper"]\n---\n\n'
        "Used by [[concepts/attention]] (self) and [[concepts/missing]] (broken).\n",
        encoding="utf-8",
    )
    (wiki / "entities" / "anthropic.md").write_text(
        '---\ntype: "Organization"\ndescription: "AI lab."\n---\n\n# Anthropic\n', encoding="utf-8"
    )
    (wiki / "concepts" / "orphan.md").write_text("# Orphan\n\nNo links.\n", encoding="utf-8")

    g = build_graph(wiki)
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"summaries/paper", "concepts/attention", "entities/anthropic", "concepts/orphan"}
    by = {n["id"]: n for n in g["nodes"]}
    assert by["concepts/orphan"]["type"] == "Concept"
    assert by["entities/anthropic"]["type"] == "Organization"
    edge_pairs = {(e["source"], e["target"]) for e in g["edges"]}
    assert ("summaries/paper", "concepts/attention") in edge_pairs
    assert ("summaries/paper", "entities/anthropic") in edge_pairs
    assert not any(e["target"] == "concepts/missing" for e in g["edges"])
    assert not any(e["source"] == e["target"] for e in g["edges"])
    assert by["concepts/attention"]["in"] == 1 and by["summaries/paper"]["out"] == 2
    assert g["types"] == ["Concept", "Organization", "Summary"]
    # sources: concepts use the `sources` field; summaries fall back to `full_text` (the origin doc)
    assert by["concepts/attention"]["sources"] == ["summaries/paper"]
    assert by["summaries/paper"]["sources"] == ["sources/paper.json"]


def test_build_graph_empty_wiki(tmp_path):
    assert build_graph(_wiki(tmp_path)) == {"nodes": [], "edges": [], "types": []}


def test_render_html_self_contained():
    g = {
        "nodes": [
            {
                "id": "concepts/a",
                "label": "a",
                "type": "Concept",
                "description": "x—y",
                "sources": [],
                "out": 0,
                "in": 0,
            }
        ],
        "edges": [],
        "types": ["Concept"],
    }
    html = render_html(g)
    assert "<canvas" in html
    assert '"concepts/a"' in html and '"Concept"' in html
    assert "__GRAPH_DATA__" not in html
    assert "x—y" in html
    assert "http://" not in html and "https://" not in html
