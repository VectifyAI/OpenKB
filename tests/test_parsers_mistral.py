from __future__ import annotations

import base64
import sys
import types
from unittest.mock import MagicMock

import pytest

from openkb.parsers.base import ParseResult


def _install_fake_mistralai(monkeypatch, client_instance):
    mod = types.ModuleType("mistralai")
    mod.Mistral = MagicMock(return_value=client_instance)
    monkeypatch.setitem(sys.modules, "mistralai", mod)
    return mod


def test_supports_pdf():
    from openkb.parsers.mistral import MistralParser
    p = MistralParser({})
    assert p.supports(".pdf") is True
    assert p.supports(".docx") is False


def test_missing_key_raises_actionable(monkeypatch, tmp_path):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    from openkb.parsers.mistral import MistralParser
    p = MistralParser({})
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    with pytest.raises(RuntimeError) as exc:
        p.parse(src)
    assert "MISTRAL_API_KEY" in str(exc.value)


def test_parse_collects_markdown_and_decodes_images(monkeypatch, tmp_path):
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    img_bytes = b"IMGDATA"
    img_b64 = base64.b64encode(img_bytes).decode()

    client = MagicMock()
    client.files.upload.return_value = MagicMock(id="file-1")
    client.files.get_signed_url.return_value = MagicMock(url="https://signed")
    page = MagicMock()
    page.markdown = "Text ![img-0.png](img-0.png)"
    page.images = [MagicMock(id="img-0.png", image_base64=f"data:image/png;base64,{img_b64}")]
    client.ocr.process.return_value = MagicMock(pages=[page])

    _install_fake_mistralai(monkeypatch, client)
    from openkb.parsers.mistral import MistralParser
    p = MistralParser({})
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    result = p.parse(src)

    assert isinstance(result, ParseResult)
    assert "img-0.png" in result.markdown
    assert result.images["img-0.png"] == img_bytes


def test_missing_package_raises_install_hint(monkeypatch, tmp_path):
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    monkeypatch.setitem(sys.modules, "mistralai", None)  # force ImportError
    from openkb.parsers.mistral import MistralParser
    p = MistralParser({})
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    with pytest.raises(RuntimeError) as exc:
        p.parse(src)
    assert "openkb[mistral]" in str(exc.value)
