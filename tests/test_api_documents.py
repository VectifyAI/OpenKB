"""Tests for the document-source REST endpoint (read a doc's ingested text).

Mirrors the helpers/patterns in tests/test_api.py. The endpoint resolves a
document hash to the converted full text under wiki/sources/ — short docs are
``<doc_name>.md``; long docs are ``<doc_name>.json`` (a per-page
``list[{page, content, images}]``).
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from openkb.api import create_app


def _client(monkeypatch, token: str | None = "secret") -> TestClient:
    if token is None:
        monkeypatch.delenv("OPENKB_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("OPENKB_API_TOKEN", token)
    return TestClient(create_app())


def _auth(token: str = "secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _use_named_kb(monkeypatch, kb_dir, name: str = "test-kb") -> str:
    def resolve(kb):
        assert kb == name
        return kb_dir

    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", resolve)
    return name


def _write_hashes(kb_dir, hashes: dict) -> None:
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes), encoding="utf-8")


def test_document_source_short_md(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(kb_dir, {"h1": {"name": "notes.md", "doc_name": "notes", "type": "md"}})
    (kb_dir / "wiki" / "sources" / "notes.md").write_text("# Notes\n\nBody text.", encoding="utf-8")

    resp = client.post("/api/v1/document/source", json={"kb": kb, "hash": "h1"}, headers=_auth())

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["content"] == "# Notes\n\nBody text."
    assert payload["format"] == "markdown"
    assert payload["pages"] is None
    assert payload["name"] == "notes.md"


def test_document_source_long_json_concatenates_pages(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(kb_dir, {"h2": {"name": "paper.pdf", "doc_name": "paper", "type": "long_pdf"}})
    pages = [
        {"page": 1, "content": "Page one text.", "images": []},
        {"page": 2, "content": "Page two text.", "images": []},
    ]
    (kb_dir / "wiki" / "sources" / "paper.json").write_text(json.dumps(pages), encoding="utf-8")

    resp = client.post("/api/v1/document/source", json={"kb": kb, "hash": "h2"}, headers=_auth())

    assert resp.status_code == 200
    payload = resp.json()
    assert "Page one text." in payload["content"]
    assert "Page two text." in payload["content"]
    # page boundaries preserved via a thematic break
    assert "---" in payload["content"]
    assert payload["pages"] == 2


def test_document_source_prefers_stored_source_path(monkeypatch, kb_dir):
    """When metadata carries source_path, it wins over the doc_name convention."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(
        kb_dir,
        {
            "h3": {
                "name": "report.txt",
                "doc_name": "report",
                "type": "md",
                "source_path": "wiki/sources/report.md",
            }
        },
    )
    (kb_dir / "wiki" / "sources" / "report.md").write_text("Converted.", encoding="utf-8")

    resp = client.post("/api/v1/document/source", json={"kb": kb, "hash": "h3"}, headers=_auth())

    assert resp.status_code == 200
    assert resp.json()["content"] == "Converted."


def test_document_source_unknown_hash_404(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(kb_dir, {})

    resp = client.post("/api/v1/document/source", json={"kb": kb, "hash": "nope"}, headers=_auth())

    assert resp.status_code == 404


def test_document_source_missing_file_404(monkeypatch, kb_dir):
    """Hash is known but the source file was deleted → 404, not a 500."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(kb_dir, {"h4": {"name": "gone.md", "doc_name": "gone", "type": "md"}})

    resp = client.post("/api/v1/document/source", json={"kb": kb, "hash": "h4"}, headers=_auth())

    assert resp.status_code == 404


def test_document_source_requires_auth(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    _use_named_kb(monkeypatch, kb_dir)

    resp = client.post("/api/v1/document/source", json={"kb": "test-kb", "hash": "h1"})

    assert resp.status_code == 401
