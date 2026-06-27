from openkb.agent.tools import read_topic_node
from openkb import topic_tree as tt


def test_read_topic_node_renders(tmp_path):
    wiki = tmp_path / "wiki"
    root = wiki / "concepts"
    tt.write_topic_md(root, "root summary", 1)
    tt.write_topic_md(root / "attention", "attention summary", 1)
    (root / "attention" / "self-attention.md").write_text(
        '---\ntype: "Concept"\ndescription: "q attends k"\n---\n', encoding="utf-8"
    )
    out = read_topic_node("attention", str(wiki))
    assert "attention summary" in out
    assert "self-attention" in out
    assert "q attends k" in out
