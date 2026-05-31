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


def test_cloud_flow_polls_then_downloads(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_API_KEY", "key")
    monkeypatch.setattr("openkb.parsers.mineru.time.sleep", lambda *a, **k: None)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "# Cloud\n\n![p](images/fig.png)")
        zf.writestr("images/fig.png", b"ZBYTES")
    zip_bytes = buf.getvalue()

    def _resp(json_data=None, content=None):
        r = MagicMock()
        r.raise_for_status = MagicMock()
        if json_data is not None:
            r.json.return_value = json_data
        if content is not None:
            r.content = content
        return r

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post.return_value = _resp(
        json_data={"data": {"batch_id": "b1", "file_urls": ["https://upload"]}}
    )
    client.put.return_value = _resp()

    poll_url = "https://mineru.net/api/v4/extract-results/batch/b1"
    poll_running = _resp(json_data={"data": {"extract_result": [{"state": "running"}]}})
    poll_done = _resp(
        json_data={"data": {"extract_result": [{"state": "done", "full_zip_url": "https://zip"}]}}
    )
    zip_resp = _resp(content=zip_bytes)

    def _get(url, *a, **k):
        if url == "https://zip":
            return zip_resp
        assert url == poll_url
        _get.calls += 1
        return poll_running if _get.calls == 1 else poll_done

    _get.calls = 0
    client.get.side_effect = _get

    httpx_mod = types.ModuleType("httpx")
    httpx_mod.Client = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "httpx", httpx_mod)

    from openkb.parsers.mineru import MineruParser
    p = MineruParser({"mode": "cloud", "poll_interval": 0})
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    result = p.parse(src)

    assert isinstance(result, ParseResult)
    assert "Cloud" in result.markdown
    assert result.images["fig.png"] == b"ZBYTES"
    assert "images/fig.png" not in result.markdown
    assert "![p](fig.png)" in result.markdown
    # drove the full poll loop: running once, then done
    assert _get.calls == 2


def test_poll_interval_zero_is_clamped_to_positive():
    from openkb.parsers.mineru import MineruParser
    assert MineruParser({"poll_interval": 0}).poll_interval > 0
    assert MineruParser({"poll_interval": -5}).poll_interval > 0
    assert MineruParser({"poll_interval": 2}).poll_interval == 2


def test_image_prefix_rewrite_is_anchored(tmp_path):
    import io, sys, types, zipfile
    from unittest.mock import MagicMock
    # markdown has a real image link AND an unrelated 'images/fig.png' substring in prose
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "See path other_images/fig.png in text.\n\n![p](images/fig.png)")
        zf.writestr("images/fig.png", b"PNG")
    from openkb.parsers.mineru import _result_from_zip
    result = _result_from_zip(buf.getvalue())
    assert "![p](fig.png)" in result.markdown          # link rewritten
    assert "other_images/fig.png" in result.markdown    # unrelated prose untouched
    assert result.images["fig.png"] == b"PNG"


def test_cloud_empty_extract_result_then_done(monkeypatch, tmp_path):
    import io, sys, types, zipfile
    from unittest.mock import MagicMock
    monkeypatch.setenv("MINERU_API_KEY", "key")
    monkeypatch.setattr("openkb.parsers.mineru.time.sleep", lambda *a, **k: None)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "# Ok")
    zip_bytes = buf.getvalue()

    def _resp(json_data=None, content=None):
        r = MagicMock(); r.raise_for_status = MagicMock()
        if json_data is not None: r.json.return_value = json_data
        if content is not None: r.content = content
        return r
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client); client.__exit__ = MagicMock(return_value=False)
    client.post.return_value = _resp(json_data={"data": {"batch_id": "b1", "file_urls": ["https://up"]}})
    client.put.return_value = _resp()
    empty = _resp(json_data={"data": {"extract_result": []}})            # queued: empty list
    done = _resp(json_data={"data": {"extract_result": [{"state": "done", "full_zip_url": "https://zip"}]}})
    zipr = _resp(content=zip_bytes)
    def _get(url, *a, **k):
        if url == "https://zip": return zipr
        _get.n += 1
        return empty if _get.n == 1 else done
    _get.n = 0
    client.get.side_effect = _get
    httpx_mod = types.ModuleType("httpx"); httpx_mod.Client = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "httpx", httpx_mod)
    from openkb.parsers.mineru import MineruParser
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    result = MineruParser({"mode": "cloud", "poll_interval": 1}).parse(src)
    assert "Ok" in result.markdown   # survived the empty-list poll without crashing
