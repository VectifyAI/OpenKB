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


def test_warns_when_falling_back_to_global_model(caplog):
    import logging as _logging
    with caplog.at_level(_logging.WARNING):
        VLMParser({}, model="gpt-5.4-mini")
    assert any("parsers.vlm.model" in r.message for r in caplog.records)


def test_no_warning_when_vlm_model_set(caplog):
    import logging as _logging
    with caplog.at_level(_logging.WARNING):
        VLMParser({"model": "gemini/gemini-2.5-pro"}, model="gpt-5.4-mini")
    assert not any("parsers.vlm.model" in r.message for r in caplog.records)


def test_parse_warns_text_only(tmp_path, caplog):
    import logging as _logging
    from unittest.mock import patch
    src = tmp_path / "d.pdf"; src.write_bytes(b"%PDF")
    p = VLMParser({"model": "gemini/gemini-2.5-pro"})
    with patch("openkb.parsers.vlm.transcribe_to_markdown", return_value="# md"):
        with caplog.at_level(_logging.WARNING):
            p.parse(src)
    assert any("text only" in r.message for r in caplog.records)
