import pytest
import json
from pathlib import Path
from openkb.state import HashRegistry


def test_empty_registry_is_known_false(tmp_path):
    registry = HashRegistry(tmp_path / "hashes.json")
    assert registry.is_known("abc123") is False


def test_empty_registry_get_returns_none(tmp_path):
    registry = HashRegistry(tmp_path / "hashes.json")
    assert registry.get("abc123") is None


def test_add_and_is_known(tmp_path):
    registry = HashRegistry(tmp_path / "hashes.json")
    registry.add("deadbeef", {"filename": "test.pdf"})
    assert registry.is_known("deadbeef") is True


def test_add_and_get(tmp_path):
    registry = HashRegistry(tmp_path / "hashes.json")
    metadata = {"filename": "doc.pdf", "pages": 10}
    registry.add("cafebabe", metadata)
    assert registry.get("cafebabe") == metadata


def test_persistence_across_instances(tmp_path):
    path = tmp_path / "hashes.json"
    r1 = HashRegistry(path)
    r1.add("hash1", {"file": "a.pdf"})

    r2 = HashRegistry(path)
    assert r2.is_known("hash1") is True
    assert r2.get("hash1") == {"file": "a.pdf"}


def test_all_entries_returns_all(tmp_path):
    registry = HashRegistry(tmp_path / "hashes.json")
    registry.add("h1", {"name": "one"})
    registry.add("h2", {"name": "two"})
    entries = registry.all_entries()
    assert "h1" in entries
    assert "h2" in entries
    assert entries["h1"] == {"name": "one"}
    assert entries["h2"] == {"name": "two"}


def test_all_entries_empty(tmp_path):
    registry = HashRegistry(tmp_path / "hashes.json")
    assert registry.all_entries() == {}


def test_hash_file_produces_64_char_hex(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("hello world")
    digest = HashRegistry.hash_file(f)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_file_deterministic(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("deterministic content")
    assert HashRegistry.hash_file(f) == HashRegistry.hash_file(f)


def test_hash_file_different_content(tmp_path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("content A")
    f2.write_text("content B")
    assert HashRegistry.hash_file(f1) != HashRegistry.hash_file(f2)


def test_load_existing_json(tmp_path):
    path = tmp_path / "hashes.json"
    data = {"existinghash": {"file": "pre.pdf"}}
    path.write_text(json.dumps(data))
    registry = HashRegistry(path)
    assert registry.is_known("existinghash") is True
    assert registry.get("existinghash") == {"file": "pre.pdf"}
