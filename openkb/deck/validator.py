"""Structural validation for a generated deck.

Default mode (``grammar=None``) only enforces SKILL-AGNOSTIC invariants:
file exists, parses as HTML, ≥ 5 ``<section class="slide">`` blocks,
self-contained (no external link/script/img references), reasonable size.

A skill that wants stricter checks can declare its slide grammar in the
SKILL.md frontmatter under ``od.deck_grammar`` and pass it as the
``grammar`` argument. The Editorial Monocle skill does this — its
grammar names the 7 ``data-type`` values plus the cover/closing
requirement; guizang / swiss / any community skill simply omit it and
get the generic validation.

Mirrors ``openkb/skill/validator.py``'s ``ValidationResult`` shape so
callers can format issues identically regardless of artifact type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional, TypedDict

__all__ = [
    "ALLOWED_DATA_TYPES",
    "DeckGrammar",
    "EDITORIAL_MONOCLE_GRAMMAR",
    "ValidationResult",
    "validate_deck",
]


class DeckGrammar(TypedDict, total=False):
    """Skill-declared rules for slide classification.

    All keys are optional. If a key is missing, the corresponding check
    is skipped. This is what a skill writer puts under
    ``frontmatter.od.deck_grammar`` to opt into structural validation.

    Example (Editorial Monocle skill)::

        od:
          mode: deck
          deck_grammar:
            kind_attr: data-type
            required: [cover, closing]
            allowed: [cover, chapter, thesis, quote, compare, data, closing]
            min_distinct: 4
            max_consecutive_same: 2
    """
    kind_attr: str             # attribute name carrying the slide kind (e.g. "data-type")
    required: list[str]        # kinds that MUST appear at least once
    allowed: list[str]         # whitelist; anything else is rejected
    min_distinct: int          # warn if fewer distinct kinds present
    max_consecutive_same: int  # warn if run-length exceeds this


# Editorial Monocle's published grammar. Kept here for the openkb-deck-editorial
# skill to import (and for tests / docs to reference); third-party skills may
# define their own or omit grammar entirely.
EDITORIAL_MONOCLE_GRAMMAR: DeckGrammar = {
    "kind_attr": "data-type",
    "required": ["cover", "closing"],
    "allowed": ["cover", "chapter", "thesis", "quote", "compare", "data", "closing"],
    "min_distinct": 4,
    "max_consecutive_same": 2,
}

# Legacy alias used by tests that pinned the old name. New code should
# read this from EDITORIAL_MONOCLE_GRAMMAR["allowed"].
ALLOWED_DATA_TYPES: frozenset[str] = frozenset(EDITORIAL_MONOCLE_GRAMMAR["allowed"])

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
MIN_SLIDES_HARD = 5              # error threshold (skill-agnostic)
MIN_SLIDES_SOFT = 8              # warning threshold (count outside [8,15])
MAX_SLIDES_SOFT = 15


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class _DeckParser(HTMLParser):
    """Collects ``<section class="slide">`` blocks and any external refs.

    The slide kind (e.g. ``data-type="cover"`` for Editorial Monocle) is
    extracted lazily — ``slide_kinds`` is keyed by the configured
    ``kind_attr``; an empty string means the slide didn't declare a kind
    under that attr.
    """

    def __init__(self, kind_attr: Optional[str] = None) -> None:
        super().__init__()
        self.kind_attr = kind_attr
        self.slide_kinds: list[str] = []
        self.external_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "section" and "slide" in (a.get("class") or "").split():
            if self.kind_attr is None:
                # Skill-agnostic: just count slides; kind is irrelevant.
                self.slide_kinds.append("")
            else:
                self.slide_kinds.append((a.get(self.kind_attr) or "").strip())
        elif tag == "link":
            href = (a.get("href") or "").strip()
            if href.startswith(("http://", "https://", "//")):
                self.external_links.append(f"<link href={href!r}>")
        elif tag == "script":
            src = (a.get("src") or "").strip()
            if src.startswith(("http://", "https://", "//")):
                self.external_links.append(f"<script src={src!r}>")
        elif tag == "img":
            src = (a.get("src") or "").strip()
            if src.startswith(("http://", "https://", "//")):
                self.external_links.append(f"<img src={src!r}>")


def validate_deck(
    deck_dir: Path,
    grammar: Optional[DeckGrammar] = None,
) -> ValidationResult:
    """Validate the generated deck at ``deck_dir/index.html``.

    Args:
        deck_dir: Directory containing ``index.html``.
        grammar: Optional skill-declared grammar (typically read from the
            skill's frontmatter under ``od.deck_grammar``). When ``None``,
            only skill-agnostic invariants are checked (file present,
            parses, ≥5 slides, self-contained). When provided, also
            enforces required/allowed slide kinds.

    Returns a :class:`ValidationResult` with categorised issues. Never
    raises for structural failures — those become entries in ``errors``.
    """
    result = ValidationResult()
    index = deck_dir / "index.html"

    if not index.is_file():
        result.errors.append(f"index.html not found at {index}")
        return result

    size = index.stat().st_size
    if size > MAX_FILE_BYTES:
        result.warnings.append(
            f"index.html is {size / 1024 / 1024:.1f} MB (> {MAX_FILE_BYTES // 1024 // 1024} MB) — "
            f"likely too many inlined images."
        )

    text = index.read_text(encoding="utf-8", errors="replace")
    kind_attr = grammar.get("kind_attr") if grammar else None
    parser = _DeckParser(kind_attr=kind_attr)
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        result.errors.append(f"index.html failed to parse: {exc}")
        return result

    n = len(parser.slide_kinds)

    # ─── Skill-agnostic checks (always run) ──────────────────────────────────
    if n < MIN_SLIDES_HARD:
        result.errors.append(
            f"deck has {n} slides; need at least {MIN_SLIDES_HARD} "
            f'<section class="slide"> blocks.'
        )

    if parser.external_links:
        result.errors.append(
            "deck is not self-contained: external references found: "
            + ", ".join(parser.external_links[:3])
            + (f", … (+{len(parser.external_links) - 3} more)" if len(parser.external_links) > 3 else "")
        )

    if n and (n < MIN_SLIDES_SOFT or n > MAX_SLIDES_SOFT):
        result.warnings.append(
            f"slide count {n} outside recommended range [{MIN_SLIDES_SOFT}, {MAX_SLIDES_SOFT}]."
        )

    # ─── Grammar-aware checks (opt-in via skill frontmatter) ─────────────────
    if grammar is not None:
        _apply_grammar_checks(parser.slide_kinds, grammar, result)

    return result


def _apply_grammar_checks(
    slide_kinds: list[str],
    grammar: DeckGrammar,
    result: ValidationResult,
) -> None:
    """Enforce skill-declared slide grammar against parsed kinds."""
    kind_attr = grammar.get("kind_attr", "data-type")
    type_set = set(slide_kinds)

    for required in grammar.get("required", []):
        if required not in type_set:
            result.errors.append(
                f'missing required slide: {kind_attr}="{required}".'
            )

    allowed = grammar.get("allowed")
    if allowed:
        allowed_set = set(allowed)
        illegal = type_set - allowed_set - {""}
        if illegal:
            result.errors.append(
                f"unknown {kind_attr} value(s): {sorted(illegal)!r}. "
                f"Allowed: {sorted(allowed)!r}."
            )

    blank = sum(1 for t in slide_kinds if t == "")
    if blank:
        result.errors.append(
            f"{blank} <section class='slide'> block(s) missing {kind_attr} attribute."
        )

    distinct = len(type_set - {""})
    min_distinct = grammar.get("min_distinct")
    if min_distinct is not None and slide_kinds and distinct < min_distinct:
        result.warnings.append(
            f"only {distinct} distinct {kind_attr} value(s) used; "
            f"recommend ≥ {min_distinct} for visual variety."
        )

    max_run = grammar.get("max_consecutive_same")
    if max_run is not None:
        run = 1
        for prev, cur in zip(slide_kinds, slide_kinds[1:]):
            run = run + 1 if cur == prev and cur != "" else 1
            if run > max_run:
                result.warnings.append(
                    f"{run} consecutive slides with {kind_attr}={cur!r}; "
                    f"break up runs of {max_run + 1}+ same type to avoid visual monotony."
                )
                break
