"""Sanity tests for the deck_create prompt. Verifies file is loadable and
contains the structural anchors the validator and creator depend on."""
from __future__ import annotations

from openkb.prompts import load_prompt


def test_prompt_loads():
    text = load_prompt("deck_create")
    assert isinstance(text, str) and len(text) > 1000


def test_prompt_has_placeholders():
    text = load_prompt("deck_create")
    assert "{intent}" in text
    assert "{wiki_schema}" in text
    assert "{deck_name}" in text


def test_prompt_lists_all_allowed_data_types():
    text = load_prompt("deck_create")
    for t in ("cover", "chapter", "thesis", "quote", "compare", "data", "closing"):
        assert t in text, f"slide grammar must mention data-type={t}"


def test_prompt_lists_editorial_monocle_tokens():
    text = load_prompt("deck_create")
    # palette values must appear so the agent can copy them verbatim
    for hex_value in ("#f3eee1", "#1a1612", "#a4341c", "#fff3a8"):
        assert hex_value in text, f"palette token {hex_value} missing"
    assert "Charter" in text  # serif stack
    assert "16:9" in text or "aspect-ratio" in text
