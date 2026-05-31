from __future__ import annotations

import io
import sys
import types
import zipfile
from unittest.mock import MagicMock

import pytest

from openkb.parsers.base import ParseResult


def test_supports_office_and_pdf():
    from openkb.parsers.mineru import MineruParser
    p = MineruParser({})
    assert p.supports(".pdf") is True
    assert p.supports(".docx") is True
    assert p.supports(".md") is False


def test_self_hosted_requires_base_url(tmp_path):
    from openkb.parsers.mineru import MineruParser
    p = MineruParser({"mode": "self_hosted"})
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    with pytest.raises(RuntimeError) as exc:
        p.parse(src)
    assert "base_url" in str(exc.value)


def test_cloud_requires_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("MINERU_API_KEY", raising=False)
    from openkb.parsers.mineru import MineruParser
    p = MineruParser({"mode": "cloud"})
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    with pytest.raises(RuntimeError) as exc:
        p.parse(src)
    assert "MINERU_API_KEY" in str(exc.value)


def test_self_hosted_parses_zip(monkeypatch, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "# Mineru\n\n![p](images/fig.png)")
        zf.writestr("images/fig.png", b"PNGBYTES")
    zip_bytes = buf.getvalue()

    fake_resp = MagicMock(status_code=200, content=zip_bytes)
    fake_resp.raise_for_status = MagicMock()
    fake_client = MagicMock()
    fake_client.__enter__ = MagicMock(return_value=fake_client)
    fake_client.__exit__ = MagicMock(return_value=False)
    fake_client.post.return_value = fake_resp

    httpx_mod = types.ModuleType("httpx")
    httpx_mod.Client = MagicMock(return_value=fake_client)
    monkeypatch.setitem(sys.modules, "httpx", httpx_mod)

    from openkb.parsers.mineru import MineruParser
    p = MineruParser({"mode": "self_hosted", "base_url": "http://localhost:8000"})
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    result = p.parse(src)
    assert isinstance(result, ParseResult)
    assert "Mineru" in result.markdown
    assert result.images["fig.png"] == b"PNGBYTES"
    # the images/ prefix should be rewritten to the bare filename for localize_images
    assert "images/fig.png" not in result.markdown
    assert "![p](fig.png)" in result.markdown
