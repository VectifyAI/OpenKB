from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path

from openkb.locks import atomic_write_json


class HashRegistry:
    """Persistent registry mapping file SHA-256 hashes to metadata dicts."""

    def __init__(self, path: Path) -> None:
        self._path = path
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                self._data: dict[str, dict] = json.load(fh)
        else:
            self._data = {}

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_known(self, file_hash: str) -> bool:
        """Return True if file_hash is already registered."""
        return file_hash in self._data

    def get(self, file_hash: str) -> dict | None:
        """Return metadata for file_hash, or None if not found."""
        return self._data.get(file_hash)

    def all_entries(self) -> dict[str, dict]:
        """Return a shallow copy of all hash -> metadata entries."""
        return dict(self._data)

    def get_by_path(self, path: str) -> dict | None:
        """Return metadata whose path/raw_path/source_path equals ``path``.

        ``path`` is a registry path string (posix, relative to the KB dir
        when inside it) as produced by ``converter._registry_path``. Entries
        written before the path index existed carry none of these fields and
        never match.
        """
        for metadata in self._data.values():
            if path in (
                metadata.get("path"),
                metadata.get("raw_path"),
                metadata.get("source_path"),
            ):
                return metadata
        return None

    def find_legacy_by_stem(self, stem: str) -> tuple[str, dict] | None:
        """Find a pre-path-index entry matching ``stem``.

        Returns ``(file_hash, metadata)`` for an entry that has no truthy
        ``path`` (a missing or empty ``path`` is treated as unindexed) and
        whose ``doc_name`` — or, for even older entries lacking
        ``doc_name``, the stem of its ``name`` — equals ``stem``. When
        several legacy entries match (pre-fix registries can hold
        same-named entries), the first in insertion order is returned.
        Callers use the hash to backfill ``path`` via :meth:`add`. Returns
        None when every matching name is already path-indexed. The
        comparison is NFKC-normalized on both sides, so macOS NFD
        filenames match their NFC registry entries.
        """
        for file_hash, metadata in self._data.items():
            if metadata.get("path"):
                continue
            entry_name = metadata.get("doc_name") or Path(metadata.get("name", "")).stem
            if unicodedata.normalize("NFKC", entry_name) == unicodedata.normalize("NFKC", stem):
                return file_hash, metadata
        return None

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, file_hash: str, metadata: dict) -> None:
        """Register file_hash with metadata and persist to disk."""
        self._data[file_hash] = metadata
        self._persist()

    def remove_by_doc_name(self, doc_name: str) -> bool:
        """Remove the entry whose metadata['doc_name'] matches. Returns True if removed."""
        for file_hash, meta in list(self._data.items()):
            if meta.get("doc_name") == doc_name:
                del self._data[file_hash]
                self._persist()
                return True
        return False

    def remove_by_hash(self, file_hash: str) -> bool:
        """Remove the entry keyed by ``file_hash``. Returns True if removed.

        Preferred over :meth:`remove_by_doc_name` when the caller already
        has the hash in hand — works regardless of whether the entry's
        metadata carries a ``doc_name`` field (legacy entries written
        before commit c504e26 do not).
        """
        if file_hash not in self._data:
            return False
        del self._data[file_hash]
        self._persist()
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        atomic_write_json(self._path, self._data)

    # ------------------------------------------------------------------
    # Static utility
    # ------------------------------------------------------------------

    @staticmethod
    def hash_file(path: Path) -> str:
        """Return the SHA-256 hex digest (64 chars) of the file at path."""
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
