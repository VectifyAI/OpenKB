"""Tests for openkb.converter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openkb.converter import convert_document, get_pdf_page_count

# ---------------------------------------------------------------------------
# get_pdf_page_count
# ---------------------------------------------------------------------------


class TestGetPdfPageCount:
    def test_returns_page_count(self, tmp_path):
        """Mock pymupdf to return a doc with 5 pages."""
        fake_doc = MagicMock()
        fake_doc.page_count = 5
        fake_doc.__enter__ = MagicMock(return_value=fake_doc)
        fake_doc.__exit__ = MagicMock(return_value=False)
        with patch("openkb.converter.pymupdf.open", return_value=fake_doc):
            count = get_pdf_page_count(tmp_path / "fake.pdf")
        assert count == 5


# ---------------------------------------------------------------------------
# convert_document — .md input
# ---------------------------------------------------------------------------


class TestConvertDocumentMarkdown:
    def test_md_file_copied_to_wiki_sources(self, kb_dir):
        """A .md file is read and saved under wiki/sources/."""
        src = kb_dir / "raw" / "notes.md"
        src.write_text("# Notes\n\nSome content here.", encoding="utf-8")

        result = convert_document(src, kb_dir)

        assert result.skipped is False
        assert result.is_long_doc is False
        assert result.source_path is not None
        assert result.source_path.exists()
        assert result.source_path.read_text(encoding="utf-8").startswith("# Notes")

    def test_md_duplicate_skipped(self, kb_dir):
        """Second call with same file returns skipped=True when hash is registered."""
        from openkb.state import HashRegistry

        src = kb_dir / "raw" / "notes.md"
        src.write_text("# Notes\n\nSome content here.", encoding="utf-8")

        result1 = convert_document(src, kb_dir)  # first call
        # Simulate CLI registering the hash after successful compilation
        registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        registry.add(result1.file_hash, {"name": src.name, "type": "md"})

        result2 = convert_document(src, kb_dir)  # second call
        assert result2.skipped is True
        assert result2.source_path is None
        assert result2.raw_path is None

    def test_md_raw_file_copied(self, kb_dir):
        """The original file should also be copied to raw/."""
        src = kb_dir / "input" / "notes.md"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("# Notes\n", encoding="utf-8")

        result = convert_document(src, kb_dir)

        assert result.raw_path is not None
        assert result.raw_path.exists()


# ---------------------------------------------------------------------------
# convert_document — PDF short doc
# ---------------------------------------------------------------------------


class TestConvertDocumentPdfShort:
    def test_short_pdf_converted_via_pymupdf(self, kb_dir, tmp_path):
        """PDF under threshold is converted with pymupdf (convert_pdf_with_images)."""
        src = tmp_path / "short.pdf"
        src.write_bytes(b"%PDF-1.4 fake content")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
            patch(
                "openkb.converter.convert_pdf_with_images", return_value="# Short PDF\n\nConverted."
            ) as mock_cpwi,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 5  # below default threshold of 20
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir)

        mock_cpwi.assert_called_once()
        assert result.skipped is False
        assert result.is_long_doc is False
        assert result.source_path is not None
        assert result.source_path.exists()


# ---------------------------------------------------------------------------
# convert_document — PDF long doc
# ---------------------------------------------------------------------------


class TestConvertDocumentPdfLong:
    def test_long_pdf_returns_is_long_doc(self, kb_dir, tmp_path):
        """PDF >= threshold pages returns is_long_doc=True, source_path=None."""
        src = tmp_path / "long.pdf"
        src.write_bytes(b"%PDF-1.4 fake long content")

        with (
            patch("openkb.converter.pymupdf.open") as mock_mu,
        ):
            fake_doc = MagicMock()
            fake_doc.page_count = 200  # above threshold
            fake_doc.__enter__ = MagicMock(return_value=fake_doc)
            fake_doc.__exit__ = MagicMock(return_value=False)
            mock_mu.return_value = fake_doc

            result = convert_document(src, kb_dir)

        assert result.is_long_doc is True
        assert result.source_path is None
        assert result.skipped is False
        assert result.raw_path is not None


# ---------------------------------------------------------------------------
# _registry_path
# ---------------------------------------------------------------------------


class TestRegistryPath:
    def test_inside_kb_is_relative_posix(self, kb_dir):
        from openkb.converter import _registry_path

        p = kb_dir / "raw" / "sub" / "doc.md"
        assert _registry_path(p, kb_dir) == "raw/sub/doc.md"

    def test_outside_kb_is_absolute_posix(self, kb_dir, tmp_path_factory):
        from openkb.converter import _registry_path

        outside = tmp_path_factory.mktemp("elsewhere") / "doc.md"
        result = _registry_path(outside, kb_dir)
        assert result == outside.resolve().as_posix()
        assert result.startswith("/")


# ---------------------------------------------------------------------------
# resolve_doc_name
# ---------------------------------------------------------------------------


class TestResolveDocName:
    def _registry(self, kb_dir):
        from openkb.state import HashRegistry

        return HashRegistry(kb_dir / ".openkb" / "hashes.json")

    def test_unique_name_stays_clean(self, kb_dir):
        from openkb.converter import resolve_doc_name

        src = kb_dir / "raw" / "report.md"
        src.write_text("x", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, self._registry(kb_dir)) == "report"

    def test_known_path_reuses_stored_doc_name(self, kb_dir):
        from openkb.converter import resolve_doc_name

        reg = self._registry(kb_dir)
        reg.add("h1", {"name": "report.md", "doc_name": "report-x1", "path": "inputs/report.md"})
        src = kb_dir / "inputs" / "report.md"
        src.parent.mkdir(parents=True)
        src.write_text("edited", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, reg) == "report-x1"

    def test_collision_gets_deterministic_suffix(self, kb_dir):
        import hashlib

        from openkb.converter import _registry_path, resolve_doc_name

        reg = self._registry(kb_dir)
        # "report" already taken by a different, path-indexed source
        reg.add("h1", {"name": "report.md", "doc_name": "report", "path": "inputs/first/report.md"})
        src = kb_dir / "inputs" / "second" / "report.md"
        src.parent.mkdir(parents=True)
        src.write_text("y", encoding="utf-8")
        expected_suffix = hashlib.sha256(_registry_path(src, kb_dir).encode("utf-8")).hexdigest()[
            :8
        ]
        assert resolve_doc_name(src, kb_dir, reg) == f"report-{expected_suffix}"

    def test_unclaimed_on_disk_artifact_is_adopted(self, kb_dir):
        # An on-disk sources file with NO registry entry is a leftover of a
        # failed ingest (or an out-of-contract manual drop): the registry is
        # the authority, so the clean name is reused and the artifact will
        # be overwritten — this is what keeps retry-after-failure stable.
        from openkb.converter import resolve_doc_name

        (kb_dir / "wiki" / "sources" / "report.md").write_text("old", encoding="utf-8")
        src = kb_dir / "raw" / "report.md"
        src.write_text("new attempt", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, self._registry(kb_dir)) == "report"

    def test_legacy_entry_is_reused_and_backfilled(self, kb_dir):
        from openkb.converter import _registry_path, resolve_doc_name

        reg = self._registry(kb_dir)
        reg.add("h_old", {"name": "notes.md", "doc_name": "notes", "type": "md"})
        src = kb_dir / "raw" / "notes.md"
        src.write_text("edited content", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, reg) == "notes"
        # path backfilled onto the legacy entry
        assert reg.get("h_old")["path"] == _registry_path(src, kb_dir)

    def test_stem_is_sanitized(self, kb_dir):
        from openkb.converter import resolve_doc_name

        src = kb_dir / "raw" / "my report (final).md"
        src.write_text("x", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, self._registry(kb_dir)) == "my-report-final"

    def test_same_stem_different_extension_collides(self, kb_dir):
        # report.pdf vs an existing "report" (from report.md) — extension
        # does not disambiguate; the second source gets a suffix.
        from openkb.converter import resolve_doc_name

        reg = self._registry(kb_dir)
        reg.add("h1", {"name": "report.md", "doc_name": "report", "path": "inputs/report.md"})
        src = kb_dir / "raw" / "report.pdf"
        src.write_bytes(b"%PDF-1.4 fake")
        name = resolve_doc_name(src, kb_dir, reg)
        assert name.startswith("report-") and name != "report"

    def test_cjk_stem_with_fullwidth_punctuation(self, kb_dir):
        from openkb.converter import resolve_doc_name

        src = kb_dir / "raw" / "技术报告（最终版）.md"
        src.write_text("x", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, self._registry(kb_dir)) == "技术报告-最终版"

    def test_all_symbol_stem_falls_back_to_document(self, kb_dir):
        from openkb.converter import resolve_doc_name

        src = kb_dir / "raw" / "!!!.md"
        src.write_text("x", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, self._registry(kb_dir)) == "document"

    def test_two_all_symbol_stems_second_gets_suffix(self, kb_dir):
        from openkb.converter import resolve_doc_name

        reg = self._registry(kb_dir)
        first = kb_dir / "raw" / "!!!.md"
        first.write_text("x", encoding="utf-8")
        assert resolve_doc_name(first, kb_dir, reg) == "document"
        reg.add("h1", {"name": "!!!.md", "doc_name": "document", "path": "raw/!!!.md"})
        second = kb_dir / "inputs" / "###.md"
        second.parent.mkdir(parents=True)
        second.write_text("y", encoding="utf-8")
        name = resolve_doc_name(second, kb_dir, reg)
        assert name.startswith("document-") and len(name) == len("document-") + 8

    def test_unclaimed_on_disk_long_doc_json_is_adopted(self, kb_dir):
        # Long docs leave wiki/sources/{name}.json — without a registry
        # entry it is likewise an unclaimed leftover: clean name is reused.
        from openkb.converter import resolve_doc_name

        (kb_dir / "wiki" / "sources" / "report.json").write_text("[]", encoding="utf-8")
        src = kb_dir / "raw" / "report.md"
        src.write_text("x", encoding="utf-8")
        assert resolve_doc_name(src, kb_dir, self._registry(kb_dir)) == "report"


# ---------------------------------------------------------------------------
# resolve_doc_name_from_key
# ---------------------------------------------------------------------------


def test_resolve_doc_name_from_key_clean(tmp_path):
    from openkb.converter import resolve_doc_name_from_key
    from openkb.state import HashRegistry

    registry = HashRegistry(tmp_path / "hashes.json")
    name = resolve_doc_name_from_key("Attention Is All You Need", "pageindex-cloud:abc", registry)
    assert name == "Attention-Is-All-You-Need"


def test_resolve_doc_name_from_key_collision_suffix(tmp_path):
    import hashlib

    from openkb.converter import resolve_doc_name_from_key
    from openkb.state import HashRegistry

    registry = HashRegistry(tmp_path / "hashes.json")
    registry.add("hash1", {"name": "paper.pdf", "doc_name": "paper"})

    path_key = "pageindex-cloud:xyz"
    name = resolve_doc_name_from_key("paper", path_key, registry)
    digest = hashlib.sha256(path_key.encode("utf-8")).hexdigest()[:8]
    assert name == f"paper-{digest}"


def test_resolve_doc_name_from_key_reuses_known_path(tmp_path):
    from openkb.converter import resolve_doc_name_from_key
    from openkb.state import HashRegistry

    registry = HashRegistry(tmp_path / "hashes.json")
    registry.add("h", {"doc_name": "kept-name", "path": "pageindex-cloud:dup"})
    name = resolve_doc_name_from_key("whatever", "pageindex-cloud:dup", registry)
    assert name == "kept-name"


# ---------------------------------------------------------------------------
# convert_document — doc_name collision handling
# ---------------------------------------------------------------------------


class TestConvertDocumentCollision:
    def test_same_basename_different_dirs_get_distinct_outputs(self, kb_dir):
        from openkb.converter import convert_document
        from openkb.state import HashRegistry

        first = kb_dir / "inputs" / "first" / "report.md"
        second = kb_dir / "inputs" / "second" / "report.md"
        first.parent.mkdir(parents=True)
        second.parent.mkdir(parents=True)
        first.write_text("# First\n\nAlpha.", encoding="utf-8")
        second.write_text("# Second\n\nBeta.", encoding="utf-8")

        r1 = convert_document(first, kb_dir)
        # Simulate add_single_file's registration so the second ingest
        # sees "report" as taken.
        HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
            r1.file_hash,
            {"name": "report.md", "doc_name": r1.doc_name, "path": "inputs/first/report.md"},
        )
        r2 = convert_document(second, kb_dir)

        assert r1.doc_name == "report"
        assert r2.doc_name.startswith("report-") and r2.doc_name != "report"
        assert r1.source_path != r2.source_path
        assert r1.source_path.read_text(encoding="utf-8").startswith("# First")
        assert r2.source_path.read_text(encoding="utf-8").startswith("# Second")
        assert r1.raw_path != r2.raw_path

    def test_skipped_dedup_carries_stored_doc_name(self, kb_dir):
        from openkb.converter import convert_document
        from openkb.state import HashRegistry

        src = kb_dir / "inputs" / "notes.md"
        src.parent.mkdir(parents=True)
        src.write_text("# Notes", encoding="utf-8")
        first = convert_document(src, kb_dir)
        HashRegistry(kb_dir / ".openkb" / "hashes.json").add(
            first.file_hash,
            {"name": "notes.md", "doc_name": first.doc_name, "path": "inputs/notes.md"},
        )
        again = convert_document(src, kb_dir)
        assert again.skipped is True
        assert again.doc_name == first.doc_name
        assert again.file_hash == first.file_hash

    def test_outputs_named_by_doc_name(self, kb_dir):
        from openkb.converter import convert_document

        src = kb_dir / "raw" / "my report (final).md"
        src.write_text("# R", encoding="utf-8")
        result = convert_document(src, kb_dir)
        assert result.doc_name == "my-report-final"
        assert result.source_path.name == "my-report-final.md"
        assert (kb_dir / "wiki" / "sources" / "images" / "my-report-final").is_dir()
        assert result.raw_path == src  # watch mode: no copy, no rename
        assert not (kb_dir / "raw" / "my-report-final.md").exists()

    def test_retry_after_failed_compile_keeps_clean_name(self, kb_dir):
        # convert succeeded but compile failed → nothing registered. The
        # retry must resolve to the SAME clean name, not a suffixed one.
        from openkb.converter import convert_document

        src = kb_dir / "inputs" / "report.md"
        src.parent.mkdir(parents=True)
        src.write_text("# R", encoding="utf-8")
        first = convert_document(src, kb_dir)  # artifacts written, no registration
        retry = convert_document(src, kb_dir)
        assert first.doc_name == "report"
        assert retry.doc_name == "report"
        assert retry.source_path == first.source_path

    def test_duplicate_copy_skip_does_not_backfill_path(self, kb_dir):
        # Re-adding an identical copy from another dir must dedup-skip
        # WITHOUT poisoning the legacy entry's path with the copy's path.
        from openkb.converter import convert_document
        from openkb.state import HashRegistry

        src_a = kb_dir / "in" / "a" / "notes.md"
        src_a.parent.mkdir(parents=True)
        src_a.write_text("# Notes", encoding="utf-8")
        first = convert_document(src_a, kb_dir)
        reg = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        # legacy-shaped entry: no path field (pre-upgrade registry)
        reg.add(first.file_hash, {"name": "notes.md", "doc_name": "notes", "type": "md"})

        src_b = kb_dir / "in" / "b" / "notes.md"
        src_b.parent.mkdir(parents=True)
        src_b.write_text("# Notes", encoding="utf-8")  # identical content
        again = convert_document(src_b, kb_dir)

        assert again.skipped is True
        assert again.doc_name == "notes"
        # re-read from disk: the add() above persisted; convert must not
        # have backfilled the copy's path onto the legacy entry
        reg2 = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        assert "path" not in reg2.get(first.file_hash)  # not poisoned
