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

    def get_by_path(self, path: str) -> dict | None:
        """Return metadata registered for a source, raw, or wiki path."""
        for metadata in self._data.values():
            if path in {
                metadata.get("path"),
                metadata.get("raw_path"),
                metadata.get("source_path"),
            }:
                return metadata
        return None

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, file_hash: str, metadata: dict) -> None:
        """Register file_hash with metadata and persist to disk."""
        self._data[file_hash] = metadata
        self._persist()

    def remove_by_doc_name(self, doc_name: str) -> None:
        """Remove older content-hash entries for the same document."""
        stale_hashes = [
            file_hash
            for file_hash, metadata in self._data.items()
            if metadata.get("doc_name") == doc_name
        ]
        if not stale_hashes:
            return
        for file_hash in stale_hashes:
            del self._data[file_hash]
        self._persist()

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
