"""Tests for openkb.skill.tools — path-scoped IO for the skill-create agent."""

from __future__ import annotations

from openkb.skill.tools import (
    list_wiki_dir,
    read_wiki_file_for_skill,
    write_skill_file,
)


def _make_wiki(tmp_path):
    wiki = tmp_path / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "summaries").mkdir(parents=True)
    (wiki / "index.md").write_text("# index\n")
    (wiki / "concepts" / "attention.md").write_text("# attention\n")
    return wiki


def test_read_wiki_file_returns_content(tmp_path):
    wiki = _make_wiki(tmp_path)
    out = read_wiki_file_for_skill("concepts/attention.md", str(wiki))
    assert "attention" in out


def test_read_wiki_file_rejects_escape(tmp_path):
    wiki = _make_wiki(tmp_path)
    out = read_wiki_file_for_skill("../secret.txt", str(wiki))
    assert "Access denied" in out


def test_list_wiki_dir_returns_filenames(tmp_path):
    wiki = _make_wiki(tmp_path)
    out = list_wiki_dir("concepts", str(wiki))
    assert "attention.md" in out


def test_list_wiki_dir_handles_missing(tmp_path):
    wiki = _make_wiki(tmp_path)
    out = list_wiki_dir("nonexistent", str(wiki))
    assert out == "No files found."


def test_write_skill_file_creates_file_and_parents(tmp_path):
    skill_root = tmp_path / "output" / "skills" / "demo"
    out = write_skill_file(
        "references/methodology.md",
        "# methodology\n",
        str(skill_root),
    )
    assert "Written" in out
    assert (skill_root / "references" / "methodology.md").read_text() == "# methodology\n"


def test_write_skill_file_rejects_path_traversal(tmp_path):
    skill_root = tmp_path / "output" / "skills" / "demo"
    skill_root.mkdir(parents=True)
    out = write_skill_file("../escaped.md", "x", str(skill_root))
    assert "Access denied" in out
    assert not (skill_root.parent / "escaped.md").exists()


def test_write_skill_file_rejects_absolute_path(tmp_path):
    skill_root = tmp_path / "output" / "skills" / "demo"
    skill_root.mkdir(parents=True)
    out = write_skill_file("/etc/passwd", "x", str(skill_root))
    assert "Access denied" in out
