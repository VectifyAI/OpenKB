import json
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


def test_get_by_path_matches_path_raw_path_and_source_path(tmp_path):
    reg = HashRegistry(tmp_path / "hashes.json")
    reg.add("h1", {
        "name": "report.md",
        "doc_name": "report",
        "path": "inputs/report.md",
        "raw_path": "raw/report.md",
        "source_path": "wiki/sources/report.md",
    })
    assert reg.get_by_path("inputs/report.md")["doc_name"] == "report"
    assert reg.get_by_path("raw/report.md")["doc_name"] == "report"
    assert reg.get_by_path("wiki/sources/report.md")["doc_name"] == "report"


def test_get_by_path_miss_returns_none(tmp_path):
    reg = HashRegistry(tmp_path / "hashes.json")
    reg.add("h1", {"name": "a.md", "doc_name": "a", "path": "a.md"})
    assert reg.get_by_path("elsewhere/a.md") is None


def test_get_by_path_legacy_entry_without_path_fields_is_not_matched(tmp_path):
    reg = HashRegistry(tmp_path / "hashes.json")
    reg.add("h1", {"name": "old.md", "doc_name": "old"})
    assert reg.get_by_path("raw/old.md") is None


def test_find_legacy_by_stem_matches_doc_name_entry_without_path(tmp_path):
    reg = HashRegistry(tmp_path / "hashes.json")
    reg.add("h1", {"name": "report.md", "doc_name": "report", "type": "md"})
    hit = reg.find_legacy_by_stem("report")
    assert hit is not None
    file_hash, meta = hit
    assert file_hash == "h1"
    assert meta["doc_name"] == "report"


def test_find_legacy_by_stem_matches_pre_doc_name_entry_by_filename_stem(tmp_path):
    # Entries written before doc_name existed carry only {name, type}.
    reg = HashRegistry(tmp_path / "hashes.json")
    reg.add("h1", {"name": "notes.md", "type": "md"})
    hit = reg.find_legacy_by_stem("notes")
    assert hit is not None
    assert hit[0] == "h1"


def test_find_legacy_by_stem_entry_with_path_is_not_legacy(tmp_path):
    reg = HashRegistry(tmp_path / "hashes.json")
    reg.add("h1", {"name": "report.md", "doc_name": "report",
                   "path": "inputs/report.md"})
    assert reg.find_legacy_by_stem("report") is None


def test_find_legacy_by_stem_miss_returns_none(tmp_path):
    reg = HashRegistry(tmp_path / "hashes.json")
    assert reg.find_legacy_by_stem("anything") is None


def test_find_legacy_by_stem_first_match_wins_on_duplicates(tmp_path):
    # Pre-fix registries can hold two same-named legacy entries (the
    # collision bug); the resolver backfills the first in insertion order.
    reg = HashRegistry(tmp_path / "hashes.json")
    reg.add("h_first", {"name": "report.md", "doc_name": "report", "type": "md"})
    reg.add("h_second", {"name": "report.md", "doc_name": "report", "type": "md"})
    hit = reg.find_legacy_by_stem("report")
    assert hit is not None
    assert hit[0] == "h_first"


def test_find_legacy_by_stem_nfkc_normalizes_both_sides(tmp_path):
    # macOS hands back NFD filenames; registry may hold NFC. Both must match.
    import unicodedata
    reg = HashRegistry(tmp_path / "hashes.json")
    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    reg.add("h1", {"name": f"{nfc}.md", "doc_name": nfc, "type": "md"})
    hit = reg.find_legacy_by_stem(nfd)
    assert hit is not None and hit[0] == "h1"
