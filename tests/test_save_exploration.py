from __future__ import annotations

from openkb.cli import save_exploration


def test_save_exploration_cjk_question_gets_hash_slug(tmp_path):
    """CJK / punctuation-only questions must not collapse to an empty slug."""
    kb_dir = tmp_path
    (kb_dir / "wiki").mkdir()
    p = save_exploration(kb_dir, "这是什么问题？", "answer text")
    assert p is not None
    assert p.name != ".md"
    assert p.exists()


def test_save_exploration_same_question_uniquifies(tmp_path):
    """Two saves of the same question must not overwrite each other."""
    kb_dir = tmp_path
    (kb_dir / "wiki").mkdir()
    p1 = save_exploration(kb_dir, "What is RAG?", "answer 1")
    p2 = save_exploration(kb_dir, "What is RAG?", "answer 2")
    assert p1 != p2
    assert p1.read_text(encoding="utf-8").endswith("answer 1\n")
    assert p2.read_text(encoding="utf-8").endswith("answer 2\n")


def test_save_exploration_quote_in_question_safe_yaml(tmp_path):
    """A question containing a double-quote must not break frontmatter."""
    kb_dir = tmp_path
    (kb_dir / "wiki").mkdir()
    p = save_exploration(kb_dir, 'What does "RAG" mean?', "answer")
    content = p.read_text(encoding="utf-8")
    import yaml as _yaml

    fm_lines = content.split("---")[1]
    parsed = _yaml.safe_load(fm_lines)
    assert "query" in parsed
    assert "RAG" in parsed["query"]


def test_save_exploration_empty_answer_returns_none(tmp_path):
    kb_dir = tmp_path
    (kb_dir / "wiki").mkdir()
    assert save_exploration(kb_dir, "any question", "") is None


def test_save_exploration_strips_ghost_wikilinks(tmp_path):
    """Ghost wikilinks to non-existent targets should be stripped (matching CLI)."""
    kb_dir = tmp_path
    (kb_dir / "wiki").mkdir()
    p = save_exploration(kb_dir, "What is X?", "see [[nonexistent_page]] for details")
    content = p.read_text(encoding="utf-8")
    assert "[[nonexistent_page]]" not in content
    assert "for details" in content
