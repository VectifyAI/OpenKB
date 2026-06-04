"""Tests for openkb.agent.tools.grep_wiki_files — grep-based wiki search."""
from __future__ import annotations

import shutil

import pytest

import openkb.agent.tools as tools_mod
from openkb.agent.tools import grep_wiki_files

_HAS_GREP = shutil.which("grep") is not None
requires_grep = pytest.mark.skipif(not _HAS_GREP, reason="system grep not available")


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
    (root / "entities" / "vaswani.md").write_text(
        "# Vaswani\nAshish Vaswani is a lead author.\n", encoding="utf-8",
    )
    (root / "sources" / "note.md").write_text(
        "Short note: the lottery ticket hypothesis appears here only.\n"
        "It also discusses a large language model in passing.\n",
        encoding="utf-8",
    )
    # Long-doc per-page JSON — never grepped (only *.md is searched).
    (root / "sources" / "book.json").write_text(
        '[{"page": 1, "text": "transformer secret in json"}]\n',
        encoding="utf-8",
    )
    # Bookkeeping / scaffolding — never grepped.
    (root / "log.md").write_text(
        "# Operations Log\n## [2026-01-01] ingest | transformer\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text(
        "# Schema\nThis schema describes synthesis and transformer concepts.\n",
        encoding="utf-8",
    )
    (root / "SCHEMA.md").write_text(
        "# Schema alias\nMentions transformer too.\n", encoding="utf-8",
    )
    return str(root)


# --- scope: what gets matched -------------------------------------------------

@requires_grep
def test_finds_match_in_summaries(tmp_path):
    out = grep_wiki_files("self-attention", _wiki(tmp_path))
    assert "summaries/paper.md:" in out
    assert "self-attention" in out


@requires_grep
def test_finds_match_in_concepts(tmp_path):
    out = grep_wiki_files("Scaled dot-product", _wiki(tmp_path))
    assert "concepts/attention.md:" in out


@requires_grep
def test_finds_match_in_entities(tmp_path):
    out = grep_wiki_files("Ashish Vaswani", _wiki(tmp_path))
    assert "entities/vaswani.md:" in out


@requires_grep
def test_finds_match_in_short_source_md(tmp_path):
    out = grep_wiki_files("lottery ticket", _wiki(tmp_path))
    assert "sources/note.md:" in out


# --- scope: what gets excluded ------------------------------------------------

@requires_grep
def test_excludes_long_doc_json(tmp_path):
    out = grep_wiki_files("transformer", _wiki(tmp_path))
    assert "book.json" not in out


@requires_grep
def test_excludes_log_md(tmp_path):
    out = grep_wiki_files("transformer", _wiki(tmp_path))
    assert "log.md" not in out


@requires_grep
def test_excludes_agents_md(tmp_path):
    # AGENTS.md contains 'synthesis' and 'transformer' but is scaffolding.
    out = grep_wiki_files("synthesis", _wiki(tmp_path))
    assert "AGENTS.md" not in out
    assert out == "No matches for synthesis."


@requires_grep
def test_excludes_schema_md(tmp_path):
    out = grep_wiki_files("transformer", _wiki(tmp_path))
    assert "SCHEMA.md" not in out


@requires_grep
def test_excludes_images_dir(tmp_path):
    wiki = _wiki(tmp_path)
    (tmp_path / "wiki" / "sources" / "images" / "caption.md").write_text(
        "transformer figure caption\n", encoding="utf-8",
    )
    out = grep_wiki_files("transformer", wiki)
    assert "images/" not in out


# --- regex dialect ------------------------------------------------------------

@requires_grep
def test_ere_alternation_matches(tmp_path):
    # ERE alternation must work (regression for the BRE-vs-Rust-regex bug).
    out = grep_wiki_files("LLM|large language model", _wiki(tmp_path))
    assert "sources/note.md:" in out


@requires_grep
def test_fixed_string_treats_pipe_literally(tmp_path):
    # As a literal, 'LLM|large language model' does not appear anywhere.
    out = grep_wiki_files("LLM|large language model", _wiki(tmp_path), fixed_string=True)
    assert out == "No matches for LLM|large language model."


@requires_grep
def test_fixed_string_vs_regex_dot(tmp_path):
    wiki = _wiki(tmp_path)
    # literal 'self.attention' does not appear (text has 'self-attention')
    assert grep_wiki_files("self.attention", wiki, fixed_string=True) == \
        "No matches for self.attention."
    # as a regex, '.' matches the hyphen
    assert "summaries/paper.md:" in grep_wiki_files("self.attention", wiki, fixed_string=False)


# --- case sensitivity ---------------------------------------------------------

@requires_grep
def test_case_insensitive_by_default(tmp_path):
    out = grep_wiki_files("TRANSFORMER", _wiki(tmp_path))
    assert "summaries/paper.md:" in out


@requires_grep
def test_case_sensitive_when_disabled(tmp_path):
    out = grep_wiki_files("TRANSFORMER", _wiki(tmp_path), ignore_case=False)
    assert out == "No matches for TRANSFORMER."


# --- guards / messages --------------------------------------------------------

@requires_grep
def test_no_match_returns_message(tmp_path):
    out = grep_wiki_files("nonexistentterm12345", _wiki(tmp_path))
    assert out == "No matches for nonexistentterm12345."


def test_empty_pattern_guarded(tmp_path):
    out = grep_wiki_files("", _wiki(tmp_path))
    assert out == "Provide a non-empty search pattern."


def test_whitespace_pattern_guarded(tmp_path):
    out = grep_wiki_files("   ", _wiki(tmp_path))
    assert out == "Provide a non-empty search pattern."


