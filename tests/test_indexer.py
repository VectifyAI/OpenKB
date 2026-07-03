"""Tests for openkb.indexer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openkb.indexer import IndexResult, _normalize_page_content, index_long_document


class TestNormalizePageContent:
    def test_normalizes_pageindex_dicts(self):
        pages = _normalize_page_content(
            [
                {
                    "page_number": "2",
                    "markdown": "  Page two  ",
                    "images": [{"path": "sources/images/doc/a.png"}],
                },
                {"page_num": 3, "text": "Page three", "images": "bad"},
            ]
        )

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
    @pytest.fixture(autouse=True)
    def _local_path_by_default(self, monkeypatch):
        # These tests exercise the LOCAL indexing path; unset PAGEINDEX_API_KEY
        # so they are deterministic regardless of a developer's configured key
        # (otherwise the cloud branch reads the page count from the fake PDF and
        # raises). Cloud-path tests in this class re-enable it via setenv.
        monkeypatch.delenv("PAGEINDEX_API_KEY", raising=False)

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
            {"page": 1, "content": "Page one text.", "images": []},
            {"page": 2, "content": "Page two text.", "images": []},
        ]

    def test_returns_index_result(self, kb_dir, sample_tree, tmp_path):
        doc_id = "abc-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        with (
            patch("openkb.indexer.PageIndexClient", return_value=fake_client),
            patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()),
        ):
            result = index_long_document(pdf_path, kb_dir)

        assert isinstance(result, IndexResult)
        assert result.doc_id == doc_id
        assert result.description == sample_tree["doc_description"]
        assert result.tree is not None

    def test_deletes_pageindex_doc_when_a_post_add_step_fails(self, kb_dir, sample_tree, tmp_path):
        """The PageIndex blob is durably written by col.add(), but .openkb/files is
        no longer in the add mutation's eager snapshot — track_new only registers
        the blob on a successful return. So if any step after col.add() raises
        (here: get_document), index_long_document must delete the doc it just
        added; otherwise the blob leaks as an orphan that pageindex.db — rolled
        back by the snapshot — no longer references, and no reaper reclaims."""
        doc_id = "abc-123"
        col = self._make_fake_collection(doc_id, sample_tree)
        col.get_document.side_effect = RuntimeError("get_document blew up")

        fake_client = MagicMock()
        fake_client.collection.return_value = col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client):
            with pytest.raises(RuntimeError, match="get_document blew up"):
                index_long_document(pdf_path, kb_dir)

        col.delete_document.assert_called_once_with(doc_id)

    def test_source_page_written_as_json(self, kb_dir, sample_tree, tmp_path):
        """Long doc source should be written as JSON, not markdown."""
        import json as json_mod

        doc_id = "abc-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col
        # Mock get_page_content to return page data
        fake_col.get_page_content.return_value = [
            {"page": 1, "content": "Page one text."},
            {"page": 2, "content": "Page two text."},
        ]

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        with (
            patch("openkb.indexer.PageIndexClient", return_value=fake_client),
            patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()),
        ):
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

        with (
            patch("openkb.indexer.PageIndexClient", return_value=fake_client),
            patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()),
        ):
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

        with (
            patch("openkb.indexer.PageIndexClient", return_value=fake_client) as mock_cls,
            patch("openkb.images.convert_pdf_to_pages", return_value=self._fake_pages()),
        ):
            index_long_document(pdf_path, kb_dir)

        # Verify PageIndexClient was instantiated with correct IndexConfig
        mock_cls.assert_called_once()
        _, kwargs = mock_cls.call_args
        ic = kwargs.get("index_config")
        assert ic is not None, "index_config must be passed to PageIndexClient"
        assert ic.if_add_node_text is True
        assert ic.if_add_node_summary is True
        assert ic.if_add_doc_description is True

    def test_cloud_page_content_is_normalized(self, kb_dir, sample_tree, tmp_path, monkeypatch):
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

        with (
            patch("openkb.indexer.PageIndexClient", return_value=fake_client),
            patch("openkb.indexer._get_pdf_page_count", return_value=2),
            patch("openkb.indexer._convert_pdf_to_pages") as local_pages,
        ):
            index_long_document(pdf_path, kb_dir)

        local_pages.assert_not_called()
        json_file = kb_dir / "wiki" / "sources" / "sample.json"
        assert '"content": "Cloud page one."' in json_file.read_text(encoding="utf-8")
        assert '"content": "Cloud page two."' in json_file.read_text(encoding="utf-8")

    def test_invalid_cloud_page_content_falls_back_to_local(
        self, kb_dir, sample_tree, tmp_path, monkeypatch
    ):
        doc_id = "cloud-456"
        fake_col = self._make_fake_collection(doc_id, sample_tree)
        fake_col.get_page_content.return_value = {"bad": "shape"}

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setenv("PAGEINDEX_API_KEY", "test-key")

        with (
            patch("openkb.indexer.PageIndexClient", return_value=fake_client),
            patch("openkb.indexer._get_pdf_page_count", return_value=2),
            patch(
                "openkb.indexer._convert_pdf_to_pages", return_value=self._fake_pages()
            ) as local_pages,
        ):
            index_long_document(pdf_path, kb_dir)

        local_pages.assert_called_once()
        json_file = kb_dir / "wiki" / "sources" / "sample.json"
        assert "Page one text." in json_file.read_text(encoding="utf-8")

    def test_empty_cloud_and_local_pages_fail(self, kb_dir, sample_tree, tmp_path, monkeypatch):
        doc_id = "empty-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")
        monkeypatch.setenv("PAGEINDEX_API_KEY", "test-key")

        with (
            patch("openkb.indexer.PageIndexClient", return_value=fake_client),
            patch("openkb.indexer._get_pdf_page_count", return_value=2),
            patch("openkb.indexer._convert_pdf_to_pages", return_value=[]),
        ):
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

    with (
        patch("openkb.indexer.PageIndexClient", return_value=fake_client),
        patch("openkb.indexer._get_pdf_page_count", return_value=30),
        patch(
            "openkb.indexer._convert_pdf_to_pages", return_value=[{"page": 1, "text": "p1"}]
        ) as mock_convert,
    ):
        result = index_long_document(pdf, kb_dir, doc_name="original-abc12345")

    assert result.doc_id == "doc-123"
    assert (kb_dir / "wiki" / "sources" / "original-abc12345.json").exists()
    assert (kb_dir / "wiki" / "summaries" / "original-abc12345.md").exists()
    # nothing written under the raw stem
    assert not (kb_dir / "wiki" / "sources" / "original.json").exists()
    assert not (kb_dir / "wiki" / "summaries" / "original.md").exists()
    # the page extractor receives the explicit doc_name and its images dir
    expected_images = kb_dir / "wiki" / "sources" / "images" / "original-abc12345"
    mock_convert.assert_called_once_with(pdf, "original-abc12345", expected_images)
    # summary frontmatter points full_text at the doc_name artifact
    summary_text = (kb_dir / "wiki" / "summaries" / "original-abc12345.md").read_text(
        encoding="utf-8"
    )
    assert "original-abc12345" in summary_text


class TestImportCloudDocument:
    def _fake_client(self, doc_id, sample_tree, pages):
        col = MagicMock()
        col.get_document.return_value = {
            "doc_id": doc_id,
            "doc_name": "Cloud Paper.pdf",
            "doc_description": sample_tree["doc_description"],
            "structure": sample_tree["structure"],
        }
        col.get_page_content.return_value = pages
        client = MagicMock()
        client.collection.return_value = col
        return client, col

    def test_writes_artifacts_and_returns_result(self, kb_dir, sample_tree, monkeypatch):
        from openkb.indexer import CloudImportResult, import_cloud_document

        monkeypatch.setenv("PAGEINDEX_API_KEY", "test-key")
        pages = [{"page": 1, "content": "Cloud page one."}]
        client, col = self._fake_client("cloud-1", sample_tree, pages)

        with patch("openkb.indexer.PageIndexClient", return_value=client):
            result = import_cloud_document("cloud-1", kb_dir, "pageindex-cloud:cloud-1")

        assert isinstance(result, CloudImportResult)
        assert result.doc_id == "cloud-1"
        assert result.name == "Cloud Paper.pdf"
        assert result.doc_name == "Cloud-Paper"
        assert (kb_dir / "wiki" / "sources" / "Cloud-Paper.json").exists()
        assert (kb_dir / "wiki" / "summaries" / "Cloud-Paper.md").exists()
        # col.add must never be called — the doc already exists in the cloud
        col.add.assert_not_called()

    def test_requires_api_key(self, kb_dir, monkeypatch):
        from openkb.indexer import import_cloud_document

        monkeypatch.delenv("PAGEINDEX_API_KEY", raising=False)
        try:
            import_cloud_document("cloud-x", kb_dir, "pageindex-cloud:cloud-x")
        except RuntimeError as exc:
            assert "PAGEINDEX_API_KEY" in str(exc)
        else:
            raise AssertionError("expected RuntimeError when API key is missing")

    def test_empty_pages_raise(self, kb_dir, sample_tree, monkeypatch):
        from openkb.indexer import import_cloud_document

        monkeypatch.setenv("PAGEINDEX_API_KEY", "test-key")
        client, col = self._fake_client("cloud-2", sample_tree, [])

        with patch("openkb.indexer.PageIndexClient", return_value=client):
            try:
                import_cloud_document("cloud-2", kb_dir, "pageindex-cloud:cloud-2")
            except RuntimeError as exc:
                assert "No page content" in str(exc)
            else:
                raise AssertionError("expected RuntimeError on empty pages")


def test_write_long_doc_artifacts_writes_json_and_summary(kb_dir, sample_tree):
    from openkb.indexer import _write_long_doc_artifacts

    pages = [{"page": 1, "content": "Hello.", "images": []}]
    summary_path = _write_long_doc_artifacts(sample_tree, pages, "my-doc", "doc-1", kb_dir)

    assert summary_path == kb_dir / "wiki" / "summaries" / "my-doc.md"
    assert summary_path.exists()
    json_file = kb_dir / "wiki" / "sources" / "my-doc.json"
    assert json_file.exists()
    assert '"content": "Hello."' in json_file.read_text(encoding="utf-8")
    assert "doc_type: pageindex" in summary_path.read_text(encoding="utf-8")


def test_fetch_cloud_pages_windows_over_1000_cap():
    """get_page_content's range filter is capped at 1000 pages by parse_pages, so
    _fetch_cloud_pages must request fixed 1000-page windows (never a wider range)
    and concatenate them, preserving real page numbers.
    """
    from openkb.indexer import _fetch_cloud_pages

    def fake_get(doc_id, rng):
        start = int(rng.split("-")[0])
        if start == 1:
            return [{"page": p, "content": f"p{p}"} for p in range(1, 1001)]
        if start == 1001:
            return [{"page": p, "content": f"p{p}"} for p in range(1001, 1501)]
        return []

    col = MagicMock()
    col.get_page_content.side_effect = fake_get

    pages = _fetch_cloud_pages(col, "doc")
    assert len(pages) == 1500
    assert pages[0]["page"] == 1 and pages[-1]["page"] == 1500
    ranges = [c.args[1] for c in col.get_page_content.call_args_list]
    # Full first window → fetch the next; the short 2nd window (500<1000) stops it.
    assert ranges == ["1-1000", "1001-2000"]
    # Every requested window spans exactly 1000 pages → parse_pages never raises.
    for r in ranges:
        a, b = (int(x) for x in r.split("-"))
        assert b - a + 1 == 1000


def test_fetch_cloud_pages_full_window_triggers_next_fetch():
    """A doc whose pages exactly fill the first window must still fetch the next
    one. Regression: bounding the loop by the tree's max page index dropped the
    straggler page(s) of a doc whose tree under-reported its page count.
    """
    from openkb.indexer import _fetch_cloud_pages

    def fake_get(doc_id, rng):
        start = int(rng.split("-")[0])
        if start == 1:
            return [{"page": p, "content": "x"} for p in range(1, 1001)]  # full window
        if start == 1001:
            return [{"page": 1001, "content": "x"}]  # one straggler past the window
        return []

    col = MagicMock()
    col.get_page_content.side_effect = fake_get

    pages = _fetch_cloud_pages(col, "doc")
    assert [p["page"] for p in pages] == list(range(1, 1002))  # page 1001 NOT dropped


def test_import_cloud_document_no_indices_avoids_oversized_range(kb_dir, monkeypatch):
    """A cloud tree with no integer page indices must NOT request a 100000-page
    range (parse_pages rejects >1000); it windows from page 1 instead.
    """
    from openkb.indexer import import_cloud_document

    monkeypatch.setenv("PAGEINDEX_API_KEY", "test-key")
    col = MagicMock()
    col.get_document.return_value = {
        "doc_id": "c",
        "doc_name": "NoIdx.pdf",
        "doc_description": "d",
        "structure": [{"title": "n", "nodes": []}],  # no start/end_index anywhere
    }
    col.get_page_content.side_effect = (
        lambda doc_id, rng: [{"page": 1, "content": "x"}] if rng == "1-1000" else []
    )
    client = MagicMock()
    client.collection.return_value = col

    with patch("openkb.indexer.PageIndexClient", return_value=client):
        result = import_cloud_document("c", kb_dir, "pageindex-cloud:c")

    assert result.doc_id == "c"
    ranges = [c.args[1] for c in col.get_page_content.call_args_list]
    assert "1-100000" not in ranges
    for r in ranges:
        a, b = (int(x) for x in r.split("-"))
        assert b - a + 1 <= 1000
