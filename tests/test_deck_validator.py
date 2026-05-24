"""Tests for the deck validator. No LLM; pure structural checks.

Post-refactor the validator runs in two modes:

* **generic** (``grammar=None``) — only skill-agnostic invariants
  (file exists, parses, ≥5 ``<section class="slide">`` blocks,
  self-contained). Used when a skill doesn't declare its slide grammar.

* **grammar-aware** — skill passes its frontmatter
  ``od.deck_grammar`` (``EDITORIAL_MONOCLE_GRAMMAR`` here). Adds
  required/allowed-kind checks.

Each test below explicitly picks a mode so the contract for both
surfaces is pinned.
"""
from __future__ import annotations

from pathlib import Path

from openkb.deck.validator import (
    ALLOWED_DATA_TYPES,
    EDITORIAL_MONOCLE_GRAMMAR,
    ValidationResult,
    validate_deck,
)


# A minimal well-formed deck: 8 slides covering all required invariants.
GOOD_DECK = """<!doctype html>
<html><head><meta charset="utf-8"><style>.slide{aspect-ratio:16/9}</style></head>
<body>
  <section class="slide" data-type="cover"><h1>Title</h1></section>
  <section class="slide" data-type="thesis"><h2>Claim A</h2><p>explanation</p></section>
  <section class="slide" data-type="quote"><blockquote>"…"</blockquote></section>
  <section class="slide" data-type="data"><div>8.2x</div></section>
  <section class="slide" data-type="thesis"><h2>Claim B</h2><p>explanation</p></section>
  <section class="slide" data-type="compare"><div>L</div><div>R</div></section>
  <section class="slide" data-type="chapter"><div>03</div></section>
  <section class="slide" data-type="closing"><h1>Thanks</h1></section>
</body></html>"""


def _write(tmp_path: Path, html: str) -> Path:
    deck_dir = tmp_path / "deck"
    deck_dir.mkdir()
    (deck_dir / "index.html").write_text(html, encoding="utf-8")
    return deck_dir


def test_good_deck_passes(tmp_path: Path):
    result = validate_deck(_write(tmp_path, GOOD_DECK))
    assert isinstance(result, ValidationResult)
    assert result.errors == []
    # 8 slides is on the inside of [8,15] so no slide-count warning
    assert not any("slide count" in w.lower() for w in result.warnings)


def test_allowed_data_types_constant():
    # Lock the public contract; prompt + validator must stay in sync.
    assert ALLOWED_DATA_TYPES == frozenset(
        {"cover", "chapter", "thesis", "quote", "compare", "data", "closing"}
    )


def test_missing_file(tmp_path: Path):
    deck_dir = tmp_path / "deck"
    deck_dir.mkdir()
    result = validate_deck(deck_dir)
    assert any("not found" in e for e in result.errors)


def test_too_few_slides(tmp_path: Path):
    html = (
        "<html><body>"
        + "".join(f'<section class="slide" data-type="thesis"><h2>{i}</h2></section>' for i in range(4))
        + "</body></html>"
    )
    result = validate_deck(_write(tmp_path, html))
    assert any("at least 5" in e for e in result.errors)


def test_missing_cover(tmp_path: Path):
    """Grammar-aware mode: missing required "cover" is an error."""
    html = GOOD_DECK.replace('data-type="cover"', 'data-type="thesis"')
    result = validate_deck(_write(tmp_path, html), grammar=EDITORIAL_MONOCLE_GRAMMAR)
    assert any('"cover"' in e for e in result.errors)


def test_missing_cover_ignored_in_generic_mode(tmp_path: Path):
    """Generic mode: missing-cover is NOT an error — third-party deck
    skills aren't required to use the Editorial Monocle data-type vocabulary."""
    html = GOOD_DECK.replace('data-type="cover"', 'data-type="thesis"')
    result = validate_deck(_write(tmp_path, html))
    assert not any('"cover"' in e for e in result.errors)


def test_missing_closing(tmp_path: Path):
    html = GOOD_DECK.replace('data-type="closing"', 'data-type="thesis"')
    result = validate_deck(_write(tmp_path, html), grammar=EDITORIAL_MONOCLE_GRAMMAR)
    assert any('"closing"' in e for e in result.errors)


def test_unknown_data_type(tmp_path: Path):
    html = GOOD_DECK.replace('data-type="quote"', 'data-type="hero-banner"')
    result = validate_deck(_write(tmp_path, html), grammar=EDITORIAL_MONOCLE_GRAMMAR)
    assert any("hero-banner" in e for e in result.errors)


def test_missing_data_type_attr(tmp_path: Path):
    html = GOOD_DECK.replace('data-type="quote"', '')
    result = validate_deck(_write(tmp_path, html), grammar=EDITORIAL_MONOCLE_GRAMMAR)
    assert any("missing data-type" in e for e in result.errors)


