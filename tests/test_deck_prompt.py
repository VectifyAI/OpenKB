"""Sanity tests for the built-in deck-editorial skill.

After the skill-system refactor the deck prompt moved from
``openkb/prompts/deck_create.md`` (a ``str.format``-style template) to
``skills/openkb-deck-editorial/SKILL.md`` (a standalone Anthropic-style
skill with YAML frontmatter that ``run_skill`` loads directly). These
tests pin the structural anchors the validator and generator depend on.
"""

from __future__ import annotations

from pathlib import Path

from openkb.agent.skills import _parse_frontmatter

SKILL_MD = Path(__file__).resolve().parent.parent / "skills" / "openkb-deck-editorial" / "SKILL.md"


def _load() -> tuple[dict, str]:
    return _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))


def test_skill_file_present_and_substantive():
    assert SKILL_MD.is_file(), f"skill file missing at {SKILL_MD}"
    meta, body = _load()
    assert meta.get("name") == "openkb-deck-editorial"
    assert isinstance(meta.get("description"), str) and len(meta["description"]) > 40
    assert len(body) > 1000, "skill body is suspiciously short"


def test_skill_lists_all_allowed_data_types():
    _, body = _load()
    for t in ("cover", "chapter", "thesis", "quote", "compare", "data", "closing"):
        assert t in body, f"slide grammar must mention data-type={t}"


def test_skill_lists_editorial_monocle_tokens():
    _, body = _load()
    # Palette values must appear so the agent can copy them verbatim.
    for hex_value in ("#f3eee1", "#1a1612", "#a4341c", "#fff3a8"):
        assert hex_value in body, f"palette token {hex_value} missing"
    assert "Charter" in body  # serif stack
    assert "16:9" in body or "aspect-ratio" in body


def test_skill_description_triggers_on_deck_requests():
    """The agent picks skills by description matching. The deck skill's
    description must include the words a user would naturally type."""
    meta, _ = _load()
    desc = meta["description"].lower()
    # at least one explicit trigger word
    assert any(word in desc for word in ("deck", "slide", "ppt", "presentation", "演示", "幻灯"))
