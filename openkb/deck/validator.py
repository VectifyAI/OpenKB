"""Structural validation for a generated deck.

Mirrors ``openkb/skill/validator.py``'s ``ValidationResult`` shape so
callers (``Generator.run``, the CLI's error reporter, the chat slash
command) can format results identically regardless of artifact type.

Errors block declare-success; warnings print but allow the deck to ship.
The file is preserved even on error so the user can inspect the failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

__all__ = ["ALLOWED_DATA_TYPES", "ValidationResult", "validate_deck"]


ALLOWED_DATA_TYPES: frozenset[str] = frozenset(
    {"cover", "chapter", "thesis", "quote", "compare", "data", "closing"}
)

MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MB
MIN_SLIDES_HARD = 5              # error threshold
MIN_SLIDES_SOFT = 8              # warning threshold (count outside [8,15])
MAX_SLIDES_SOFT = 15
MIN_DISTINCT_TYPES = 4
MAX_CONSECUTIVE_SAME_TYPE = 2    # warning if run-length >= 3


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class _DeckParser(HTMLParser):
    """Collects <section class="slide" data-type="..."> blocks and external refs."""

    def __init__(self) -> None:
        super().__init__()
        self.slide_types: list[str] = []   # data-type, in document order
        self.external_links: list[str] = []  # offending href/src values
        self._depth_in_slide = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "section" and "slide" in (a.get("class") or "").split():
            self.slide_types.append((a.get("data-type") or "").strip())
        elif tag == "link":
            href = (a.get("href") or "").strip()
            if href.startswith(("http://", "https://", "//")):
                self.external_links.append(f"<link href={href!r}>")
        elif tag == "script":
            src = (a.get("src") or "").strip()
            if src.startswith(("http://", "https://", "//")):
                self.external_links.append(f"<script src={src!r}>")


def validate_deck(deck_dir: Path) -> ValidationResult:
    """Validate the generated deck at ``deck_dir/index.html``.

    Returns a :class:`ValidationResult` with categorised issues. Never
    raises for structural failures — those become entries in ``errors``.
    Raises only for unforeseen I/O failures the caller would want to see.
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
    parser = _DeckParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:  # html.parser raises HTMLParseError only on strict mode
        result.errors.append(f"index.html failed to parse: {exc}")
        return result

    n = len(parser.slide_types)
    if n < MIN_SLIDES_HARD:
        result.errors.append(
            f"deck has {n} slides; need at least {MIN_SLIDES_HARD} "
            f'<section class="slide" data-type="..."> blocks.'
        )

    type_set = set(parser.slide_types)
    if "cover" not in type_set:
        result.errors.append('missing required slide: data-type="cover".')
    if "closing" not in type_set:
        result.errors.append('missing required slide: data-type="closing".')

    illegal = type_set - ALLOWED_DATA_TYPES - {""}
    if illegal:
        result.errors.append(
            f"unknown data-type value(s): {sorted(illegal)!r}. "
            f"Allowed: {sorted(ALLOWED_DATA_TYPES)!r}."
        )
    blank = sum(1 for t in parser.slide_types if t == "")
    if blank:
        result.errors.append(f"{blank} <section class='slide'> block(s) missing data-type attribute.")

    if parser.external_links:
        result.errors.append(
            "deck is not self-contained: external references found: "
            + ", ".join(parser.external_links[:3])
            + (f", … (+{len(parser.external_links) - 3} more)" if len(parser.external_links) > 3 else "")
        )

    # Warnings — non-blocking.
    if n and (n < MIN_SLIDES_SOFT or n > MAX_SLIDES_SOFT):
        result.warnings.append(
            f"slide count {n} outside recommended range [{MIN_SLIDES_SOFT}, {MAX_SLIDES_SOFT}]."
        )
    distinct = len(type_set - {""})
    if distinct < MIN_DISTINCT_TYPES:
        result.warnings.append(
            f"only {distinct} distinct data-type(s) used; recommend ≥ {MIN_DISTINCT_TYPES} for visual variety."
        )
    # Run-length: warn on any run >= MAX_CONSECUTIVE_SAME_TYPE + 1.
    run = 1
    for prev, cur in zip(parser.slide_types, parser.slide_types[1:]):
        run = run + 1 if cur == prev and cur != "" else 1
        if run > MAX_CONSECUTIVE_SAME_TYPE:
            result.warnings.append(
                f"{run} consecutive slides with data-type={cur!r}; break up runs of "
                f"3+ same type to avoid visual monotony."
            )
            break  # one warning is enough; agent will read it and revise

    return result
