from pathlib import Path

from openkb import topic_tree as tt


def _concept(d: Path, stem: str, brief: str):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.md").write_text(
        f'---\ntype: "Concept"\ndescription: "{brief}"\n---\n# {stem}\n',
        encoding="utf-8",
    )


def test_write_and_read_topic(tmp_path):
    root = tmp_path / "concepts"
    sub = root / "attention"
    tt.write_topic_md(sub, summary="All about attention.", size=2)
    _concept(sub, "self-attention", "queries attend to keys")
    _concept(sub, "multi-head", "parallel attention heads")
    view = tt.read_topic(root, "attention")
    assert view.summary == "All about attention."
    stems = {s for s, _ in view.child_concepts}
    assert stems == {"self-attention", "multi-head"}
    assert view.child_topics == []
    assert tt.child_count(sub) == 2
