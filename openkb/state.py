from __future__ import annotations

import hashlib
import json
from pathlib import Path


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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)

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
