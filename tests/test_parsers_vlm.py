from __future__ import annotations

from unittest.mock import patch

from openkb.parsers.vlm import VLMParser
from openkb.parsers.base import ParseResult


def test_supports_pdf_only_for_v1():
    p = VLMParser({}, model="gemini/gemini-2.5-pro")
    assert p.supports(".pdf") is True
    assert p.supports(".md") is False
    assert p.supports(".docx") is False


def test_parse_calls_transcribe_with_configured_model(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF")
    p = VLMParser({"model": "gpt-4o"}, model="fallback-model")
    with patch("openkb.parsers.vlm.transcribe_to_markdown", return_value="# MD") as t:
        result = p.parse(src)
    t.assert_called_once_with(src, model="gpt-4o")
    assert isinstance(result, ParseResult)
    assert result.markdown == "# MD"


def test_parse_falls_back_to_global_model(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF")
    p = VLMParser({}, model="global-model")
    with patch("openkb.parsers.vlm.transcribe_to_markdown", return_value="x") as t:
        p.parse(src)
    t.assert_called_once_with(src, model="global-model")
