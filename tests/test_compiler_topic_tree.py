from openkb.agent.compiler import _write_concept
from openkb import topic_tree as tt


def test_write_concept_into_topic_dir(tmp_path):
    wiki = tmp_path / "wiki"
    _write_concept(
        wiki,
        "self-attention",
        "# self-attention\n",
        "summaries/doc.md",
        is_update=False,
        brief="q attends k",
        topic_dir=wiki / "concepts" / "attention",
    )
    assert (wiki / "concepts" / "attention" / "self-attention.md").is_file()
    assert not (wiki / "concepts" / "self-attention.md").exists()  # not flat


def test_place_topic_dir_descends(tmp_path):
    root = tmp_path / "concepts"
    tt.write_topic_md(root, "root", 0)
    tt.write_topic_md(root / "attention", "att", 0)
    node = tt.place_topic_dir(
        root, brief="q", choose=lambda v, b: "attention" if v.child_topics else None
    )
    assert node == root / "attention"
    # descent only — no concept leaf written here, just the topic file
    assert [p.name for p in node.glob("*.md")] == ["_topic.md"]
