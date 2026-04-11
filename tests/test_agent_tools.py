"""Tests for openkb.agent.tools — plain function implementations."""
from __future__ import annotations

from pathlib import Path

import pytest

from openkb.agent.tools import get_wiki_page_content, list_wiki_files, parse_pages, read_wiki_file, write_wiki_file


# ---------------------------------------------------------------------------
# list_wiki_files
# ---------------------------------------------------------------------------


class TestListWikiFiles:
    def test_lists_md_files(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "doc1.md").write_text("# Doc 1")
        (tmp_path / "sources" / "doc2.md").write_text("# Doc 2")

        result = list_wiki_files("sources", wiki_root)

        assert "doc1.md" in result
        assert "doc2.md" in result

    def test_empty_directory_returns_no_files(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "concepts").mkdir()

        result = list_wiki_files("concepts", wiki_root)

        assert result == "No files found."

    def test_only_md_files_returned(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "doc.md").write_text("# Doc")
        (tmp_path / "sources" / "image.png").write_bytes(b"PNG")
        (tmp_path / "sources" / "data.json").write_text("{}")

        result = list_wiki_files("sources", wiki_root)

        assert "doc.md" in result
        assert "image.png" not in result
        assert "data.json" not in result

    def test_nonexistent_directory_returns_no_files(self, tmp_path):
        wiki_root = str(tmp_path)

        result = list_wiki_files("does_not_exist", wiki_root)

        assert result == "No files found."


# ---------------------------------------------------------------------------
# read_wiki_file
# ---------------------------------------------------------------------------


class TestReadWikiFile:
    def test_reads_existing_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        (tmp_path / "sources" / "notes.md").write_text("# Notes\n\nContent here.")

        result = read_wiki_file("sources/notes.md", wiki_root)

        assert "# Notes" in result
        assert "Content here." in result

    def test_missing_file_returns_not_found(self, tmp_path):
        wiki_root = str(tmp_path)

        result = read_wiki_file("sources/missing.md", wiki_root)

        assert result == "File not found: sources/missing.md"

    def test_path_is_relative_to_wiki_root(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "summaries").mkdir()
        (tmp_path / "summaries" / "paper.md").write_text("Summary content.")

        result = read_wiki_file("summaries/paper.md", wiki_root)

        assert "Summary content." in result


# ---------------------------------------------------------------------------
# write_wiki_file
# ---------------------------------------------------------------------------


class TestWriteWikiFile:
    def test_writes_new_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "concepts").mkdir()

        result = write_wiki_file("concepts/new_concept.md", "# New Concept\n", wiki_root)

        assert result == "Written: concepts/new_concept.md"
        assert (tmp_path / "concepts" / "new_concept.md").read_text() == "# New Concept\n"

    def test_overwrites_existing_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "concepts").mkdir()
        (tmp_path / "concepts" / "existing.md").write_text("Old content.")

        write_wiki_file("concepts/existing.md", "New content.", wiki_root)

        assert (tmp_path / "concepts" / "existing.md").read_text() == "New content."

    def test_creates_parent_directories(self, tmp_path):
        wiki_root = str(tmp_path)

        result = write_wiki_file(
            "deep/nested/dir/file.md", "# Deep File\n", wiki_root
        )

        assert result == "Written: deep/nested/dir/file.md"
        assert (tmp_path / "deep" / "nested" / "dir" / "file.md").exists()

    def test_returns_written_path(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "reports").mkdir()

        result = write_wiki_file("reports/health.md", "All good.", wiki_root)

        assert result == "Written: reports/health.md"


# ---------------------------------------------------------------------------
# parse_pages
# ---------------------------------------------------------------------------


class TestParsePages:
    def test_single_page(self):
        assert parse_pages("3") == [3]

    def test_range(self):
        assert parse_pages("3-5") == [3, 4, 5]

    def test_comma_separated(self):
        assert parse_pages("1,3,5") == [1, 3, 5]

    def test_mixed(self):
        assert parse_pages("1-3,7,10-12") == [1, 2, 3, 7, 10, 11, 12]

    def test_deduplication(self):
        assert parse_pages("3,3,3") == [3]

    def test_sorted(self):
        assert parse_pages("5,1,3") == [1, 3, 5]

    def test_ignores_zero_and_negative(self):
        assert parse_pages("0,-1,3") == [3]


# ---------------------------------------------------------------------------
# get_wiki_page_content
# ---------------------------------------------------------------------------


class TestGetWikiPageContent:
    def test_reads_pages_from_json(self, tmp_path):
        import json
        wiki_root = str(tmp_path)
        sources = tmp_path / "sources"
        sources.mkdir()
        pages = [
            {"page": 1, "content": "Page one text."},
            {"page": 2, "content": "Page two text."},
            {"page": 3, "content": "Page three text."},
        ]
        (sources / "paper.json").write_text(json.dumps(pages), encoding="utf-8")
        result = get_wiki_page_content("paper", "1,3", wiki_root)
        assert "[Page 1]" in result
        assert "Page one text." in result
        assert "[Page 3]" in result
        assert "Page three text." in result
        assert "Page two" not in result

    def test_returns_error_for_missing_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        result = get_wiki_page_content("nonexistent", "1", wiki_root)
        assert "not found" in result.lower()

    def test_returns_error_for_no_matching_pages(self, tmp_path):
        import json
        wiki_root = str(tmp_path)
        sources = tmp_path / "sources"
        sources.mkdir()
        pages = [{"page": 1, "content": "Only page."}]
        (sources / "paper.json").write_text(json.dumps(pages), encoding="utf-8")
        result = get_wiki_page_content("paper", "99", wiki_root)
        assert "no content" in result.lower()

    def test_includes_images_info(self, tmp_path):
        import json
        wiki_root = str(tmp_path)
        sources = tmp_path / "sources"
        sources.mkdir()
        pages = [{"page": 1, "content": "Text.", "images": [{"path": "images/p/img.png", "width": 100, "height": 80}]}]
        (sources / "doc.json").write_text(json.dumps(pages), encoding="utf-8")
        result = get_wiki_page_content("doc", "1", wiki_root)
        assert "img.png" in result

    def test_path_escape_denied(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        result = get_wiki_page_content("../../etc/passwd", "1", wiki_root)
        assert "denied" in result.lower() or "not found" in result.lower()
