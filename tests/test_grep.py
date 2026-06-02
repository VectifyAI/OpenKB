"""Tests for openkb.agent.tools.grep_wiki_files — lexical wiki search."""
from __future__ import annotations

from openkb.agent.tools import grep_wiki_files


def _wiki(tmp_path):
    """Build a minimal wiki/ tree and return its root as a string."""
    root = tmp_path / "wiki"
    (root / "summaries").mkdir(parents=True)
    (root / "concepts").mkdir(parents=True)
    (root / "entities").mkdir(parents=True)
    (root / "sources" / "images").mkdir(parents=True)
    (root / "summaries" / "paper.md").write_text(
        "# Paper\nThe transformer architecture uses self-attention.\n",
        encoding="utf-8",
    )
    (root / "concepts" / "attention.md").write_text(
        "# Attention\nScaled dot-product Attention is central.\n",
        encoding="utf-8",
    )
    (root / "sources" / "note.md").write_text(
        "Short note: the lottery ticket hypothesis appears here only.\n",
        encoding="utf-8",
    )
    # Long-doc per-page JSON — must NEVER be grepped.
    (root / "sources" / "book.json").write_text(
        '[{"page": 1, "text": "transformer secret in json"}]\n',
        encoding="utf-8",
    )
    # Bookkeeping — must NEVER be grepped.
    (root / "log.md").write_text(
        "# Operations Log\n## [2026-01-01] ingest | transformer\n",
        encoding="utf-8",
    )
    return str(root)


def test_finds_match_in_summaries(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("self-attention", wiki)
    assert "summaries/paper.md:" in out
    assert "self-attention" in out


def test_finds_match_in_short_source_md(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("lottery ticket", wiki)
    assert "sources/note.md:" in out


def test_excludes_long_doc_json(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("transformer", wiki)
    assert "book.json" not in out


def test_excludes_log_md(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("transformer", wiki)
    assert "log.md" not in out


def test_case_insensitive_by_default(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("TRANSFORMER", wiki)
    assert "summaries/paper.md:" in out


def test_case_sensitive_when_disabled(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("TRANSFORMER", wiki, ignore_case=False)
    assert out == "No matches for TRANSFORMER."


def test_fixed_string_treats_regex_literally(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("self.attention", wiki, fixed_string=True)
    # literal "self.attention" does not appear (text has "self-attention")
    assert out == "No matches for self.attention."
    # but as a regex, "." matches the hyphen
    out2 = grep_wiki_files("self.attention", wiki, fixed_string=False)
    assert "summaries/paper.md:" in out2


def test_no_match_returns_message(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("nonexistentterm12345", wiki)
    assert out == "No matches for nonexistentterm12345."


def test_paths_are_relative_to_wiki_root(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("self-attention", wiki)
    assert wiki not in out
    assert out.splitlines()[0].startswith("summaries/")


def test_result_cap_and_truncation_notice(tmp_path):
    wiki = _wiki(tmp_path)
    root = tmp_path / "wiki" / "summaries"
    big = "\n".join(f"line {i} needle" for i in range(60))
    (root / "big.md").write_text(big + "\n", encoding="utf-8")
    out = grep_wiki_files("needle", wiki)
    lines = out.splitlines()
    assert lines[-1] == "… more matches; narrow the pattern."
    assert len(lines) == 51


def test_shell_metacharacters_do_not_execute(tmp_path):
    wiki = _wiki(tmp_path)
    sentinel = tmp_path / "pwned"
    grep_wiki_files("; touch " + str(sentinel), wiki)
    assert not sentinel.exists()
