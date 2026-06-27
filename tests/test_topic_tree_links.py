from pathlib import Path

from openkb.lint import list_existing_wiki_targets, strip_ghost_wikilinks
from openkb import topic_tree as tt


def _mk(p: Path, text: str = "x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_targets_include_bare_stem_for_nested_concept(tmp_path):
    wiki = tmp_path / "wiki"
    _mk(wiki / "concepts" / "attention-and-transformers" / "self-attention.md")
    targets = list_existing_wiki_targets(wiki)
    assert "self-attention" in targets  # bare stem resolves
    assert "concepts/attention-and-transformers/self-attention" in targets


def test_bare_stem_link_not_stripped_when_nested(tmp_path):
    wiki = tmp_path / "wiki"
    _mk(wiki / "concepts" / "topic" / "self-attention.md")
    targets = list_existing_wiki_targets(wiki)
    out, ghosts = strip_ghost_wikilinks("see [[self-attention]]", targets)
    assert ghosts == []  # link survives despite living in a subfolder
    assert "[[self-attention]]" in out


def test_link_resolves_after_split_move(tmp_path):
    wiki = tmp_path / "wiki"
    root = wiki / "concepts"
    tt.write_topic_md(root, "root", 0)
    (root / "self-attention.md").write_text("# self-attention\n", encoding="utf-8")
    tt.split_node(
        root,
        cluster=lambda items: {"attention": ["self-attention"]},
        summarize=lambda n, b: "s",
    )
    targets = list_existing_wiki_targets(wiki)
    out, ghosts = strip_ghost_wikilinks("see [[self-attention]]", targets)
    assert ghosts == []  # bare-stem link still resolves after the move
