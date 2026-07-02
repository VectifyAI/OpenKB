"""Regression tests for ``openkb.agent.tools.read_kb_file``.

Symmetric to :mod:`tests.test_write_kb_file`. ``read_kb_file`` is the
read-side allow-list exposed to skill agents via the
``read_output_or_skill_file`` function tool in
``openkb.agent.skill_runner``. It controls what files the agent can
inspect — particularly that it **cannot** see ``.openkb/config.yaml``
(which contains the LLM API key path), ``.env``, or anything outside
``wiki/``, ``output/``, ``skills/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.agent.tools import read_kb_file


@pytest.fixture
def kb_root(tmp_path: Path) -> str:
    return str(tmp_path)


def test_rejects_empty_path(kb_root: str) -> None:
    result = read_kb_file("", kb_root)
    assert result.startswith("Access denied")


def test_rejects_path_escape_via_dotdot(kb_root: str, tmp_path: Path) -> None:
    """Even if the relative path resolves outside kb_root via ``..``,
    the guard must reject it before reading anything."""
    # Create a secret file outside the KB
    secret = tmp_path.parent / "secret.txt"
    secret.write_text("API_KEY=xxx")
    try:
        result = read_kb_file("../secret.txt", kb_root)
        assert result.startswith("Access denied")
    finally:
        secret.unlink(missing_ok=True)


def test_rejects_absolute_path_outside_kb(kb_root: str) -> None:
    result = read_kb_file("/etc/passwd", kb_root)
    assert result.startswith("Access denied")


def test_rejects_dotopenkb_config(kb_root: str, tmp_path: Path) -> None:
    """The .openkb/ directory holds the user's model config and is the
    canonical place an attacker would aim for. Must be inaccessible."""
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-5.4\n")
    result = read_kb_file(".openkb/config.yaml", kb_root)
    assert result.startswith("Access denied")


def test_rejects_env_file(kb_root: str, tmp_path: Path) -> None:
    """``.env`` holds API keys; must not be readable by skill agents."""
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-xxx")
    result = read_kb_file(".env", kb_root)
    assert result.startswith("Access denied")


def test_rejects_raw_dir(kb_root: str, tmp_path: Path) -> None:
    """The ``raw/`` directory holds pre-ingest source documents — not
    in the read allow-list."""
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "doc.pdf").write_bytes(b"PDF bytes")
    result = read_kb_file("raw/doc.pdf", kb_root)
    assert result.startswith("Access denied")


def test_rejects_bare_kb_root(kb_root: str) -> None:
    """A bare empty/root path must be rejected."""
    result = read_kb_file(".", kb_root)
    # Either rejected as escape or as empty-after-relative — either is
    # a refusal, which is what we want.
    assert result.startswith("Access denied") or "not a file" in result.lower()


def test_returns_not_found_for_missing_allowed_path(kb_root: str, tmp_path: Path) -> None:
    """Inside the allow-list but file missing — clear error, no leak."""
    (tmp_path / "output").mkdir()
    result = read_kb_file("output/decks/missing/index.html", kb_root)
    assert "not found" in result.lower()


def test_reads_wiki_file(kb_root: str, tmp_path: Path) -> None:
    (tmp_path / "wiki").mkdir()
    target = tmp_path / "wiki" / "index.md"
    target.write_text("# Wiki content", encoding="utf-8")
    result = read_kb_file("wiki/index.md", kb_root)
    assert result == "# Wiki content"


def test_reads_output_file(kb_root: str, tmp_path: Path) -> None:
    """The whole point of ``read_kb_file`` (vs the existing wiki-only
    reader): skill agents need to inspect their own prior outputs to
    critique / iterate. Confirm ``output/`` is in the read scope."""
    out = tmp_path / "output" / "decks" / "foo"
    out.mkdir(parents=True)
    (out / "index.html").write_text("<html>deck content</html>", encoding="utf-8")
    result = read_kb_file("output/decks/foo/index.html", kb_root)
    assert "deck content" in result


def test_reads_skill_file(kb_root: str, tmp_path: Path) -> None:
    """Skill bodies must be readable so a meta-skill can introspect
    another skill (rare but legitimate use)."""
    sk = tmp_path / "skills" / "my-skill"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: my-skill\n---\nbody", encoding="utf-8")
    result = read_kb_file("skills/my-skill/SKILL.md", kb_root)
    assert "name: my-skill" in result


def test_rejects_relative_traversal_inside_allow_list(kb_root: str, tmp_path: Path) -> None:
    """`output/../.env` must NOT be allowed even though it starts with
    `output/`. The resolve-then-check pattern handles this; pin it."""
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-xxx")
    (tmp_path / "output").mkdir()
    result = read_kb_file("output/../.env", kb_root)
    assert result.startswith("Access denied")


def test_directory_target_returns_not_found(kb_root: str, tmp_path: Path) -> None:
    """If the path resolves to a directory inside the allow-list, the
    reader treats it as not-a-file rather than crashing."""
    (tmp_path / "output" / "decks").mkdir(parents=True)
    result = read_kb_file("output/decks", kb_root)
    # Directory exists but is not a file — should return a clear message.
    assert "not found" in result.lower() or "not a file" in result.lower()
