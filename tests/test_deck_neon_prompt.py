"""Sanity tests for the built-in deck-neon skill.

``openkb-deck-neon`` is the DEFAULT deck skill (``DEFAULT_DECK_SKILL`` in
``openkb/deck/creator.py``), so a typo in its frontmatter or a broken ``od``
contract would break the most-traveled ``openkb deck new`` path at runtime
while every other test stayed green. These tests pin the structural anchors
the validator and generator depend on — mirroring ``test_deck_prompt.py``
which guards the sibling deck-editorial skill.
"""
from __future__ import annotations

from pathlib import Path

from openkb.agent.skills import _parse_frontmatter
from openkb.deck.creator import DEFAULT_DECK_SKILL

SKILL_MD = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "openkb-deck-neon"
    / "SKILL.md"
)


def _load() -> tuple[dict, str]:
    return _parse_frontmatter(SKILL_MD.read_text(encoding="utf-8"))


def test_neon_is_the_default_deck_skill():
    # The skill dir name, the frontmatter name, and the routed default must agree.
    assert DEFAULT_DECK_SKILL == "openkb-deck-neon"
    meta, _ = _load()
    assert meta.get("name") == "openkb-deck-neon"


def test_skill_file_present_and_substantive():
    assert SKILL_MD.is_file(), f"skill file missing at {SKILL_MD}"
    meta, body = _load()
    assert isinstance(meta.get("description"), str) and len(meta["description"]) > 40
    assert len(body) > 1000, "skill body is suspiciously short"


def test_od_contract_is_intact():
    """run_skill reads od.mode/output_path_template; validate_deck reads
    od.deck_grammar. A missing key silently skips output enforcement."""
    meta, _ = _load()
    od = meta.get("od")
    assert isinstance(od, dict), "frontmatter must carry an `od:` block"
    assert od.get("mode") == "deck"
    assert od.get("output_path_template") == "output/decks/{slug}/index.html"
    grammar = od.get("deck_grammar")
    assert isinstance(grammar, dict), "od.deck_grammar must be present"
    assert grammar.get("kind_attr") == "data-type"
    assert "cover" in grammar.get("required", [])
    assert "closing" in grammar.get("required", [])
    allowed = grammar.get("allowed", [])
    for t in ("cover", "chapter", "thesis", "quote", "compare", "data", "closing"):
        assert t in allowed, f"deck_grammar.allowed must include {t}"


def test_skill_lists_all_allowed_data_types():
    _, body = _load()
    for t in ("cover", "chapter", "thesis", "quote", "compare", "data", "closing"):
        assert t in body, f"slide grammar must mention data-type={t}"


def test_skill_lists_aurora_glass_tokens():
    _, body = _load()
    # Neon palette values must appear so the agent can copy them verbatim.
    for hex_value in ("#080b11", "#2dd4bf", "#38bdf8", "#e879f9", "#f6b94b"):
        assert hex_value in body, f"palette token {hex_value} missing"
    assert "JetBrains Mono" in body  # mono stack
    # Fill-viewport frame, not a fixed card.
    assert "position:fixed" in body and "inset:0" in body


def test_skill_is_self_contained_and_no_web_fonts():
    _, body = _load()
    # The deck must be self-contained; the skill must forbid web fonts.
    assert "self-contained" in body.lower()
    assert "fonts.googleapis.com" in body  # mentioned as a forbidden pattern


def test_skill_description_triggers_on_deck_requests():
    meta, _ = _load()
    desc = meta["description"].lower()
    assert any(
        word in desc
        for word in ("deck", "slide", "ppt", "presentation", "演示", "幻灯")
    )
