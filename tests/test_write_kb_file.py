"""Regression tests for ``openkb.agent.tools.write_kb_file``.

Covers the allow-list (``wiki/explorations/**`` and ``output/**``), path
traversal rejection, the bare-directory guard (e.g. ``"output"`` alone),
and automatic parent-directory creation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.agent.tools import write_kb_file


@pytest.fixture
def kb_root(tmp_path: Path) -> str:
    return str(tmp_path)


def test_rejects_empty_path(kb_root: str) -> None:
    result = write_kb_file("", "hi", kb_root)
    assert result.startswith("Access denied")


def test_rejects_bare_output_directory(kb_root: str) -> None:
    # "output" alone resolves to the directory itself and would crash
    # write_text with IsADirectoryError. The guard must reject it.
    result = write_kb_file("output", "hi", kb_root)
    assert result.startswith("Access denied")
    # And, critically, no file/dir should have been written at that name.
    assert not (Path(kb_root) / "output").exists() or (Path(kb_root) / "output").is_dir()


def test_rejects_bare_wiki_explorations_directory(kb_root: str) -> None:
    result = write_kb_file("wiki/explorations", "hi", kb_root)
    assert result.startswith("Access denied")


def test_rejects_outside_allow_list(kb_root: str) -> None:
    result = write_kb_file("wiki/summaries/x.md", "hi", kb_root)
    assert result.startswith("Access denied")
    assert not (Path(kb_root) / "wiki" / "summaries" / "x.md").exists()


def test_accepts_output_skill_path(kb_root: str) -> None:
    result = write_kb_file("output/skills/demo/SKILL.md", "# demo\n", kb_root)
    assert result == "Written: output/skills/demo/SKILL.md"
    written = Path(kb_root) / "output" / "skills" / "demo" / "SKILL.md"
    assert written.read_text(encoding="utf-8") == "# demo\n"


def test_accepts_wiki_exploration(kb_root: str) -> None:
    result = write_kb_file("wiki/explorations/transcript.md", "notes", kb_root)
    assert result == "Written: wiki/explorations/transcript.md"
    written = Path(kb_root) / "wiki" / "explorations" / "transcript.md"
    assert written.read_text(encoding="utf-8") == "notes"


def test_rejects_path_traversal(kb_root: str) -> None:
    result = write_kb_file("../escape.md", "evil", kb_root)
    assert result.startswith("Access denied")
    # Must not have written outside the KB root.
    assert not (Path(kb_root).parent / "escape.md").exists()


def test_rejects_traversal_via_allowed_prefix(kb_root: str) -> None:
    # Even when starting with an allowed-looking prefix, traversal must escape
    # the KB root (Path.resolve normalizes ``..``) and be rejected.
    result = write_kb_file("output/../../escape.md", "evil", kb_root)
    assert result.startswith("Access denied")


def test_creates_parent_directories(kb_root: str) -> None:
    # Deeply nested target with no pre-existing parents.
    target = "output/skills/new/nested/deep/SKILL.md"
    result = write_kb_file(target, "deep", kb_root)
    assert result == f"Written: {target}"
    assert (Path(kb_root) / target).read_text(encoding="utf-8") == "deep"
