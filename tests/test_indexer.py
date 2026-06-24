"""Tests for openkb.indexer."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from openkb.indexer import IndexResult, _normalize_page_content, index_long_document
from openkb.pdf_extractor import PageContent


class TestNormalizePageContent:
    def test_normalizes_pageindex_dicts(self):
        pages = _normalize_page_content([
            {"page_number": "2", "markdown": "  Page two  ", "images": [{"path": "sources/images/doc/a.png"}]},
            {"page_num": 3, "text": "Page three", "images": "bad"},
        ])

        assert pages == [
            {
                "page": 2,
                "content": "Page two",
                "images": [{"path": "sources/images/doc/a.png"}],
            },
            {"page": 3, "content": "Page three", "images": []},
        ]

    def test_normalizes_string_pages(self):
        pages = _normalize_page_content([" page one ", "", "page three"])

        assert pages == [
            {"page": 1, "content": "page one", "images": []},
            {"page": 3, "content": "page three", "images": []},
        ]

    def test_rejects_unusable_shapes(self):
        assert _normalize_page_content({"page": 1}) == []
        assert _normalize_page_content([None, {}, {"content": ""}]) == []


class TestIndexLongDocument:
    def _make_fake_collection(self, doc_id: str, sample_tree: dict):
        """Build a mock Collection that returns the sample_tree fixture data."""
        col = MagicMock()
        col.add.return_value = doc_id

        # get_document(doc_id, include_text=True) returns full document
        col.get_document.return_value = {
            "doc_id": doc_id,
            "doc_name": sample_tree["doc_name"],
            "doc_description": sample_tree["doc_description"],
            "doc_type": "pdf",
            "structure": sample_tree["structure"],
        }

        # get_page_content returns empty list by default (overridden per test as needed)
        col.get_page_content.return_value = []
        return col

    def _fake_pages(self):
        return [
            PageContent(page=1, content="Page one text.", images=[]),
            PageContent(page=2, content="Page two text.", images=[]),
        ]

    def test_returns_index_result(self, kb_dir, sample_tree, tmp_path):
        doc_id = "abc-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        extractor = MagicMock()
        extractor.parse_pages.return_value = self._fake_pages()

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
             patch("openkb.indexer.resolve_pdf_extractor", return_value=extractor):
            result = index_long_document(pdf_path, kb_dir)

        assert isinstance(result, IndexResult)
        assert result.doc_id == doc_id
        assert result.description == sample_tree["doc_description"]
        assert result.tree is not None

    def test_source_page_written_as_json(self, kb_dir, sample_tree, tmp_path):
        """Long doc source should be written as JSON, not markdown."""
        import json as json_mod
        doc_id = "abc-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col
        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        extractor = MagicMock()
        extractor.parse_pages.return_value = self._fake_pages()

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
             patch("openkb.indexer.resolve_pdf_extractor", return_value=extractor):
            index_long_document(pdf_path, kb_dir)

        json_file = kb_dir / "wiki" / "sources" / "sample.json"
        assert json_file.exists()
        assert not (kb_dir / "wiki" / "sources" / "sample.md").exists()
        data = json_mod.loads(json_file.read_text())
        assert len(data) == 2
        assert data[0]["page"] == 1
        assert data[0]["content"] == "Page one text."

    def test_summary_page_written(self, kb_dir, sample_tree, tmp_path):
        doc_id = "abc-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        extractor = MagicMock()
        extractor.parse_pages.return_value = self._fake_pages()

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
             patch("openkb.indexer.resolve_pdf_extractor", return_value=extractor):
            index_long_document(pdf_path, kb_dir)

        summary_file = kb_dir / "wiki" / "summaries" / "sample.md"
        assert summary_file.exists()
        content = summary_file.read_text(encoding="utf-8")
        assert "doc_type: pageindex" in content
        assert "Summary:" in content

    def test_localclient_called_with_index_config(self, kb_dir, sample_tree, tmp_path):
        """LocalClient must be created with the correct IndexConfig flags."""
        doc_id = "xyz-456"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        extractor = MagicMock()
        extractor.parse_pages.return_value = self._fake_pages()

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client) as mock_cls, \
             patch("openkb.indexer.resolve_pdf_extractor", return_value=extractor):
            index_long_document(pdf_path, kb_dir)

        # Verify PageIndexClient was instantiated with correct IndexConfig
        mock_cls.assert_called_once()
        _, kwargs = mock_cls.call_args
        ic = kwargs.get("index_config")
        assert ic is not None, "index_config must be passed to PageIndexClient"
        assert ic.if_add_node_text is True
        assert ic.if_add_node_summary is True
        assert ic.if_add_doc_description is True

    def test_cloud_page_content_is_normalized_before_shared_extractor_fallback(self, kb_dir, sample_tree, tmp_path, monkeypatch):
        doc_id = "cloud-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)
        fake_col.get_page_content.return_value = [
            {"page_number": "1", "markdown": "Cloud page one."},
            "Cloud page two.",
        ]

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setenv("PAGEINDEX_API_KEY", "test-key")

        extractor = MagicMock()
        extractor.parse_pages.return_value = self._fake_pages()

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
             patch("openkb.indexer._get_pdf_page_count", return_value=2), \
             patch("openkb.indexer.resolve_pdf_extractor", return_value=extractor):
            index_long_document(pdf_path, kb_dir)

        fake_col.get_page_content.assert_called_once_with(doc_id, "1-2")
        extractor.parse_pages.assert_not_called()
        json_file = kb_dir / "wiki" / "sources" / "sample.json"
        assert '"content": "Cloud page one."' in json_file.read_text(encoding="utf-8")
        assert '"content": "Cloud page two."' in json_file.read_text(encoding="utf-8")

    def test_invalid_cloud_page_content_falls_back_to_shared_extractor(self, kb_dir, sample_tree, tmp_path, monkeypatch):
        doc_id = "cloud-456"
        fake_col = self._make_fake_collection(doc_id, sample_tree)
        fake_col.get_page_content.return_value = {"bad": "shape"}

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setenv("PAGEINDEX_API_KEY", "test-key")

        extractor = MagicMock()
        extractor.parse_pages.return_value = self._fake_pages()

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
             patch("openkb.indexer._get_pdf_page_count", return_value=2), \
             patch("openkb.indexer.resolve_pdf_extractor", return_value=extractor):
            index_long_document(pdf_path, kb_dir)

        fake_col.get_page_content.assert_called_once_with(doc_id, "1-2")
        extractor.parse_pages.assert_called_once()
        json_file = kb_dir / "wiki" / "sources" / "sample.json"
        assert "Page one text." in json_file.read_text(encoding="utf-8")

    def test_empty_shared_extractor_pages_fail(self, kb_dir, sample_tree, tmp_path, monkeypatch):
        doc_id = "empty-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setenv("PAGEINDEX_API_KEY", "test-key")

        extractor = MagicMock()
        extractor.parse_pages.return_value = []

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
             patch("openkb.indexer._get_pdf_page_count", return_value=2), \
             patch("openkb.indexer.resolve_pdf_extractor", return_value=extractor):
            try:
                index_long_document(pdf_path, kb_dir)
            except RuntimeError as exc:
                assert "No page content extracted" in str(exc)
            else:
                raise AssertionError("expected RuntimeError")


def test_index_long_document_uses_explicit_doc_name(kb_dir, monkeypatch):
    monkeypatch.delenv("PAGEINDEX_API_KEY", raising=False)

    fake_col = MagicMock()
    fake_col.add.return_value = "doc-123"
    fake_col.get_document.return_value = {
        "doc_name": "original.pdf",
        "doc_description": "desc",
        "structure": [],
    }
    fake_client = MagicMock()
    fake_client.collection.return_value = fake_col

    pdf = kb_dir / "raw" / "original.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    extractor = MagicMock()
    extractor.parse_pages.return_value = [PageContent(page=1, content="p1", images=[])]

    with patch("openkb.indexer.PageIndexClient", return_value=fake_client), \
         patch("openkb.indexer.resolve_pdf_extractor", return_value=extractor):
        result = index_long_document(pdf, kb_dir, doc_name="original-abc12345")

    assert result.doc_id == "doc-123"
    assert (kb_dir / "wiki" / "sources" / "original-abc12345.json").exists()
    assert (kb_dir / "wiki" / "summaries" / "original-abc12345.md").exists()
    # nothing written under the raw stem
    assert not (kb_dir / "wiki" / "sources" / "original.json").exists()
    assert not (kb_dir / "wiki" / "summaries" / "original.md").exists()
    # the page extractor receives the explicit doc_name and its images dir
    expected_images = kb_dir / "wiki" / "sources" / "images" / "original-abc12345"
    extractor.parse_pages.assert_called_once_with(pdf, "original-abc12345", expected_images)
    # summary frontmatter points full_text at the doc_name artifact
    summary_text = (kb_dir / "wiki" / "summaries" / "original-abc12345.md").read_text(encoding="utf-8")
    assert "original-abc12345" in summary_text
