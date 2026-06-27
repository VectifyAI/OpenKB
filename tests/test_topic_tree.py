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


def test_place_descends_then_drops(tmp_path):
    root = tmp_path / "concepts"
    tt.write_topic_md(root, "root", 0)
    tt.write_topic_md(root / "attention", "attention topic", 0)
    calls = []

    def choose(view, brief):
        calls.append([t for t, _ in view.child_topics])
        return "attention" if any(t == "attention" for t, _ in view.child_topics) else None

    path = tt.place_concept(
        root, "self-attention", "q attends k", "# self-attention\n", choose=choose
    )
    assert path == (root / "attention" / "self-attention.md")
    assert path.read_text(encoding="utf-8") == "# self-attention\n"
    assert calls == [["attention"], []]  # descended once, then stopped at leaf node


def test_place_triggers_overflow(tmp_path):
    root = tmp_path / "concepts"
    tt.write_topic_md(root, "root", 0)
    for i in range(tt.FANOUT_K):
        (root / f"c{i}.md").write_text("x", encoding="utf-8")
    fired = []
    tt.place_concept(
        root, "c-extra", "b", "x", choose=lambda v, b: None,
        on_overflow=lambda d: fired.append(d),
    )
    assert fired == [root]  # K existing + 1 new > FANOUT_K
