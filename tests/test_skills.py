"""Direct unit tests for :mod:`openkb.agent.skills`.

Tests the scanner that ``run_skill`` / ``build_chat_agent`` depend on
to discover ``SKILL.md`` packages across multiple root directories.
Pre-existing tests only exercised the scanner indirectly (via a
test_deck_prompt assertion that the built-in deck skill loads), so
edge-case behavior (precedence, malformed frontmatter, missing fields,
description truncation) was silently uncovered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openkb.agent.skills import (
    DEFAULT_SKILL_ROOTS,
    _parse_frontmatter,
    scan_local_skills,
)


@pytest.fixture(autouse=True)
def _isolate_home(monkeypatch, tmp_path):
    """Point ``$HOME`` at the test's tmp_path so the scanner's default
    ``~/.openkb/skills`` and ``~/.claude/skills`` roots resolve to empty
    locations under the test sandbox — otherwise the user's real
    installed skills leak into every test."""
    fake_home = tmp_path / "isolated-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # Neutralize the package-bundled roots so these unit tests exercise the
    # scanning primitive in isolation; bundled discovery is covered explicitly.
    monkeypatch.setattr("openkb.agent.skills.BUNDLED_SKILL_ROOTS", ())


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "A test skill.",
    body: str = "instructions",
) -> Path:
    """Drop a minimally well-formed SKILL.md and return its directory."""
    sk_dir = root / name
    sk_dir.mkdir(parents=True, exist_ok=True)
    (sk_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}",
        encoding="utf-8",
    )
    return sk_dir


# ─── scan_local_skills ──────────────────────────────────────────────────


def test_scan_returns_empty_when_no_skill_roots_exist(tmp_path: Path):
    assert scan_local_skills(tmp_path) == []


def test_scan_finds_kb_local_skills(tmp_path: Path):
    _write_skill(tmp_path / "skills", "alpha")
    _write_skill(tmp_path / "skills", "beta")
    skills = scan_local_skills(tmp_path)
    names = {s["name"] for s in skills}
    assert names == {"alpha", "beta"}


def test_scan_returns_sdk_shape(tmp_path: Path):
    """SDK contract: each entry has 'name', 'description', 'path' keys
    of type str. ``path`` is absolute (resolved)."""
    sk_dir = _write_skill(tmp_path / "skills", "shape-check")
    skills = scan_local_skills(tmp_path)
    assert len(skills) == 1
    s = skills[0]
    assert set(s.keys()) == {"name", "description", "path"}
    assert all(isinstance(s[k], str) for k in s)
    assert Path(s["path"]).is_absolute()
    assert Path(s["path"]) == sk_dir.resolve()


def test_scan_skips_dirs_without_skill_md(tmp_path: Path):
    """A subdirectory under skills/ that doesn't have SKILL.md is ignored,
    not an error."""
    (tmp_path / "skills" / "no-skill-here").mkdir(parents=True)
    (tmp_path / "skills" / "no-skill-here" / "README.md").write_text("nope")
    _write_skill(tmp_path / "skills", "real-skill")
    skills = scan_local_skills(tmp_path)
    assert [s["name"] for s in skills] == ["real-skill"]


def test_scan_skips_skill_without_description(tmp_path: Path):
    """SDK requires both name and description; missing description ->
    silently skipped (don't crash, don't include)."""
    sk_dir = tmp_path / "skills" / "no-desc"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text("---\nname: no-desc\n---\nbody")
    # And one well-formed one for control
    _write_skill(tmp_path / "skills", "well-formed")
    skills = scan_local_skills(tmp_path)
    assert [s["name"] for s in skills] == ["well-formed"]


def test_scan_falls_back_to_dir_name_when_frontmatter_omits_name(tmp_path: Path):
    """If frontmatter has ``description`` but no ``name``, the scanner
    uses the directory name as a sane default. This lets a user drop a
    skill into ``~/.openkb/skills/my-deck/`` without writing the name
    twice."""
    sk_dir = tmp_path / "skills" / "dir-name-only"
    sk_dir.mkdir(parents=True)
    (sk_dir / "SKILL.md").write_text("---\ndescription: x\n---\nbody")
    skills = scan_local_skills(tmp_path)
    assert [s["name"] for s in skills] == ["dir-name-only"]


def test_scan_truncates_long_description_to_1024(tmp_path: Path):
    """The SDK ``ShellToolLocalSkill`` description field has a length
    cap. Ours conservatively truncates at 1024 chars so any agent UI
    that surfaces this string can't be overrun."""
    long_desc = "a" * 5000
    _write_skill(tmp_path / "skills", "verbose", description=long_desc)
    skills = scan_local_skills(tmp_path)
    assert len(skills) == 1
    assert len(skills[0]["description"]) == 1024


