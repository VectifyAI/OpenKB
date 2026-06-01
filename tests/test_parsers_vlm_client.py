"""Tests for the reusable litellm vision client."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from openkb.parsers.vlm_client import transcribe_to_markdown


def _fake_response(text):
    resp = MagicMock()
    resp.choices = [MagicMock(message=MagicMock(content=text))]
    return resp


def test_transcribe_pdf_sends_data_uri_and_returns_content(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 data")
    with patch("openkb.parsers.vlm_client.litellm.completion",
               return_value=_fake_response("# Parsed")) as comp:
        out = transcribe_to_markdown(src, model="gemini/gemini-2.5-pro")
    assert out == "# Parsed"
    _, kwargs = comp.call_args
    assert kwargs["model"] == "gemini/gemini-2.5-pro"
    content = kwargs["messages"][0]["content"]
    assert any("base64" in str(part) for part in content)


def test_default_model_used_when_none(tmp_path):
    src = tmp_path / "img.png"
    src.write_bytes(b"PNG")
    with patch("openkb.parsers.vlm_client.litellm.completion",
               return_value=_fake_response("desc")) as comp:
        transcribe_to_markdown(src, model=None)
    _, kwargs = comp.call_args
    assert kwargs["model"]  # some non-empty default


def test_pdf_uses_file_content_part(tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"%PDF-1.4 data")
    with patch("openkb.parsers.vlm_client.litellm.completion",
               return_value=_fake_response("x")) as comp:
        transcribe_to_markdown(src, model="some/model")
    content = comp.call_args.kwargs["messages"][0]["content"]
    file_parts = [p for p in content if p.get("type") == "file"]
    assert len(file_parts) == 1
    assert file_parts[0]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert not any(p.get("type") == "image_url" for p in content)


def test_image_uses_image_url_content_part(tmp_path):
    src = tmp_path / "fig.png"
    src.write_bytes(b"\x89PNG\r\n")
    with patch("openkb.parsers.vlm_client.litellm.completion",
               return_value=_fake_response("x")) as comp:
        transcribe_to_markdown(src, model="some/model")
    content = comp.call_args.kwargs["messages"][0]["content"]
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert not any(p.get("type") == "file" for p in content)
