"""Tests for DbRegistry SQLite-backed storage."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from openkb.state import DbRegistry


def test_db_registry_creates_database_file(tmp_path):
    """DbRegistry should create a .db file on init."""
    db_path = tmp_path / "hashes.db"
    registry = DbRegistry(db_path)
    assert db_path.exists()


def test_db_registry_creates_table(tmp_path):
    """DbRegistry should create the registry table."""
    db_path = tmp_path / "hashes.db"
    registry = DbRegistry(db_path)
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='registry'"
    )
    result = cursor.fetchone()
    conn.close()
    assert result is not None


def test_db_empty_registry_is_known_false(tmp_path):
    """Empty DbRegistry should return False for is_known."""
    registry = DbRegistry(tmp_path / "hashes.db")
    assert registry.is_known("abc123") is False


def test_db_empty_registry_get_returns_none(tmp_path):
    """Empty DbRegistry should return None for get."""
    registry = DbRegistry(tmp_path / "hashes.db")
    assert registry.get("abc123") is None


def test_db_add_and_is_known(tmp_path):
    """After add, is_known should return True."""
    registry = DbRegistry(tmp_path / "hashes.db")
    registry.add("deadbeef", {"filename": "test.pdf"})
    assert registry.is_known("deadbeef") is True


def test_db_add_and_get(tmp_path):
    """After add, get should return the metadata."""
    registry = DbRegistry(tmp_path / "hashes.db")
    metadata = {"filename": "doc.pdf", "pages": 10}
    registry.add("cafebabe", metadata)
    assert registry.get("cafebabe") == metadata


def test_db_persistence_across_instances(tmp_path):
    """Data should persist across DbRegistry instances."""
    db_path = tmp_path / "hashes.db"
    r1 = DbRegistry(db_path)
    r1.add("hash1", {"file": "a.pdf"})
    
    r2 = DbRegistry(db_path)
    assert r2.is_known("hash1") is True
    assert r2.get("hash1") == {"file": "a.pdf"}


def test_db_all_entries_returns_all(tmp_path):
    """all_entries should return all hash -> metadata mappings."""
    registry = DbRegistry(tmp_path / "hashes.db")
    registry.add("h1", {"name": "one"})
    registry.add("h2", {"name": "two"})
    entries = registry.all_entries()
    assert "h1" in entries
    assert "h2" in entries
    assert entries["h1"] == {"name": "one"}
    assert entries["h2"] == {"name": "two"}


def test_db_all_entries_empty(tmp_path):
    """all_entries on empty registry should return empty dict."""
    registry = DbRegistry(tmp_path / "hashes.db")
    assert registry.all_entries() == {}


def test_db_hash_file_unchanged(tmp_path):
    """DbRegistry.hash_file should work same as HashRegistry."""
    f = tmp_path / "sample.txt"
    f.write_text("hello world")
    digest = DbRegistry.hash_file(f)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_db_update_existing_hash(tmp_path):
    """Adding same hash twice should update metadata."""
    registry = DbRegistry(tmp_path / "hashes.db")
    registry.add("hash1", {"version": 1})
    registry.add("hash1", {"version": 2})
    assert registry.get("hash1") == {"version": 2}


def test_db_metadata_with_nested_dict(tmp_path):
    """Metadata can contain nested dictionaries."""
    registry = DbRegistry(tmp_path / "hashes.db")
    metadata = {
        "name": "doc.pdf",
        "stats": {"pages": 10, "words": 5000},
    }
    registry.add("hash1", metadata)
    assert registry.get("hash1") == metadata


def test_db_wal_mode_enabled(tmp_path):
    """Database should use WAL mode for concurrency."""
    db_path = tmp_path / "hashes.db"
    DbRegistry(db_path)
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("PRAGMA journal_mode")
    result = cursor.fetchone()
    conn.close()
    assert result[0].lower() == "wal"


def test_migrate_from_json(tmp_path):
    """DbRegistry should migrate existing JSON data on first access."""
    json_path = tmp_path / "hashes.json"
    existing_data = {
        "hash1": {"name": "doc1.pdf", "pages": 10},
        "hash2": {"name": "doc2.pdf", "pages": 20},
    }
    json_path.write_text(json.dumps(existing_data), encoding="utf-8")
    
    db_path = tmp_path / "hashes.db"
    registry = DbRegistry(db_path, migrate_from=json_path)
    
    assert registry.is_known("hash1")
    assert registry.is_known("hash2")
    assert registry.get("hash1") == {"name": "doc1.pdf", "pages": 10}
    assert registry.get("hash2") == {"name": "doc2.pdf", "pages": 20}


def test_migrate_only_once(tmp_path):
    """Migration should only happen once, not on subsequent loads."""
    json_path = tmp_path / "hashes.json"
    existing_data = {"hash1": {"name": "doc1.pdf"}}
    json_path.write_text(json.dumps(existing_data), encoding="utf-8")
    
    db_path = tmp_path / "hashes.db"
    
    r1 = DbRegistry(db_path, migrate_from=json_path)
    assert r1.is_known("hash1")
    
    existing_data["hash2"] = {"name": "doc2.pdf"}
    json_path.write_text(json.dumps(existing_data), encoding="utf-8")
    
    r2 = DbRegistry(db_path, migrate_from=json_path)
    assert r2.is_known("hash1")
    assert not r2.is_known("hash2")


def test_migrate_optional(tmp_path):
    """DbRegistry should work without migration."""
    db_path = tmp_path / "hashes.db"
    registry = DbRegistry(db_path)
    registry.add("hash1", {"name": "doc.pdf"})
    assert registry.is_known("hash1")
