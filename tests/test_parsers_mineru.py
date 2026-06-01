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
    # _result_from_zip no longer rewrites links; the raw 'images/fig.png' survives
    assert "images/fig.png" in result.markdown
    # localize_images (which now rewrites by basename) canonicalizes it
    from openkb.images import localize_images
    md2 = localize_images(result.markdown, result.images, "d", tmp_path / "imgs")
    assert "sources/images/d/fig.png" in md2


def test_cloud_flow_polls_then_downloads(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_API_KEY", "key")
    monkeypatch.setattr("openkb.parsers.mineru.time.sleep", lambda *a, **k: None)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "# Cloud")
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
    # drove the full poll loop: running once, then done
    assert _get.calls == 2


def test_poll_interval_zero_is_clamped_to_positive():
    from openkb.parsers.mineru import MineruParser
    assert MineruParser({"poll_interval": 0}).poll_interval > 0
    assert MineruParser({"poll_interval": -5}).poll_interval > 0
    assert MineruParser({"poll_interval": 2}).poll_interval == 2


def test_result_from_zip_does_not_rewrite_links(tmp_path):
    import io, zipfile
    # The images/ -> bare rewrite moved OUT of _result_from_zip into
    # localize_images; _result_from_zip must leave the markdown link text intact.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "See path other_images/fig.png in text.\n\n![p](images/fig.png)")
        zf.writestr("images/fig.png", b"PNG")
    from openkb.parsers.mineru import _result_from_zip
    result = _result_from_zip(buf.getvalue())
    assert "![p](images/fig.png)" in result.markdown   # link text unchanged
    assert "other_images/fig.png" in result.markdown    # unrelated prose untouched
    assert result.images["fig.png"] == b"PNG"           # images keyed by basename


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


def test_timeout_invalid_is_clamped():
    from openkb.parsers.mineru import MineruParser
    assert MineruParser({"timeout": 0}).timeout == 600
    assert MineruParser({"timeout": "x"}).timeout == 600
    assert MineruParser({"timeout": 30}).timeout == 30


def test_cloud_api_error_envelope_raises(monkeypatch, tmp_path):
    import sys, types
    from unittest.mock import MagicMock
    monkeypatch.setenv("MINERU_API_KEY", "key")
    r = MagicMock(); r.raise_for_status = MagicMock()
    r.json.return_value = {"code": -10001, "msg": "token expired", "data": None}
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client); client.__exit__ = MagicMock(return_value=False)
    client.post.return_value = r
    httpx_mod = types.ModuleType("httpx"); httpx_mod.Client = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "httpx", httpx_mod)
    from openkb.parsers.mineru import MineruParser
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    import pytest
    with pytest.raises(RuntimeError) as exc:
        MineruParser({"mode": "cloud"}).parse(src)
    assert "token expired" in str(exc.value) or "-10001" in str(exc.value)


def test_cloud_empty_file_urls_raises(monkeypatch, tmp_path):
    import sys, types
    from unittest.mock import MagicMock
    monkeypatch.setenv("MINERU_API_KEY", "key")
    r = MagicMock(); r.raise_for_status = MagicMock()
    r.json.return_value = {"code": 0, "data": {"batch_id": "b1", "file_urls": []}}
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client); client.__exit__ = MagicMock(return_value=False)
    client.post.return_value = r
    httpx_mod = types.ModuleType("httpx"); httpx_mod.Client = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "httpx", httpx_mod)
    from openkb.parsers.mineru import MineruParser
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    import pytest
    with pytest.raises(RuntimeError) as exc:
        MineruParser({"mode": "cloud"}).parse(src)
    assert "upload URL" in str(exc.value)


def test_full_md_basename_preferred_over_endswith(tmp_path):
    import io, zipfile
    from openkb.parsers.mineru import _result_from_zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("careful.md", "# WRONG")     # ends with 'full.md' but isn't it
        zf.writestr("full.md", "# RIGHT")
    result = _result_from_zip(buf.getvalue())
    assert "RIGHT" in result.markdown
    assert "WRONG" not in result.markdown


def test_image_basename_collision_warns(tmp_path, caplog):
    import io, zipfile, logging as _logging
    from openkb.parsers.mineru import _result_from_zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "# x")
        zf.writestr("images/fig.png", b"A")
        zf.writestr("sub/fig.png", b"B")
    with caplog.at_level(_logging.WARNING):
        result = _result_from_zip(buf.getvalue())
    assert any("fig.png" in r.message for r in caplog.records)
