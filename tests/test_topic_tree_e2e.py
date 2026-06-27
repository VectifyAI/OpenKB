"""Full-stack topic-tree integration (deterministic, no network).

Exercises the path a real ``openkb reindex`` takes — bootstrap a flat wiki of
concepts into a tree, then verify links survive the moves and the query tool
can navigate it — using injected deterministic callables instead of an LLM.
"""
from pathlib import Path

from openkb import topic_tree as tt
from openkb.lint import list_existing_wiki_targets, strip_ghost_wikilinks
from openkb.agent.tools import read_topic_node


def _concept(wiki: Path, stem: str, brief: str, links=()):
    d = wiki / "concepts"
    d.mkdir(parents=True, exist_ok=True)
    body_links = " ".join(f"[[{l}]]" for l in links)
    (d / f"{stem}.md").write_text(
        f'---\ntype: "Concept"\ndescription: "{brief}"\n---\n# {stem}\n{body_links}\n',
        encoding="utf-8",
    )


def test_reindex_builds_tree_links_survive_and_navigable(tmp_path):
    wiki = tmp_path / "wiki"
    stems = [f"concept-{i:02d}" for i in range(15)]
    for s in stems:
        _concept(wiki, s, f"brief for {s}", links=["concept-00"])  # all reference concept-00

    n = tt.bootstrap(
        wiki / "concepts",
        choose=lambda v, b: None,  # drop at root → forces a split past FANOUT_K
        cluster=lambda items: {
            "group-a": [s for s, _ in items[: len(items) // 2]],
            "group-b": [s for s, _ in items[len(items) // 2:]],
        },
        summarize=lambda name, briefs: f"summary of {name}",
    )
    assert n == 15

    # 1. a multi-level tree was built (at least one subtopic directory)
    assert any(d.is_dir() for d in (wiki / "concepts").iterdir())

    # 2. concept-00 was moved into a subtopic, yet the [[concept-00]] links survive
    assert not (wiki / "concepts" / "concept-00.md").exists()
    targets = list_existing_wiki_targets(wiki)
    out, ghosts = strip_ghost_wikilinks("see [[concept-00]]", targets)
    assert ghosts == []
    assert "[[concept-00]]" in out

    # 3. the query tool can navigate from the root
    root_render = read_topic_node("", str(wiki))
    assert "child topics" in root_render
    assert "group-a" in root_render
