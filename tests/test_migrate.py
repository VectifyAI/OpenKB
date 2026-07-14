"""Tests for openkb.migrate and the migrate-images CLI command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from openkb.cli import cli
from openkb.migrate import migrate_source_image_links

OLD_PAGE = (
    "# Doc\n\n"
    "Intro text.\n\n"
    "![figure 1](sources/images/doc/p1_img1.png)\n\n"
    "More text with an inline ![icon](sources/images/doc/p2_img2.png) here.\n"
)

MIGRATED_PAGE = (
    "# Doc\n\n"
    "Intro text.\n\n"
    "![figure 1](images/doc/p1_img1.png)\n\n"
    "More text with an inline ![icon](images/doc/p2_img2.png) here.\n"
)


def _make_wiki(tmp_path: Path) -> Path:
    wiki = tmp_path / "wiki"
    (wiki / "sources" / "images").mkdir(parents=True)
    (wiki / "summaries").mkdir(parents=True)
    return wiki


class TestMigrateSourceImageLinks:
    def test_rewrites_old_links_preserving_alt_and_text(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        page = wiki / "sources" / "doc.md"
        page.write_text(OLD_PAGE, encoding="utf-8")

        changed = migrate_source_image_links(wiki)

        assert changed == [(page, 2)]
        assert page.read_text(encoding="utf-8") == MIGRATED_PAGE

    def test_idempotent_on_migrated_page(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        page = wiki / "sources" / "doc.md"
        page.write_text(MIGRATED_PAGE, encoding="utf-8")

        assert migrate_source_image_links(wiki) == []
        assert page.read_text(encoding="utf-8") == MIGRATED_PAGE

    def test_dry_run_reports_without_writing(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        page = wiki / "sources" / "doc.md"
        page.write_text(OLD_PAGE, encoding="utf-8")

        changed = migrate_source_image_links(wiki, dry_run=True)

        assert changed == [(page, 2)]
        assert page.read_text(encoding="utf-8") == OLD_PAGE

    def test_long_doc_json_left_untouched(self, tmp_path):
        # Per-page JSON keeps wiki-root-relative paths by design.
        wiki = _make_wiki(tmp_path)
        payload = json.dumps([{"page": 1, "content": "![image](sources/images/doc/p1_img1.png)"}])
        doc_json = wiki / "sources" / "doc.json"
        doc_json.write_text(payload, encoding="utf-8")

        assert migrate_source_image_links(wiki) == []
        assert doc_json.read_text(encoding="utf-8") == payload

    def test_pages_outside_sources_left_untouched(self, tmp_path):
        # Note-relative resolution differs outside sources/ — out of scope.
        wiki = _make_wiki(tmp_path)
        summary = wiki / "summaries" / "doc.md"
        summary.write_text(OLD_PAGE, encoding="utf-8")

        assert migrate_source_image_links(wiki) == []
        assert summary.read_text(encoding="utf-8") == OLD_PAGE

    def test_missing_sources_dir_is_noop(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()

        assert migrate_source_image_links(wiki) == []

    def test_multiple_files_sorted(self, tmp_path):
        wiki = _make_wiki(tmp_path)
        page_b = wiki / "sources" / "b.md"
        page_a = wiki / "sources" / "a.md"
        page_b.write_text("![x](sources/images/b/i.png)", encoding="utf-8")
        page_a.write_text(OLD_PAGE, encoding="utf-8")

        changed = migrate_source_image_links(wiki)

        assert changed == [(page_a, 2), (page_b, 1)]


class TestMigrateImagesCommand:
    def _setup_kb(self, tmp_path: Path) -> Path:
        kb_dir = tmp_path
        (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True)
        (kb_dir / ".openkb").mkdir()
        return kb_dir

    def test_no_kb(self, tmp_path):
        runner = CliRunner()
        with (
            runner.isolated_filesystem(temp_dir=tmp_path),
            patch("openkb.cli._find_kb_dir", return_value=None),
        ):
            result = runner.invoke(cli, ["migrate-images"])
        assert result.exit_code == 0
        assert "No knowledge base found" in result.output

    def test_nothing_to_migrate(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        (kb_dir / "wiki" / "sources" / "doc.md").write_text(MIGRATED_PAGE, encoding="utf-8")
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["migrate-images"])
        assert result.exit_code == 0
        assert "Nothing to migrate" in result.output

    def test_migrates_and_logs(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        page = kb_dir / "wiki" / "sources" / "doc.md"
        page.write_text(OLD_PAGE, encoding="utf-8")
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["migrate-images"])
        assert result.exit_code == 0
        assert "Rewrote 2 image link(s) across 1 file(s)." in result.output
        assert "doc.md: 2 link(s)" in result.output
        assert page.read_text(encoding="utf-8") == MIGRATED_PAGE
        log = kb_dir / "wiki" / "log.md"
        assert log.exists()
        assert "migrate-images" in log.read_text(encoding="utf-8")

    def test_dry_run_does_not_write(self, tmp_path):
        kb_dir = self._setup_kb(tmp_path)
        page = kb_dir / "wiki" / "sources" / "doc.md"
        page.write_text(OLD_PAGE, encoding="utf-8")
        runner = CliRunner()
        with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
            result = runner.invoke(cli, ["migrate-images", "--dry-run"])
        assert result.exit_code == 0
        assert "Would rewrite 2 image link(s) across 1 file(s)." in result.output
        assert page.read_text(encoding="utf-8") == OLD_PAGE
        assert not (kb_dir / "wiki" / "log.md").exists()