def test_scan_first_hit_wins_across_roots(tmp_path: Path, monkeypatch):
    """When the same skill name lives in multiple roots, the EARLIER
    root wins. This is what lets a user override a built-in skill by
    dropping a same-named SKILL.md into ``<kb>/skills/``."""
    # Point the home-global root at a tmp location (so the test doesn't
    # rely on the real ~/.openkb/skills state)
    home_root = tmp_path / "home-openkb-skills"
    home_root.mkdir()
    _write_skill(home_root, "dup", description="HOME version")

    kb_local = tmp_path / "skills"
    _write_skill(kb_local, "dup", description="KB-LOCAL version")

    # extra_roots is APPENDED, so home_root (passed explicitly here)
    # comes after the default kb-local "skills/" — kb-local wins.
    skills = scan_local_skills(tmp_path, extra_roots=(str(home_root),))
    dup = next(s for s in skills if s["name"] == "dup")
    assert dup["description"] == "KB-LOCAL version"


def test_scan_extra_roots_appended_after_defaults(tmp_path: Path):
    """``extra_roots`` come AFTER the built-in defaults — they're a
    courtesy for callers that want to scan additional locations,
    not a way to override default order."""
    extra = tmp_path / "extra"
    _write_skill(extra, "extra-only")
    skills = scan_local_skills(tmp_path, extra_roots=(str(extra),))
    assert "extra-only" in {s["name"] for s in skills}


def test_scan_default_roots_listed(tmp_path: Path):
    """The module's published constant matches expectations — pin it so
    a refactor doesn't silently change which dirs are searched."""
    assert DEFAULT_SKILL_ROOTS == (
        "skills",
        "~/.openkb/skills",
        "~/.claude/skills",
    )


def test_scan_includes_bundled_skills(tmp_path: Path, monkeypatch):
    """Skills shipped with the package (deck themes / critic) are
    discovered even for a KB with no local ``skills/`` — this is what makes
    ``deck new`` work right after ``pip install``."""
    bundled = tmp_path / "bundled"
    _write_skill(bundled, "openkb-deck-neon", description="built-in deck theme")
    monkeypatch.setattr("openkb.agent.skills.BUNDLED_SKILL_ROOTS", (str(bundled),))
    names = {s["name"] for s in scan_local_skills(tmp_path)}
    assert "openkb-deck-neon" in names


def test_kb_skill_overrides_bundled(tmp_path: Path, monkeypatch):
    """Bundled roots are scanned last (lowest priority): a same-named skill
    in the KB wins, so users can customize a built-in theme."""
    bundled = tmp_path / "bundled"
    _write_skill(bundled, "openkb-deck-neon", description="BUILT-IN")
    monkeypatch.setattr("openkb.agent.skills.BUNDLED_SKILL_ROOTS", (str(bundled),))
    _write_skill(tmp_path / "skills", "openkb-deck-neon", description="KB OVERRIDE")
    match = next(s for s in scan_local_skills(tmp_path) if s["name"] == "openkb-deck-neon")
    assert match["description"] == "KB OVERRIDE"


# ─── _parse_frontmatter ──────────────────────────────────────────────────


def test_parse_frontmatter_happy_path():
    text = "---\nname: foo\ndescription: bar\n---\nbody text"
    meta, body = _parse_frontmatter(text)
    assert meta == {"name": "foo", "description": "bar"}
    assert body == "body text"


def test_parse_frontmatter_returns_empty_when_no_delim():
    """No leading ``---`` means no frontmatter; return ({}, full_text)."""
    text = "just a body, no frontmatter"
    meta, body = _parse_frontmatter(text)
    assert meta == {}
    assert body == text


def test_parse_frontmatter_returns_empty_when_unclosed():
    """Frontmatter that opens with ``---`` but never closes is malformed;
    treat as no frontmatter rather than raising."""
    text = "---\nname: foo\nbut no closing"
    meta, body = _parse_frontmatter(text)
    assert meta == {}
    assert body == text  # everything passed through as body


def test_parse_frontmatter_handles_malformed_yaml():
    """Malformed YAML inside the delimiters shouldn't crash; return
    ({}, body). Caller is responsible for asserting required keys."""
    text = "---\n: : :\nname: foo\n---\nbody"
    meta, body = _parse_frontmatter(text)
    assert meta == {}
    assert body == "body"


def test_parse_frontmatter_non_dict_yaml_returns_empty():
    """If the frontmatter parses but isn't a dict (e.g. a YAML list),
    treat as empty metadata rather than trying to use it."""
    text = "---\n- item-1\n- item-2\n---\nbody"
    meta, body = _parse_frontmatter(text)
    assert meta == {}
    # body is correctly extracted even though metadata was discarded
    assert body == "body"


def test_parse_frontmatter_preserves_body_with_dashes():
    """Body containing standalone ``---`` (a Markdown horizontal rule) is
    preserved intact — only the FIRST closing ``---`` ends frontmatter."""
    text = "---\nname: foo\ndescription: bar\n---\nIntro\n\n---\n\nMore body"
    meta, body = _parse_frontmatter(text)
    assert meta == {"name": "foo", "description": "bar"}
    assert body == "Intro\n\n---\n\nMore body"
