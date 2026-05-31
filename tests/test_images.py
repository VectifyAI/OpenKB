"""Tests for openkb.images — base64 extraction and relative image copy."""
from __future__ import annotations

import base64


from openkb.images import copy_relative_images, extract_base64_images, localize_images


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
        assert "![alt text](sources/images/doc/img_001.png)" == result

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

        assert "![fig1](sources/images/doc/img_001.png)" in result
        assert "![fig2](sources/images/doc/img_002.jpeg)" in result
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
        assert "![good](sources/images/doc/img_001.png)" in result
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

        assert "![diagram](sources/images/doc/diagram.png)" == result
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

        assert "![a](sources/images/doc/a.png)" in result
        assert "![b](sources/images/doc/b.jpg)" in result
        assert (images_dir / "a.png").exists()
        assert (images_dir / "b.jpg").exists()


# ---------------------------------------------------------------------------
# localize_images
# ---------------------------------------------------------------------------


def test_localize_images_writes_bytes_and_rewrites_bare_refs(tmp_path):
    images_dir = tmp_path / "wiki" / "sources" / "images" / "doc"
    md = "Before\n\n![fig](p1_img1.png)\n\nAfter"
    out = localize_images(md, {"p1_img1.png": b"PNGDATA"}, "doc", images_dir)
    assert "![fig](sources/images/doc/p1_img1.png)" in out
    assert (images_dir / "p1_img1.png").read_bytes() == b"PNGDATA"


def test_localize_images_handles_inline_base64(tmp_path):
    import base64
    images_dir = tmp_path / "wiki" / "sources" / "images" / "doc"
    payload = base64.b64encode(b"JPEGDATA").decode()
    md = f"![x](data:image/jpeg;base64,{payload})"
    out = localize_images(md, {}, "doc", images_dir)
    assert "sources/images/doc/img_001.jpeg" in out
    assert (images_dir / "img_001.jpeg").read_bytes() == b"JPEGDATA"


def test_localize_images_leaves_unreferenced_bytes_on_disk(tmp_path):
    images_dir = tmp_path / "wiki" / "sources" / "images" / "doc"
    out = localize_images("no images here", {"orphan.png": b"X"}, "doc", images_dir)
    assert out == "no images here"
    assert (images_dir / "orphan.png").read_bytes() == b"X"


def test_localize_images_filename_with_regex_metachars(tmp_path):
    images_dir = tmp_path / "wiki" / "sources" / "images" / "doc"
    weird = r"img\g<9>.png"  # backslash-escape-like name must not crash re.sub
    md = f"![f]({weird})"
    out = localize_images(md, {weird: b"DATA"}, "doc", images_dir)
    assert f"sources/images/doc/{weird}" in out
    assert (images_dir / weird).read_bytes() == b"DATA"
