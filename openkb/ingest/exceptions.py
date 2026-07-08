"""Exceptions used to control ingest pipeline routing."""

from __future__ import annotations

from pathlib import Path


class LongDocumentFallback(Exception):
    """Signal that the target should be routed to the legacy long-doc path."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(reason)
        self.path = path
        self.reason = reason
