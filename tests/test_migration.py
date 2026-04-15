"""Integration tests for JSON to SQLite migration."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from openkb.state import get_registry, DbRegistry


def test_full_migration_workflow(tmp_path):
    """Test complete migration from JSON to SQLite."""
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    
    # Step 1: Start with JSON backend
    json_registry = get_registry(openkb_dir, backend="json")
    json_registry.add("hash1", {"name": "doc1.pdf", "pages": 10})
    json_registry.add("hash2", {"name": "doc2.pdf", "pages": 20})
    
    # Verify JSON file exists
    json_path = openkb_dir / "hashes.json"
    assert json_path.exists()
    
    # Step 2: Switch to SQLite backend (triggers migration)
    sqlite_registry = get_registry(openkb_dir, backend="sqlite")
    
    # Verify data was migrated
    assert sqlite_registry.is_known("hash1")
    assert sqlite_registry.is_known("hash2")
    assert sqlite_registry.get("hash1") == {"name": "doc1.pdf", "pages": 10}
    assert sqlite_registry.get("hash2") == {"name": "doc2.pdf", "pages": 20}
    
    # Step 3: Add new data via SQLite
    sqlite_registry.add("hash3", {"name": "doc3.pdf", "pages": 30})
    
    # Step 4: Create new SQLite instance - should have all data
    sqlite_registry2 = get_registry(openkb_dir, backend="sqlite")
    assert sqlite_registry2.is_known("hash1")
    assert sqlite_registry2.is_known("hash2")
    assert sqlite_registry2.is_known("hash3")


def test_concurrent_sqlite_access(tmp_path):
    """Test that SQLite handles concurrent access correctly."""
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    
    registry = get_registry(openkb_dir, backend="sqlite")
    errors = []
    
    def add_entries(start: int, count: int) -> None:
        try:
            for i in range(start, start + count):
                registry.add(f"hash{i}", {"index": i})
        except Exception as e:
            errors.append(e)
    
    threads = [
        threading.Thread(target=add_entries, args=(0, 50)),
        threading.Thread(target=add_entries, args=(50, 50)),
        threading.Thread(target=add_entries, args=(100, 50)),
    ]
    
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    
    assert not errors
    entries = registry.all_entries()
    assert len(entries) == 150
