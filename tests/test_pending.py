"""Tests for openkb.pending.PendingTopicsStore (see issue #247)."""

from __future__ import annotations

import pytest

from openkb.pending import MAX_NOTES_BEFORE_PROMOTION, PendingTopicsStore


def test_new_store_has_no_pending_entries(tmp_path):
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    assert store.get("concepts", "attention") is None
    assert store.note_count("concepts", "attention") == 0
    assert store.brief_lines("concepts") == []


def test_add_note_creates_entry_and_returns_count(tmp_path):
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    count = store.add_note(
        "concepts",
        "attention",
        "A mechanism for weighting input relevance.",
        "doc-1",
        "summaries/doc-1.md",
        "first note",
    )
    assert count == 1
    entry = store.get("concepts", "attention")
    assert entry["description"] == "A mechanism for weighting input relevance."
    assert entry["notes"] == [
        {
            "doc_name": "doc-1",
            "source_file": "summaries/doc-1.md",
            "note": "first note",
            "date": entry["notes"][0]["date"],
        }
    ]


def test_add_note_accumulates_in_order(tmp_path):
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    store.add_note("concepts", "attention", "desc 1", "doc-1", "summaries/doc-1.md", "note 1")
    count = store.add_note(
        "concepts", "attention", "desc 2", "doc-2", "summaries/doc-2.md", "note 2"
    )
    assert count == 2
    notes = store.get("concepts", "attention")["notes"]
    assert [n["note"] for n in notes] == ["note 1", "note 2"]


def test_add_note_overwrites_description_not_accumulates(tmp_path):
    """The 2nd (or later) note's description replaces the previous one — it's
    a single cumulative summary field, not a list of past descriptions."""
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    store.add_note(
        "concepts", "attention", "desc from note 1", "doc-1", "summaries/doc-1.md", "note 1"
    )
    store.add_note(
        "concepts", "attention", "desc from note 2", "doc-2", "summaries/doc-2.md", "note 2"
    )
    entry = store.get("concepts", "attention")
    assert entry["description"] == "desc from note 2"


def test_promotion_threshold_is_third_note(tmp_path):
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    for i in range(MAX_NOTES_BEFORE_PROMOTION):
        store.add_note(
            "concepts", "attention", f"desc {i}", f"doc-{i}", f"summaries/doc-{i}.md", f"note {i}"
        )
    # Not yet promote-eligible after MAX_NOTES_BEFORE_PROMOTION notes.
    assert store.note_count("concepts", "attention") == MAX_NOTES_BEFORE_PROMOTION
    count = store.add_note(
        "concepts", "attention", "final desc", "doc-final", "summaries/doc-final.md", "final note"
    )
    assert count == MAX_NOTES_BEFORE_PROMOTION + 1


def test_remove_clears_entry(tmp_path):
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    store.add_note("concepts", "attention", "desc", "doc-1", "summaries/doc-1.md", "note 1")
    store.remove("concepts", "attention")
    assert store.get("concepts", "attention") is None
    # Removing an absent entry is a no-op, not an error.
    store.remove("concepts", "attention")


def test_entity_notes_carry_type(tmp_path):
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    store.add_note(
        "entities",
        "nvidia",
        "A semiconductor and AI computing company.",
        "doc-1",
        "summaries/doc-1.md",
        "seen in doc-1",
        type_="organization",
    )
    entry = store.get("entities", "nvidia")
    assert entry["type"] == "organization"


def test_brief_lines_format(tmp_path):
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    store.add_note("concepts", "attention", "desc 1", "doc-1", "summaries/doc-1.md", "note 1")
    store.add_note("concepts", "attention", "desc 2", "doc-2", "summaries/doc-2.md", "note 2")
    lines = store.brief_lines("concepts")
    assert lines == [f"- attention (pending, 2/{MAX_NOTES_BEFORE_PROMOTION + 1} mentions) — desc 2"]


def test_brief_lines_requires_description(tmp_path):
    """A legacy entry that predates the ``description`` field (see the
    one-time migration for issue #247) is a genuine data bug by the time
    ``brief_lines`` runs — it fails loudly with a plain ``KeyError`` rather
    than degrading silently, no dedicated guard/exception class needed."""
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    store.add_note("concepts", "attention", "desc", "doc-1", "summaries/doc-1.md", "note 1")
    del store.get("concepts", "attention")["description"]
    with pytest.raises(KeyError):
        store.brief_lines("concepts")


def test_concepts_and_entities_are_independent_namespaces(tmp_path):
    store = PendingTopicsStore(tmp_path / "pending_topics.json")
    store.add_note(
        "concepts", "shared-name", "concept desc", "doc-1", "summaries/doc-1.md", "concept note"
    )
    store.add_note(
        "entities", "shared-name", "entity desc", "doc-1", "summaries/doc-1.md", "entity note"
    )
    assert store.note_count("concepts", "shared-name") == 1
    assert store.note_count("entities", "shared-name") == 1
    store.remove("concepts", "shared-name")
    assert store.get("concepts", "shared-name") is None
    assert store.get("entities", "shared-name") is not None


def test_persistence_across_instances(tmp_path):
    path = tmp_path / "pending_topics.json"
    store1 = PendingTopicsStore(path)
    store1.add_note("concepts", "attention", "desc", "doc-1", "summaries/doc-1.md", "note 1")

    store2 = PendingTopicsStore(path)
    assert store2.note_count("concepts", "attention") == 1
    entry = store2.get("concepts", "attention")
    assert entry["notes"][0]["note"] == "note 1"


def test_creates_parent_directory(tmp_path):
    path = tmp_path / ".openkb" / "pending_topics.json"
    assert not path.parent.exists()
    store = PendingTopicsStore(path)
    store.add_note("concepts", "attention", "desc", "doc-1", "summaries/doc-1.md", "note 1")
    assert path.exists()
