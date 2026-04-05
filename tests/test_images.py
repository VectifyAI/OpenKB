"""Tests for openkb.images — base64 extraction and relative image copy."""
from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest

from openkb.images import copy_relative_images, extract_base64_images


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8  # minimal fake PNG bytes
FAKE_JPG = b"\xff\xd8\xff" + b"\x00" * 8        # minimal fake JPEG bytes


# ---------------------------------------------------------------------------
# extract_base64_images
# ---------------------------------------------------------------------------


class TestExtractBase64Images:
    def test_no_images_returns_unchanged(self, tmp_path):
        md = "# Hello\n\nSome text without any images."
        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)
        result = extract_base64_images(md, "doc", images_dir)
        assert result == md

    def test_single_base64_image_extracted(self, tmp_path):
        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)
        b64 = _make_b64(FAKE_PNG)
        md = f"![alt text](data:image/png;base64,{b64})"
        result = extract_base64_images(md, "doc", images_dir)

        # Result should reference a saved file, not the raw base64
        assert "data:image/png;base64," not in result
        assert "![alt text](images/doc/img_001.png)" == result

        # File should exist on disk
        saved = images_dir / "img_001.png"
        assert saved.exists()
        assert saved.read_bytes() == FAKE_PNG

    def test_multiple_base64_images_numbered_sequentially(self, tmp_path):
        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)
        b64_png = _make_b64(FAKE_PNG)
        b64_jpg = _make_b64(FAKE_JPG)
        md = (
            f"![fig1](data:image/png;base64,{b64_png})\n"
            f"![fig2](data:image/jpeg;base64,{b64_jpg})"
        )
        result = extract_base64_images(md, "doc", images_dir)

        assert "![fig1](images/doc/img_001.png)" in result
        assert "![fig2](images/doc/img_002.jpeg)" in result
        assert (images_dir / "img_001.png").exists()
        assert (images_dir / "img_002.jpeg").exists()

    def test_invalid_base64_leaves_original(self, tmp_path, caplog):
        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)
        bad = "NOT_VALID_BASE64!!!"
        md = f"![alt](data:image/png;base64,{bad})"
        import logging
        with caplog.at_level(logging.WARNING, logger="openkb.images"):
            result = extract_base64_images(md, "doc", images_dir)
        assert result == md  # unchanged
        # No files created
        assert list(images_dir.iterdir()) == []

    def test_mixed_valid_invalid_base64(self, tmp_path, caplog):
        """Valid image extracted; invalid image left in place."""
        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)
        b64 = _make_b64(FAKE_PNG)
        bad = "BADBAD!!!"
        md = (
            f"![good](data:image/png;base64,{b64})\n"
            f"![bad](data:image/png;base64,{bad})"
        )
        import logging
        with caplog.at_level(logging.WARNING, logger="openkb.images"):
            result = extract_base64_images(md, "doc", images_dir)
        assert "![good](images/doc/img_001.png)" in result
        assert f"data:image/png;base64,{bad}" in result


# ---------------------------------------------------------------------------
# copy_relative_images
# ---------------------------------------------------------------------------


class TestCopyRelativeImages:
    def test_existing_relative_image_copied_and_rewritten(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        img_file = source_dir / "diagram.png"
        img_file.write_bytes(FAKE_PNG)

        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)

        md = "![diagram](diagram.png)"
        result = copy_relative_images(md, source_dir, "doc", images_dir)

        assert "![diagram](images/doc/diagram.png)" == result
        assert (images_dir / "diagram.png").read_bytes() == FAKE_PNG

    def test_missing_relative_image_leaves_original(self, tmp_path, caplog):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)

        md = "![missing](missing.png)"
        import logging
        with caplog.at_level(logging.WARNING, logger="openkb.images"):
            result = copy_relative_images(md, source_dir, "doc", images_dir)
        assert result == md  # unchanged
        assert list(images_dir.iterdir()) == []

    def test_http_url_not_processed(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)

        md = "![logo](https://example.com/logo.png)"
        result = copy_relative_images(md, source_dir, "doc", images_dir)
        assert result == md  # HTTP URLs left untouched

    def test_data_url_not_processed(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)

        b64 = _make_b64(FAKE_PNG)
        md = f"![img](data:image/png;base64,{b64})"
        result = copy_relative_images(md, source_dir, "doc", images_dir)
        assert result == md  # data URIs left untouched

    def test_multiple_relative_images_all_copied(self, tmp_path):
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "a.png").write_bytes(FAKE_PNG)
        (source_dir / "b.jpg").write_bytes(FAKE_JPG)

        images_dir = tmp_path / "images" / "doc"
        images_dir.mkdir(parents=True)

        md = "![a](a.png)\n![b](b.jpg)"
        result = copy_relative_images(md, source_dir, "doc", images_dir)

        assert "![a](images/doc/a.png)" in result
        assert "![b](images/doc/b.jpg)" in result
        assert (images_dir / "a.png").exists()
        assert (images_dir / "b.jpg").exists()
