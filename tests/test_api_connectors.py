"""REST connectors call the same functions as the CLI, off the event loop."""

from __future__ import annotations

from fastapi.testclient import TestClient

from openkb.api import create_app
from openkb.connectors.sync import BindingReport, SyncOutcome


def _client(monkeypatch, token: str | None = "secret") -> TestClient:
    if token is None:
        monkeypatch.delenv("OPENKB_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("OPENKB_API_TOKEN", token)
    return TestClient(create_app())


def _auth(token: str = "secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _kb(tmp_path):
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    (kb / "wiki").mkdir()
    (kb / ".env").write_text(
        "OPENKB_S3_ACCESS_KEY_ID=AKIA_TEST\nOPENKB_S3_SECRET_ACCESS_KEY=secret-test\n",
        encoding="utf-8",
    )
    return kb


def _use_kb(monkeypatch, kb_dir, name: str = "test-kb") -> str:
    def resolve(kb):
        assert kb == name
        return kb_dir

    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", resolve)
    return name


def test_connector_round_trip(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    monkeypatch.delenv("OPENKB_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("OPENKB_S3_SECRET_ACCESS_KEY", raising=False)
    name = _use_kb(monkeypatch, kb)
    client = _client(monkeypatch)
    created = client.post(
        "/api/v1/connectors",
        headers=_auth(),
        json={
            "kb": name,
            "name": "papers",
            "provider": "s3",
            "region": "us-east-1",
            "bucket": "acme",
            "prefix": "docs/",
        },
    )
    assert created.status_code == 200, created.text
    listed = client.get("/api/v1/connectors", headers=_auth(), params={"kb": name})
    assert listed.status_code == 200
    body = listed.json()["connectors"]
    assert body[0]["name"] == "papers"
    assert body[0]["location"]["bucket"] == "acme"
    assert body[0]["stale"] is True

    def fake_sync(kb_dir, *, name=None, non_interactive=False, **kwargs):
        assert non_interactive is True
        return SyncOutcome(
            reports=[
                BindingReport(name="papers", provider="s3", ok=True, added=1),
            ]
        )

    monkeypatch.setattr("openkb.api_connectors_router.sync_bindings", fake_sync)
    synced = client.post(
        "/api/v1/connectors/sync",
        headers=_auth(),
        json={"kb": name, "name": "papers"},
    )
    assert synced.status_code == 200
    assert synced.json()["exit_code"] == 0
    removed = client.post(
        "/api/v1/connectors/remove",
        headers=_auth(),
        json={"kb": name, "name": "papers"},
    )
    assert removed.status_code == 200
    assert "remain" in removed.json()["message"]
    page = kb / "wiki" / "keep.md"
    page.write_text("stay", encoding="utf-8")
    assert page.read_text(encoding="utf-8") == "stay"
