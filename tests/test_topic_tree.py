from pathlib import Path
from unittest.mock import patch

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


def test_split_clusters_and_moves(tmp_path):
    root = tmp_path / "concepts"
    tt.write_topic_md(root, "root", 0)
    for s in ("self-attention", "multi-head", "adam", "warmup"):
        (root / f"{s}.md").write_text(f"# {s}\n", encoding="utf-8")

    def cluster(items):
        return {
            "attention": ["self-attention", "multi-head"],
            "training": ["adam", "warmup"],
        }

    tt.split_node(root, cluster=cluster, summarize=lambda n, b: f"summary of {n}")
    assert (root / "attention" / "self-attention.md").is_file()
    assert (root / "training" / "adam.md").is_file()
    assert not (root / "self-attention.md").exists()  # moved, not copied
    assert tt.read_topic(root, "attention").summary == "summary of attention"


def _half_cluster(items):
    """Deterministic fake: split the items into two halves (a/b)."""
    half = len(items) // 2
    return {
        "a": [s for s, _ in items[:half]],
        "b": [s for s, _ in items[half:]],
    }


def test_bootstrap_topdown_global(tmp_path):
    """Bootstrap clusters the FULL set top-down (global view), not one-by-one."""
    root = tmp_path / "concepts"
    root.mkdir(parents=True)
    stems = [f"a{i}" for i in range(6)] + [f"b{i}" for i in range(6)]  # 12 > FANOUT_K
    for s in stems:
        (root / f"{s}.md").write_text(f"# {s}\n", encoding="utf-8")

    def cluster(items):  # clean global split by prefix
        return {
            "group-a": [s for s, _ in items if s.startswith("a")],
            "group-b": [s for s, _ in items if s.startswith("b")],
        }

    n = tt.bootstrap(root, cluster=cluster, summarize=lambda name, briefs: f"s {name}")
    assert n == 12
    assert (root / "_topic.md").exists()
    assert (root / "group-a" / "a0.md").is_file()
    assert (root / "group-b" / "b0.md").is_file()
    assert not (root / "a0.md").exists()  # nothing left flat at root
    assert tt.read_topic(root, "group-a").summary == "s group-a"


def test_bootstrap_recurses_until_under_fanout(tmp_path):
    """A group still larger than FANOUT_K recurses into sub-topics (depth grows)."""
    root = tmp_path / "concepts"
    root.mkdir(parents=True)
    n_concepts = tt.FANOUT_K * 2 + 5  # forces >1 level of recursion
    for i in range(n_concepts):
        (root / f"c{i:02d}.md").write_text(f"# c{i}\n", encoding="utf-8")
    n = tt.bootstrap(root, cluster=_half_cluster, summarize=lambda name, briefs: "s")
    assert n == n_concepts
    # at least one subtopic that itself has a subtopic (depth >= 2)
    deep = [d for d in root.rglob("*") if d.is_dir() and any(c.is_dir() for c in d.iterdir())]
    assert deep, "expected a multi-level tree (a subtopic containing subtopics)"
    # every leaf node holds <= FANOUT_K concepts
    for d in root.rglob("*"):
        if d.is_dir():
            assert tt.child_count(d) <= tt.FANOUT_K


def test_make_choose_parses_pick(tmp_path):
    from openkb import topic_tree_llm as ttl

    root = tmp_path / "concepts"
    tt.write_topic_md(root, "root", 0)
    tt.write_topic_md(root / "attention", "att", 0)
    view = tt.read_topic(root, "")
    with patch.object(ttl, "_llm_call", return_value='{"pick": "attention"}'):
        assert ttl.make_choose("gpt-5.4")(view, "q attends k") == "attention"
    with patch.object(ttl, "_llm_call", return_value='{"pick": null}'):
        assert ttl.make_choose("gpt-5.4")(view, "x") is None