def test_guizang_shape_passes_generic_mode(tmp_path: Path):
    """A third-party deck (no data-type, uses CSS class for kind) must
    pass the default (skill-agnostic) validator. Confirms #2 in the
    architectural-review fix list."""
    guizang_shape = (
        "<html><body>"
        '<section class="slide hero active"><h1>Cover</h1></section>'
        '<section class="slide act inverse"><h1>Act 1</h1></section>'
        '<section class="slide grid6"><div>Numbers</div></section>'
        '<section class="slide two-col"><div>L</div><div>R</div></section>'
        '<section class="slide q inverse"><h2>?</h2></section>'
        '<section class="slide hero inverse"><h1>Close</h1></section>'
        "</body></html>"
    )
    result = validate_deck(_write(tmp_path, guizang_shape))
    assert result.errors == [], f"unexpected errors: {result.errors}"


def test_external_link_blocks(tmp_path: Path):
    html = GOOD_DECK.replace(
        "</head>",
        '<link rel="stylesheet" href="https://cdn.example/x.css"></head>',
    )
    result = validate_deck(_write(tmp_path, html))
    assert any("not self-contained" in e for e in result.errors)


def test_external_script_blocks(tmp_path: Path):
    html = GOOD_DECK.replace(
        "</head>",
        '<script src="https://cdn.example/x.js"></script></head>',
    )
    result = validate_deck(_write(tmp_path, html))
    assert any("not self-contained" in e for e in result.errors)


def test_external_img_blocks(tmp_path: Path):
    html = GOOD_DECK.replace(
        '<section class="slide" data-type="quote"><blockquote>"…"</blockquote></section>',
        '<section class="slide" data-type="quote"><img src="https://example.com/x.png"></section>',
    )
    result = validate_deck(_write(tmp_path, html))
    assert any("not self-contained" in e for e in result.errors)


def test_few_slides_warning(tmp_path: Path):
    # 6 slides — passes hard floor (5), but warns (< 8).
    html = (
        "<html><body>"
        '<section class="slide" data-type="cover"></section>'
        '<section class="slide" data-type="thesis"></section>'
        '<section class="slide" data-type="data"></section>'
        '<section class="slide" data-type="thesis"></section>'
        '<section class="slide" data-type="compare"></section>'
        '<section class="slide" data-type="closing"></section>'
        "</body></html>"
    )
    result = validate_deck(_write(tmp_path, html))
    assert result.errors == []
    assert any("slide count 6" in w for w in result.warnings)


def test_run_of_3_same_type_warning(tmp_path: Path):
    html = (
        "<html><body>"
        '<section class="slide" data-type="cover"></section>'
        '<section class="slide" data-type="thesis"></section>'
        '<section class="slide" data-type="thesis"></section>'
        '<section class="slide" data-type="thesis"></section>'
        '<section class="slide" data-type="data"></section>'
        '<section class="slide" data-type="compare"></section>'
        '<section class="slide" data-type="quote"></section>'
        '<section class="slide" data-type="closing"></section>'
        "</body></html>"
    )
    result = validate_deck(_write(tmp_path, html), grammar=EDITORIAL_MONOCLE_GRAMMAR)
    assert result.errors == []
    assert any("consecutive" in w for w in result.warnings)


def test_low_distinct_types_warning(tmp_path: Path):
    # 8 slides but only 3 distinct types (cover, thesis, closing).
    html = (
        "<html><body>"
        '<section class="slide" data-type="cover"></section>'
        + '<section class="slide" data-type="thesis"></section>' * 6
        + '<section class="slide" data-type="closing"></section>'
        "</body></html>"
    )
    result = validate_deck(_write(tmp_path, html), grammar=EDITORIAL_MONOCLE_GRAMMAR)
    # Errors fine; this run-length and distinct-count will both warn.
    assert any("distinct data-type" in w for w in result.warnings)


def test_no_slides_no_distinct_warning(tmp_path: Path):
    # A deck with zero slides already produces hard errors; the distinct-type
    # warning ("only 0 distinct…") is noise on an empty deck and is suppressed.
    html = "<html><body></body></html>"
    result = validate_deck(_write(tmp_path, html), grammar=EDITORIAL_MONOCLE_GRAMMAR)
    assert not any("distinct data-type" in w for w in result.warnings)


def test_oversize_file_warning(tmp_path: Path, monkeypatch):
    # Lower the size threshold so we don't actually allocate 2MB to
    # trigger the warning branch.
    monkeypatch.setattr(
        "openkb.deck.validator.MAX_FILE_BYTES",
        100,  # threshold 100 bytes for the test
    )
    result = validate_deck(_write(tmp_path, GOOD_DECK))
    assert any("MB" in w for w in result.warnings)
