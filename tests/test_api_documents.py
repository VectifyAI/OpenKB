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
    assert payload["page"] == 1
    assert payload["total_pages"] == 1
    assert payload["name"] == "notes.md"


def test_document_source_long_json_returns_first_page_by_default(monkeypatch, kb_dir):
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
    # Default page=1 returns ONLY the first page (not the concatenated whole).
    assert payload["content"] == "Page one text."
    assert "Page two text." not in payload["content"]
    assert payload["page"] == 1
    assert payload["total_pages"] == 2


def test_document_source_long_json_paginates_by_page(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(kb_dir, {"h2": {"name": "paper.pdf", "doc_name": "paper", "type": "long_pdf"}})
    pages = [
        {"page": 1, "content": "Page one text.", "images": []},
        {"page": 2, "content": "Page two text.", "images": []},
        {"page": 3, "content": "Page three text.", "images": []},
    ]
    (kb_dir / "wiki" / "sources" / "paper.json").write_text(json.dumps(pages), encoding="utf-8")

    resp = client.post(
        "/api/v1/document/source", json={"kb": kb, "hash": "h2", "page": 2}, headers=_auth()
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["content"] == "Page two text."
    assert payload["page"] == 2
    assert payload["total_pages"] == 3


def test_document_source_page_clamped_when_out_of_range(monkeypatch, kb_dir):
    """A page past the end clamps to the last page (no 404), so a stale client
    request after a recompile shrinks the doc degrades gracefully."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(kb_dir, {"h2": {"name": "paper.pdf", "doc_name": "paper", "type": "long_pdf"}})
    pages = [{"page": 1, "content": "Only page.", "images": []}]
    (kb_dir / "wiki" / "sources" / "paper.json").write_text(json.dumps(pages), encoding="utf-8")

    resp = client.post(
        "/api/v1/document/source", json={"kb": kb, "hash": "h2", "page": 99}, headers=_auth()
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["page"] == 1
    assert payload["total_pages"] == 1
    assert payload["content"] == "Only page."


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


def test_document_source_doc_name_collision_resolves_by_type(monkeypatch, kb_dir):
    """Two docs share a doc_name (short .md + long .json), and the long entry has
    no source_path. Each hash must resolve to its OWN file via the type ordering,
    not whichever extension is tried first."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(
        kb_dir,
        {
            "short": {"name": "dup.md", "doc_name": "dup", "type": "md"},
            "long": {"name": "dup.pdf", "doc_name": "dup", "type": "long_pdf"},
        },
    )
    (kb_dir / "wiki" / "sources" / "dup.md").write_text("SHORT content", encoding="utf-8")
    (kb_dir / "wiki" / "sources" / "dup.json").write_text(
        json.dumps([{"page": 1, "content": "LONG page one", "images": []}]), encoding="utf-8"
    )

    long_resp = client.post(
        "/api/v1/document/source", json={"kb": kb, "hash": "long"}, headers=_auth()
    )
    assert long_resp.status_code == 200
    assert "LONG page one" in long_resp.json()["content"]
    assert "SHORT content" not in long_resp.json()["content"]
    assert long_resp.json()["total_pages"] == 1

    short_resp = client.post(
        "/api/v1/document/source", json={"kb": kb, "hash": "short"}, headers=_auth()
    )
    assert short_resp.json()["content"] == "SHORT content"


def test_document_source_malformed_json_is_controlled_500(monkeypatch, kb_dir):
    """A corrupt/unexpected source JSON returns a controlled 500 with a clean
    message, not an unhandled exception."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(kb_dir, {"h": {"name": "broken.pdf", "doc_name": "broken", "type": "long_pdf"}})
    (kb_dir / "wiki" / "sources" / "broken.json").write_text("{not valid json", encoding="utf-8")

    resp = client.post("/api/v1/document/source", json={"kb": kb, "hash": "h"}, headers=_auth())

    assert resp.status_code == 500
    assert "Could not read" in resp.json()["detail"]


def test_document_source_non_list_json_is_controlled_500(monkeypatch, kb_dir):
    """A source JSON that parses but is not a page list is rejected cleanly."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _write_hashes(kb_dir, {"h": {"name": "odd.pdf", "doc_name": "odd", "type": "long_pdf"}})
    (kb_dir / "wiki" / "sources" / "odd.json").write_text('{"pages": 3}', encoding="utf-8")

    resp = client.post("/api/v1/document/source", json={"kb": kb, "hash": "h"}, headers=_auth())

    assert resp.status_code == 500


def test_document_source_requires_auth(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    _use_named_kb(monkeypatch, kb_dir)

    resp = client.post("/api/v1/document/source", json={"kb": "test-kb", "hash": "h1"})

    assert resp.status_code == 401
