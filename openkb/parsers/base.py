from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParseResult:
    """Normalized output of a parser.

    ``markdown`` references images either as bare filenames present in
    ``images`` or as inline base64 data URIs. ``images`` maps a filename to
    its raw bytes; the caller persists them and rewrites links via
    :func:`openkb.images.localize_images`.
    """

    markdown: str
    images: dict[str, bytes] = field(default_factory=dict)


class Parser(ABC):
    """Converts a source document to Markdown."""

    name: str

    @abstractmethod
    def supports(self, suffix: str) -> bool:
        """Return True if this parser handles files with ``suffix`` (e.g. ``.pdf``)."""

    @abstractmethod
    def parse(self, src: Path) -> ParseResult:
        """Parse ``src`` and return a :class:`ParseResult`."""
