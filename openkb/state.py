from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_MIGRATION_META_KEY = "migrated_from_json"


def _hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest (64 chars) of the file at path."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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
        return _hash_file(path)


class DbRegistry:
    """SQLite-backed registry mapping file SHA-256 hashes to metadata dicts.

    Provides better scalability, concurrency support, and extensibility
    compared to JSON-backed HashRegistry.
    """

    def __init__(self, path: Path, migrate_from: Path | None = None) -> None:
        """Initialize DbRegistry.

        Args:
            path: Path to SQLite database file.
            migrate_from: Optional path to JSON file to migrate from.
                          Migration is retried if not previously completed
                          and marked complete atomically.
        """
        self._path = path
        self._init_db()
        if migrate_from is not None and migrate_from.exists():
            self._migrate_from_json_if_needed(migrate_from)

    def _migrate_from_json_if_needed(self, json_path: Path) -> None:
        """Migrate data from JSON file to SQLite database."""
        if not json_path.exists():
            return

        with json_path.open("r", encoding="utf-8") as fh:
            data: dict[str, dict] = json.load(fh)

        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT value FROM schema_meta WHERE key = ?",
                (_MIGRATION_META_KEY,),
            )
            if cursor.fetchone() is not None:
                return

            for file_hash, metadata in data.items():
                metadata_json = json.dumps(metadata, ensure_ascii=False)
                conn.execute("""
                    INSERT OR REPLACE INTO registry (file_hash, metadata_json)
                    VALUES (?, ?)
                """, (file_hash, metadata_json))
            conn.execute("""
                INSERT OR REPLACE INTO schema_meta (key, value)
                VALUES (?, '1')
            """, (_MIGRATION_META_KEY,))

    def _init_db(self) -> None:
        """Initialize database schema if not exists."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS registry (
                    file_hash TEXT PRIMARY KEY,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at ON registry(created_at)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self._path))
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def is_known(self, file_hash: str) -> bool:
        """Return True if file_hash is already registered."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM registry WHERE file_hash = ?",
                (file_hash,)
            )
            return cursor.fetchone() is not None

    def get(self, file_hash: str) -> dict | None:
        """Return metadata for file_hash, or None if not found."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT metadata_json FROM registry WHERE file_hash = ?",
                (file_hash,)
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    def all_entries(self) -> dict[str, dict]:
        """Return a shallow copy of all hash -> metadata entries."""
        with self._connect() as conn:
            cursor = conn.execute(
                "SELECT file_hash, metadata_json FROM registry"
            )
            return {
                row[0]: json.loads(row[1])
                for row in cursor.fetchall()
            }

    def add(self, file_hash: str, metadata: dict) -> None:
        """Register file_hash with metadata and persist to disk.

        If file_hash already exists, updates the metadata.
        """
        metadata_json = json.dumps(metadata, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO registry (file_hash, metadata_json, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(file_hash) DO UPDATE SET
                    metadata_json = excluded.metadata_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (file_hash, metadata_json))

    def get_by_path(self, path: str) -> dict | None:
        """Return metadata registered for a source, raw, or wiki path."""
        with self._connect() as conn:
            rows = conn.execute("SELECT metadata_json FROM registry").fetchall()
            for (metadata_json,) in rows:
                metadata = json.loads(metadata_json)
                if path in {
                    metadata.get("path"),
                    metadata.get("raw_path"),
                    metadata.get("source_path"),
                }:
                    return metadata
        return None

    def remove_by_doc_name(self, doc_name: str) -> None:
        """Remove older content-hash entries for the same document."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT file_hash, metadata_json FROM registry"
            ).fetchall()
            for file_hash, metadata_json in rows:
                metadata = json.loads(metadata_json)
                if metadata.get("doc_name") == doc_name:
                    conn.execute(
                        "DELETE FROM registry WHERE file_hash = ?",
                        (file_hash,),
                    )

    def remove_by_hash(self, file_hash: str) -> bool:
        """Remove the entry keyed by ``file_hash``. Returns True if removed."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM registry WHERE file_hash = ?",
                (file_hash,),
            )
            return cursor.rowcount > 0

    @staticmethod
    def hash_file(path: Path) -> str:
        """Return the SHA-256 hex digest (64 chars) of the file at path."""
        return _hash_file(path)


def get_registry(
    openkb_dir: Path,
    backend: str = "sqlite",
) -> HashRegistry | DbRegistry:
    """Factory function to get the appropriate registry implementation.

    Args:
        openkb_dir: Path to .openkb directory.
        backend: Storage backend - "sqlite" or "json".

    Returns:
        HashRegistry for "json" backend, DbRegistry for "sqlite" backend.

    When switching from json to sqlite and a JSON file exists,
    automatically migrates the data.
    """
    if backend not in ("sqlite", "json"):
        raise ValueError(f"Unknown storage_backend: {backend!r}")

    if backend == "json":
        return HashRegistry(openkb_dir / "hashes.json")

    db_path = openkb_dir / "hashes.db"
    json_path = openkb_dir / "hashes.json"

    migrate_from = json_path if json_path.exists() else None
    return DbRegistry(db_path, migrate_from=migrate_from)
