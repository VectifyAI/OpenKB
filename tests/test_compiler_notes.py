"""Tests for openkb.agent.compiler_notes (concept_update_mode="append")."""

from __future__ import annotations

from openkb.agent.compiler_notes import (
    _NOTES_HEADING,
    _upsert_note_line,
    append_concept_note,
    append_entity_note,
    note_fields,
)


class TestNoteFields:
    def test_parses_description_and_note(self):
        raw = '{"description": "A greeting", "note": "Says hello."}'
        assert note_fields(raw) == ("A greeting", "Says hello.")

    def test_update_shape_has_no_description(self):
        raw = '{"note": "Adds a detail."}'
        assert note_fields(raw) == ("", "Adds a detail.")

    def test_non_json_falls_back_to_raw_text_as_note(self):
        assert note_fields("  Just a plain note.  ") == ("", "Just a plain note.")

    def test_malformed_shape_returns_empty(self):
        # A JSON array of scalars is valid JSON but not a usable object.
        assert note_fields("[1, 2, 3]") == ("", "")


class TestUpsertNoteLine:
    def test_seeds_heading_and_inserts_first_note(self):
        body = _upsert_note_line(f"{_NOTES_HEADING}\n\n", "jira-1", "First note.")
        expected = f"{_NOTES_HEADING}\n\n- **{_today()}** First note. ([[summaries/jira-1]])"
        assert body.rstrip("\n") == expected

    def test_second_doc_inserted_above_first(self):
        body = _upsert_note_line(f"{_NOTES_HEADING}\n\n", "jira-1", "First note.")
        body = _upsert_note_line(body, "jira-2", "Second note.")
        lines = body.split("\n")
        note_lines = [ln for ln in lines if ln.startswith("- **")]
        assert len(note_lines) == 2
        assert "jira-2" in note_lines[0]  # newest first
        assert "jira-1" in note_lines[1]

    def test_reingesting_same_doc_replaces_not_duplicates(self):
        body = _upsert_note_line(f"{_NOTES_HEADING}\n\n", "jira-1", "Old text.")
        body = _upsert_note_line(body, "jira-2", "Unrelated.")
        body = _upsert_note_line(body, "jira-1", "Updated text.")
        note_lines = [ln for ln in body.split("\n") if ln.startswith("- **")]
        assert len(note_lines) == 2
        assert any("Updated text." in ln for ln in note_lines)
        assert not any("Old text." in ln for ln in note_lines)

    def test_creates_missing_heading(self):
        body = _upsert_note_line("Some unrelated body.", "jira-1", "A note.")
        assert body.split("\n") == [
            "Some unrelated body.",
            "",
            _NOTES_HEADING,
            "",
            f"- **{_today()}** A note. ([[summaries/jira-1]])",
        ]


class TestAppendConceptNote:
    def test_creates_new_page_with_description(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        append_concept_note(
            wiki,
            "approval-workflows",
            "Customer reports a timeout.",
            "summaries/jira-1.md",
            "jira-1",
            description="How approvals are routed.",
        )
        path = wiki / "concepts" / "approval-workflows.md"
        text = path.read_text(encoding="utf-8")
        assert 'type: "Concept"' in text
        assert 'sources: ["summaries/jira-1.md"]' in text
        assert 'description: "How approvals are routed."' in text
        assert _NOTES_HEADING in text
        assert "Customer reports a timeout." in text
        assert "[[summaries/jira-1]]" in text

    def test_creates_new_page_without_description(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        append_concept_note(wiki, "approval-workflows", "A note.", "summaries/jira-1.md", "jira-1")
        text = (wiki / "concepts" / "approval-workflows.md").read_text(encoding="utf-8")
        assert "description:" not in text

    def test_update_appends_source_and_note_keeps_description(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        append_concept_note(
            wiki,
            "approval-workflows",
            "First ticket note.",
            "summaries/jira-1.md",
            "jira-1",
            description="Original description.",
        )
        append_concept_note(
            wiki,
            "approval-workflows",
            "Second ticket note.",
            "summaries/jira-2.md",
            "jira-2",
            description="Ignored — page already exists.",
        )
        text = (wiki / "concepts" / "approval-workflows.md").read_text(encoding="utf-8")
        assert '"summaries/jira-1.md"' in text
        assert '"summaries/jira-2.md"' in text
        assert "First ticket note." in text
        assert "Second ticket note." in text
        # description is frozen at first creation, never overwritten.
        assert 'description: "Original description."' in text
        assert "Ignored" not in text

    def test_reingest_same_doc_replaces_note_not_source_duplicate(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        append_concept_note(
            wiki, "approval-workflows", "Old note.", "summaries/jira-1.md", "jira-1"
        )
        append_concept_note(
            wiki, "approval-workflows", "Updated note.", "summaries/jira-1.md", "jira-1"
        )
        text = (wiki / "concepts" / "approval-workflows.md").read_text(encoding="utf-8")
        assert text.count("summaries/jira-1.md") == 1  # sources: list stays deduped
        assert "Updated note." in text
        assert "Old note." not in text


class TestAppendEntityNote:
    def test_creates_new_page_with_capitalized_type(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        append_entity_note(
            wiki,
            "acme-corp",
            "Mentioned as the customer.",
            "summaries/jira-1.md",
            "jira-1",
            description="A customer organization.",
            type_="organization",
        )
        text = (wiki / "entities" / "acme-corp.md").read_text(encoding="utf-8")
        assert 'type: "Organization"' in text
        assert 'description: "A customer organization."' in text
        assert "Mentioned as the customer." in text

    def test_update_keeps_original_type_regardless_of_new_value(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        append_entity_note(
            wiki, "acme-corp", "First note.", "summaries/jira-1.md", "jira-1", type_="organization"
        )
        append_entity_note(
            wiki, "acme-corp", "Second note.", "summaries/jira-2.md", "jira-2", type_="person"
        )
        text = (wiki / "entities" / "acme-corp.md").read_text(encoding="utf-8")
        assert 'type: "Organization"' in text
        assert "Person" not in text


def _today() -> str:
    import datetime

    return datetime.date.today().isoformat()
