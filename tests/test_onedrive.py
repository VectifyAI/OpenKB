"""OneDrive binding and sync. No live Microsoft account."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from openkb.connectors.backend import ProviderError, RemoteObject, TransientError
from openkb.connectors.model import parse_onedrive_binding, parse_onedrive_path
from openkb.connectors.oauth import LOOPBACK_PORT, AuthRejected
from openkb.connectors.onedrive import (
    LOOPBACK_HOST,
    ONEDRIVE_REDIRECT_URI,
    OneDriveBackend,
    _load_msal,
    access_token_for,
    download_graph_file,
    prepare_onedrive_binding,
)
from openkb.connectors.sync import sync_bindings
from openkb.locks import kb_ingest_lock_held


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    (kb / "wiki").mkdir()
    (kb / "raw").mkdir()
    return kb


def _isolate(monkeypatch, tmp_path: Path) -> None:
    global_dir = tmp_path / "global-config"
    global_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("openkb.connectors.credentials.GLOBAL_CONFIG_DIR", global_dir)
    for key in (
        "OPENKB_ONEDRIVE_CLIENT_ID",
        "OPENKB_ONEDRIVE_CLIENT_SECRET",
        "OPENKB_ONEDRIVE_REFRESH_TOKEN",
        "OPENKB_S3_ACCESS_KEY_ID",
        "OPENKB_S3_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _client_env(kb: Path, refresh: str = "") -> None:
    lines = "OPENKB_ONEDRIVE_CLIENT_ID=client\nOPENKB_ONEDRIVE_CLIENT_SECRET=secret\n"
    if refresh:
        lines += f"OPENKB_ONEDRIVE_REFRESH_TOKEN={refresh}\n"
    (kb / ".env").write_text(lines, encoding="utf-8")


def test_redirect_uri_uses_localhost_while_the_listener_stays_loopback():
    assert ONEDRIVE_REDIRECT_URI == "http://localhost:7567/callback"
    assert LOOPBACK_HOST == "127.0.0.1"
    assert LOOPBACK_PORT == 7567


def test_path_rejects_root_and_parent_segments():
    assert parse_onedrive_path("Documents/Papers") == "Documents/Papers"
    for bad in ("", "/Documents", "Documents/../Papers", "Documents\\Papers", "Documents//Papers"):
        with pytest.raises(ValueError):
            parse_onedrive_path(bad)


def test_onedrive_module_does_not_import_drive():
    import openkb.connectors.onedrive as onedrive

    text = Path(onedrive.__file__).read_text(encoding="utf-8")
    assert "gdrive" not in text
    assert "connectors.gdrive" not in text


def test_download_strips_authorization_on_a_cross_host_redirect(tmp_path: Path):
    seen: dict[str, str | None] = {}

    class _File(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen["auth"] = self.headers.get("Authorization")
            body = b"pdf-bytes"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    class _Redirect(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{file_port}/file")
            self.end_headers()

        def log_message(self, fmt: str, *args: object) -> None:
            return

    file_server = HTTPServer(("127.0.0.1", 0), _File)
    file_port = int(file_server.server_address[1])
    redirect_server = HTTPServer(("127.0.0.1", 0), _Redirect)
    redirect_port = int(redirect_server.server_address[1])
    threads = [
        threading.Thread(target=file_server.serve_forever, daemon=True),
        threading.Thread(target=redirect_server.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()
    dest = tmp_path / "a.pdf"
    try:
        download_graph_file(
            f"http://127.0.0.1:{redirect_port}/start",
            "secret-token",
            dest,
        )
    finally:
        redirect_server.shutdown()
        file_server.shutdown()
        redirect_server.server_close()
        file_server.server_close()
    assert seen["auth"] is None
    assert dest.read_bytes() == b"pdf-bytes"


def test_non_interactive_add_does_not_save(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb)
    called: list[str] = []
    with pytest.raises(ProviderError, match="Sign-in is required"):
        prepare_onedrive_binding(
            kb,
            name="papers",
            path="Documents/Papers",
            non_interactive=True,
            sign_in_hook=lambda: called.append("opened") or "nope",
            refresh=lambda _token: ("access", None),
        )
    assert called == []
    assert not (kb / ".openkb" / "config.yaml").exists()


def test_rejected_refresh_signs_in_once(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb, refresh="old-token")
    calls: list[str] = []

    def refresh(token: str) -> tuple[str, str | None]:
        if token == "old-token":
            raise AuthRejected("rejected")
        return "access", None

    message = prepare_onedrive_binding(
        kb,
        name="papers",
        path="Documents/Papers",
        non_interactive=False,
        sign_in_hook=lambda: calls.append("opened") or "new-token",
        refresh=refresh,
    )
    assert "saved" in message
    assert calls == ["opened"]
    saved = (kb / ".env").read_text(encoding="utf-8")
    assert "OPENKB_ONEDRIVE_REFRESH_TOKEN=new-token" in saved
    assert "secret" not in (kb / ".openkb" / "config.yaml").read_text(encoding="utf-8")


def test_listing_uses_the_path_from_the_drive_root():
    class _Items:
        def list_children(self, folder_path: str) -> list[dict[str, object]]:
            if folder_path == "Documents/Papers":
                return [
                    {"name": "sub", "folder": True},
                    {"name": "a.txt", "folder": False},
                    {"name": "c.bin", "folder": False},
                ]
            return [{"name": "b.pdf", "folder": False}]

        def download(self, file_path: str, dest: Path) -> None:
            dest.write_text(file_path, encoding="utf-8")

    binding = parse_onedrive_binding(name="papers", path="Documents/Papers")
    backend = OneDriveBackend(binding, "access", client=_Items())  # type: ignore[arg-type]
    assert backend.list_objects() == [
        RemoteObject(
            "onedrive://Documents/Papers/sub/b.pdf",
            "b.pdf",
            "Documents/Papers/sub/b.pdf",
        ),
        RemoteObject(
            "onedrive://Documents/Papers/a.txt",
            "a.txt",
            "Documents/Papers/a.txt",
        ),
        RemoteObject(
            "onedrive://Documents/Papers/c.bin",
            "c.bin",
            "Documents/Papers/c.bin",
        ),
    ]


def test_sync_skips_unsupported_and_keeps_s3(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb)
    (kb / ".env").write_text(
        (kb / ".env").read_text(encoding="utf-8")
        + "OPENKB_S3_ACCESS_KEY_ID=AKIA\nOPENKB_S3_SECRET_ACCESS_KEY=secret-s3\n",
        encoding="utf-8",
    )
    from openkb.connectors.model import parse_s3_binding
    from openkb.connectors.store import upsert_binding

    upsert_binding(
        kb, parse_s3_binding(name="bucket", region="us-east-1", bucket="acme", prefix="")
    )
    upsert_binding(kb, parse_onedrive_binding(name="papers", path="Documents/Papers"))
    monkeypatch.setattr(
        "openkb.connectors.onedrive.access_token_for",
        lambda *_args, **_kwargs: "access-token",
    )

    class _Files:
        def list_objects(self) -> list[RemoteObject]:
            return [
                RemoteObject("onedrive://Documents/Papers/a.txt", "a.txt", "a"),
                RemoteObject("onedrive://Documents/Papers/c.bin", "c.bin", "c"),
            ]

        def download(self, obj: RemoteObject, dest: Path) -> None:
            dest.write_text(obj.locator, encoding="utf-8")
            assert not kb_ingest_lock_held(kb / ".openkb")

    class _Empty:
        def list_objects(self) -> list[RemoteObject]:
            return []

        def download(self, obj: RemoteObject, dest: Path) -> None:
            dest.write_bytes(b"")

    outcome = sync_bindings(
        kb,
        add=lambda path, kb_dir, identity: "added",
        backend_for=lambda binding, access, secret: _Empty(),
        onedrive_backend_for=lambda binding, token: _Files(),
    )
    by_name = {item.name: item for item in outcome.reports}
    assert by_name["bucket"].ok
    assert by_name["papers"].added == 1
    assert by_name["papers"].skipped == 1


def test_sign_in_failure_does_not_stop_s3(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb)
    (kb / ".env").write_text(
        (kb / ".env").read_text(encoding="utf-8")
        + "OPENKB_S3_ACCESS_KEY_ID=AKIA\nOPENKB_S3_SECRET_ACCESS_KEY=secret-s3\n",
        encoding="utf-8",
    )
    from openkb.connectors.model import parse_s3_binding
    from openkb.connectors.store import upsert_binding

    upsert_binding(
        kb, parse_s3_binding(name="bucket", region="us-east-1", bucket="acme", prefix="")
    )
    upsert_binding(kb, parse_onedrive_binding(name="papers", path="Documents/Papers"))

    class _Empty:
        def list_objects(self) -> list[RemoteObject]:
            return []

        def download(self, obj: RemoteObject, dest: Path) -> None:
            dest.write_bytes(b"")

    def fail() -> str:
        raise ProviderError("browser closed")

    outcome = sync_bindings(
        kb,
        sign_in=fail,
        add=lambda path, kb_dir, identity: "added",
        backend_for=lambda binding, access, secret: _Empty(),
    )
    by_name = {item.name: item for item in outcome.reports}
    assert by_name["bucket"].ok
    assert "browser closed" in (by_name["papers"].error or "")


def test_refresh_gives_up_after_three_transient_failures(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb, refresh="refresh-1")
    calls = {"n": 0}

    def refresh(_token: str) -> tuple[str, str | None]:
        calls["n"] += 1
        raise TransientError("HTTP 503")

    with pytest.raises(ProviderError, match="503"):
        access_token_for(kb, non_interactive=True, refresh=refresh)
    assert calls["n"] == 3


def test_missing_msal_names_the_extra():
    try:
        import msal  # noqa: F401
    except ImportError:
        with pytest.raises(ProviderError, match=r"openkb\[onedrive\]"):
            _load_msal()
    else:
        pytest.skip("msal is installed in this environment")


def test_cli_and_api_do_not_open_a_browser(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from fastapi.testclient import TestClient

    from openkb.api import create_app
    from openkb.cli import cli

    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb)
    monkeypatch.chdir(kb)
    result = CliRunner().invoke(
        cli,
        [
            "source",
            "add",
            "onedrive",
            "--name",
            "papers",
            "--path",
            "Documents/Papers",
            "--non-interactive",
        ],
    )
    assert result.exit_code != 0
    assert "Sign-in is required" in result.output
    assert not (kb / ".openkb" / "config.yaml").exists()

    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", lambda name: kb)
    monkeypatch.setenv("OPENKB_API_TOKEN", "secret")
    response = TestClient(create_app()).post(
        "/api/v1/connectors",
        headers={"Authorization": "Bearer secret"},
        json={
            "kb": "demo",
            "name": "papers",
            "provider": "onedrive",
            "path": "Documents/Papers",
        },
    )
    assert response.status_code == 400
    assert "Sign-in is required" in response.text
    assert not (kb / ".openkb" / "config.yaml").exists()
