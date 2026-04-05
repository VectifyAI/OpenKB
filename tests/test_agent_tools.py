"""Tests for openkb.agent.tools — plain function implementations."""
from __future__ import annotations

from pathlib import Path

import pytest

from openkb.agent.tools import list_wiki_files, read_wiki_file, write_wiki_file


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