def test_grep_unavailable_returns_message(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_mod, "_grep_binary", lambda: None)
    out = grep_wiki_files("transformer", _wiki(tmp_path))
    assert out == "grep unavailable on this system."


# --- paths --------------------------------------------------------------------

@requires_grep
def test_paths_are_relative_to_wiki_root(tmp_path):
    wiki = _wiki(tmp_path)
    out = grep_wiki_files("self-attention", wiki)
    assert wiki not in out  # no absolute-path leak
    # order-independent: the summaries hit is present as a relative path
    assert any(ln.startswith("summaries/paper.md:") for ln in out.splitlines())


@requires_grep
def test_result_cap_and_truncation_notice(tmp_path):
    wiki = _wiki(tmp_path)
    big = "\n".join(f"line {i} needle" for i in range(60))
    (tmp_path / "wiki" / "summaries" / "big.md").write_text(big + "\n", encoding="utf-8")
    out = grep_wiki_files("needle", wiki)
    lines = out.splitlines()
    assert lines[-1] == "… more matches; narrow the pattern."
    assert len(lines) == 51


# --- safety -------------------------------------------------------------------

@requires_grep
def test_shell_metacharacters_do_not_execute(tmp_path):
    wiki = _wiki(tmp_path)
    sentinel = tmp_path / "pwned"
    grep_wiki_files("; touch " + str(sentinel), wiki)
    assert not sentinel.exists()


@requires_grep
def test_non_utf8_bytes_do_not_raise(tmp_path):
    wiki = _wiki(tmp_path)
    # A matched line with a non-UTF-8 byte must not raise (errors='replace').
    (tmp_path / "wiki" / "summaries" / "latin.md").write_bytes(
        b"caf\xe9 transformer here\n"
    )
    out = grep_wiki_files("transformer", wiki)  # must return a string, not raise
    assert isinstance(out, str)
    assert "summaries/" in out


# --- command construction (binary-agnostic, no real grep needed) --------------

def test_grep_command_built_with_ere_and_excludes(tmp_path, monkeypatch):
    wiki = _wiki(tmp_path)
    monkeypatch.setattr(tools_mod, "_grep_binary", lambda: "/usr/bin/grep")
    captured = {}

    class _FakeProc:
        returncode = 1
        stdout = ""
        stderr = ""

    def _fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["shell"] = kwargs.get("shell", False)
        return _FakeProc()

    monkeypatch.setattr(tools_mod.subprocess, "run", _fake_run)
    grep_wiki_files("needle", wiki, ignore_case=True, fixed_string=False)

    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/grep"
    assert captured["shell"] is False
    assert "-rn" in cmd
    assert "--include=*.md" in cmd
    assert "-i" in cmd
    assert "-E" in cmd and "-F" not in cmd
    for name in ("AGENTS.md", "SCHEMA.md", "log.md"):
        assert f"--exclude={name}" in cmd
    assert cmd[-3] == "-e"
    assert cmd[-2] == "needle"
    from pathlib import Path as _P
    assert cmd[-1] == str(_P(wiki).resolve())
    assert "--exclude-dir=images" in cmd
    assert "--exclude-dir=.git" in cmd


def test_grep_command_uses_F_when_fixed_and_omits_i(tmp_path, monkeypatch):
    wiki = _wiki(tmp_path)
    monkeypatch.setattr(tools_mod, "_grep_binary", lambda: "/usr/bin/grep")
    captured = {}

    class _FakeProc:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        tools_mod.subprocess, "run",
        lambda cmd, *a, **k: (captured.__setitem__("cmd", cmd) or _FakeProc()),
    )
    grep_wiki_files("a|b", wiki, ignore_case=False, fixed_string=True)
    cmd = captured["cmd"]
    assert "-F" in cmd and "-E" not in cmd
    assert "-i" not in cmd


# --- returncode handling ------------------------------------------------------

def test_partial_error_preserves_matches(tmp_path, monkeypatch):
    """grep exit >=2 (e.g. one unreadable file) must NOT discard valid matches."""
    wiki = _wiki(tmp_path)
    root_str = str((tmp_path / "wiki").resolve())
    monkeypatch.setattr(tools_mod, "_grep_binary", lambda: "/usr/bin/grep")

    class _FakeProc:
        returncode = 2
        stdout = f"{root_str}/summaries/paper.md:2:self-attention here\n"
        stderr = "grep: /x/locked: Permission denied"

    monkeypatch.setattr(tools_mod.subprocess, "run", lambda *a, **k: _FakeProc())
    out = grep_wiki_files("self-attention", wiki)
    assert "summaries/paper.md:" in out
    assert "grep error" not in out


def test_error_with_no_results_returns_error(tmp_path, monkeypatch):
    wiki = _wiki(tmp_path)
    monkeypatch.setattr(tools_mod, "_grep_binary", lambda: "/usr/bin/grep")

    class _FakeProc:
        returncode = 2
        stdout = ""
        stderr = "grep: something broke\nsecond line"

    monkeypatch.setattr(tools_mod.subprocess, "run", lambda *a, **k: _FakeProc())
    out = grep_wiki_files("whatever", wiki)
    assert out == "grep error: grep: something broke."


def test_timeout_returns_message(tmp_path, monkeypatch):
    import subprocess as _sp
    wiki = _wiki(tmp_path)
    monkeypatch.setattr(tools_mod, "_grep_binary", lambda: "/usr/bin/grep")

    def _raise_timeout(*a, **k):
        raise _sp.TimeoutExpired(cmd="grep", timeout=10)

    monkeypatch.setattr(tools_mod.subprocess, "run", _raise_timeout)
    out = grep_wiki_files("transformer", wiki)
    assert out == "grep timed out; narrow the pattern."
