"""Google Drive binding, loopback sign-in, and sync. No live Google account."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from openkb.connectors.backend import ProviderError, RemoteObject, TransientError
from openkb.connectors.gdrive import (
    GDriveBackend,
    _load_google,
    access_token_for,
    prepare_gdrive_binding,
)
from openkb.connectors.model import parse_gdrive_binding, parse_gdrive_folder
from openkb.connectors.oauth import (
    LOOPBACK_HOST,
    LOOPBACK_PORT,
    SIGN_IN_TIMEOUT_SECONDS,
    AuthRejected,
    save_refresh_token,
    sign_in,
)
from openkb.connectors.sync import sync_bindings
from openkb.locks import kb_ingest_lock_held, kb_lock


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
        "OPENKB_GDRIVE_CLIENT_ID",
        "OPENKB_GDRIVE_CLIENT_SECRET",
        "OPENKB_GDRIVE_REFRESH_TOKEN",
        "OPENKB_S3_ACCESS_KEY_ID",
        "OPENKB_S3_SECRET_ACCESS_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _client_env(kb: Path, refresh: str = "") -> None:
    lines = "OPENKB_GDRIVE_CLIENT_ID=client\nOPENKB_GDRIVE_CLIENT_SECRET=secret\n"
    if refresh:
        lines += f"OPENKB_GDRIVE_REFRESH_TOKEN={refresh}\n"
    (kb / ".env").write_text(lines, encoding="utf-8")


def _free_port() -> int:
    server = HTTPServer((LOOPBACK_HOST, 0), BaseHTTPRequestHandler)
    port = int(server.server_address[1])
    server.server_close()
    return port


def test_folder_url_and_raw_id():
    url = "https://drive.google.com/drive/folders/1AbCdefghij?usp=sharing"
    assert parse_gdrive_folder(url) == "1AbCdefghij"
    assert parse_gdrive_folder("1AbCdefghij") == "1AbCdefghij"
    with pytest.raises(ValueError):
        parse_gdrive_folder("not a folder")


def test_sign_in_constants():
    assert LOOPBACK_HOST == "127.0.0.1"
    assert LOOPBACK_PORT == 7567
    assert SIGN_IN_TIMEOUT_SECONDS == 300


def test_sign_in_refuses_a_public_bind():
    with pytest.raises(ProviderError, match="127.0.0.1"):
        sign_in(
            authorize_url="https://example.invalid/auth",
            token_url="https://example.invalid/token",
            client_id="client",
            client_secret="secret",
            scopes="scope",
            redirect_uri="http://127.0.0.1:9/callback",
            host="0.0.0.0",
            port=9,
            timeout=0.1,
        )


def test_sign_in_port_in_use():
    port = _free_port()
    holder = HTTPServer((LOOPBACK_HOST, port), BaseHTTPRequestHandler)
    try:
        with pytest.raises(ProviderError, match=f"port {port} is already in use"):
            sign_in(
                authorize_url="https://example.invalid/auth",
                token_url="https://example.invalid/token",
                client_id="client",
                client_secret="secret",
                scopes="scope",
                redirect_uri=f"http://127.0.0.1:{port}/callback",
                port=port,
                timeout=0.2,
            )
    finally:
        holder.server_close()


def test_sign_in_times_out():
    port = _free_port()
    with pytest.raises(ProviderError, match="timed out"):
        sign_in(
            authorize_url="https://example.invalid/auth",
            token_url="https://example.invalid/token",
            client_id="client",
            client_secret="secret",
            scopes="scope",
            redirect_uri=f"http://127.0.0.1:{port}/callback",
            port=port,
            timeout=0.2,
        )


def test_sign_in_checks_state_and_pkce():
    callback_port = _free_port()
    token_box: dict[str, str] = {}

    class _Token(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            parsed = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            token_box.update({key: values[0] for key, values in parsed.items()})
            body = json.dumps({"refresh_token": "refresh-1", "access_token": "access-1"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    token_server = HTTPServer((LOOPBACK_HOST, 0), _Token)
    token_port = int(token_server.server_address[1])
    thread = threading.Thread(target=token_server.serve_forever, daemon=True)
    thread.start()

    def announce(url: str) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        state = query["state"][0]
        challenge = query["code_challenge"][0]
        token_box["challenge"] = challenge

        def hit() -> None:
            urllib.request.urlopen(
                f"http://localhost:{callback_port}/callback?code=the-code&state={state}",
                timeout=2,
            )

        threading.Thread(target=hit, daemon=True).start()

    try:
        refresh = sign_in(
            authorize_url="https://example.invalid/auth",
            token_url=f"http://127.0.0.1:{token_port}/token",
            client_id="client",
            client_secret="secret",
            scopes="scope",
            redirect_uri=f"http://localhost:{callback_port}/callback",
            port=callback_port,
            timeout=3,
            announce=announce,
        )
    finally:
        token_server.shutdown()
        token_server.server_close()

    assert refresh == "refresh-1"
    assert token_box["code"] == "the-code"
    assert token_box["code_verifier"]
    import base64
    import hashlib

    digest = hashlib.sha256(token_box["code_verifier"].encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    assert token_box["challenge"] == expected


def test_refresh_token_is_written_only_to_the_kb_env(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setattr("openkb.connectors.credentials.GLOBAL_CONFIG_DIR", global_dir)
    save_refresh_token(kb, "OPENKB_GDRIVE_REFRESH_TOKEN", "refresh-1")
    text = (kb / ".env").read_text(encoding="utf-8")
    assert "OPENKB_GDRIVE_REFRESH_TOKEN=refresh-1" in text
    assert (kb / ".env").stat().st_mode & 0o777 == 0o600
    assert not (global_dir / ".env").exists()


def test_non_interactive_add_does_not_open_sign_in_or_save(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb)
    monkeypatch.delenv("OPENKB_GDRIVE_REFRESH_TOKEN", raising=False)
    called: list[str] = []
    with pytest.raises(ProviderError, match="Sign-in is required"):
        prepare_gdrive_binding(
            kb,
            name="notes",
            folder="1AbCdefghij",
            non_interactive=True,
            sign_in_hook=lambda: called.append("opened") or "nope",
            refresh=lambda _token: ("access", None),
        )
    assert called == []
    assert not (kb / ".openkb" / "config.yaml").exists()


def test_rejected_refresh_signs_in_once_then_saves(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb, refresh="old-token")
    monkeypatch.delenv("OPENKB_GDRIVE_REFRESH_TOKEN", raising=False)
    calls: list[str] = []

    def refresh(token: str) -> tuple[str, str | None]:
        if token == "old-token":
            raise AuthRejected("rejected")
        return "access", None

    def hook() -> str:
        calls.append("opened")
        assert not kb_ingest_lock_held(kb / ".openkb")
        return "new-token"

    message = prepare_gdrive_binding(
        kb,
        name="notes",
        folder="https://drive.google.com/drive/folders/1AbCdefghij",
        non_interactive=False,
        sign_in_hook=hook,
        refresh=refresh,
    )
    assert "saved" in message
    assert calls == ["opened"]
    saved = (kb / ".env").read_text(encoding="utf-8")
    assert "OPENKB_GDRIVE_REFRESH_TOKEN=new-token" in saved
    assert "secret" not in (kb / ".openkb" / "config.yaml").read_text(encoding="utf-8")


def test_sign_in_does_not_hold_the_ingest_lock(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb)
    monkeypatch.delenv("OPENKB_GDRIVE_REFRESH_TOKEN", raising=False)
    started = threading.Event()
    release = threading.Event()

    def hook() -> str:
        started.set()
        assert release.wait(2)
        return "new-token"

    def run() -> None:
        access_token_for(
            kb,
            non_interactive=False,
            sign_in_hook=hook,
            refresh=lambda _token: ("access", None),
        )

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(2)
    with kb_lock(kb / ".openkb", exclusive=True, timeout=1):
        release.set()
    worker.join(2)
    assert not worker.is_alive()


def test_drive_sync_keeps_s3_when_sign_in_fails(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb)
    monkeypatch.delenv("OPENKB_GDRIVE_REFRESH_TOKEN", raising=False)
    (kb / ".env").write_text(
        (kb / ".env").read_text(encoding="utf-8")
        + "OPENKB_S3_ACCESS_KEY_ID=AKIA\nOPENKB_S3_SECRET_ACCESS_KEY=secret-s3\n",
        encoding="utf-8",
    )
    from openkb.connectors.model import parse_s3_binding
    from openkb.connectors.store import upsert_binding

    upsert_binding(
        kb, parse_s3_binding(name="papers", region="us-east-1", bucket="acme", prefix="")
    )
    upsert_binding(kb, parse_gdrive_binding(name="notes", folder="1AbCdefghij"))

    def fail() -> str:
        raise ProviderError("browser closed")

    outcome = sync_bindings(
        kb,
        sign_in=fail,
        add=lambda path, kb_dir, identity: "added",
        backend_for=lambda binding, access, secret: _EmptyBackend(),
        gdrive_backend_for=lambda binding, token: _EmptyBackend(),
    )
    by_name = {item.name: item for item in outcome.reports}
    assert by_name["papers"].ok
    assert by_name["notes"].ok is False
    assert "browser closed" in (by_name["notes"].error or "")


class _EmptyBackend:
    def list_objects(self) -> list[RemoteObject]:
        return []

    def download(self, obj: RemoteObject, dest: Path) -> None:
        dest.write_bytes(b"")


def test_drive_lists_recursively_and_skips_unsupported(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb, refresh="refresh-1")
    monkeypatch.delenv("OPENKB_GDRIVE_REFRESH_TOKEN", raising=False)
    from openkb.connectors.store import upsert_binding

    upsert_binding(kb, parse_gdrive_binding(name="notes", folder="1AbCdefghij"))
    monkeypatch.setattr(
        "openkb.connectors.gdrive.access_token_for",
        lambda *_args, **_kwargs: "access-token",
    )

    class _Files:
        def list_objects(self) -> list[RemoteObject]:
            return [
                RemoteObject("gdrive://1AbCdefghij/a.txt", "a.txt", "a"),
                RemoteObject("gdrive://1AbCdefghij/c.bin", "c.bin", "bin"),
                RemoteObject("gdrive://1AbCdefghij/sub/b.pdf", "b.pdf", "b"),
            ]

        def download(self, obj: RemoteObject, dest: Path) -> None:
            dest.write_text(obj.locator, encoding="utf-8")

    seen: list[str] = []

    def add(path, kb_dir, identity) -> str:
        seen.append(identity)
        assert not kb_ingest_lock_held(kb_dir / ".openkb")
        return "added"

    outcome = sync_bindings(
        kb,
        add=add,
        gdrive_backend_for=lambda binding, token: _Files(),
    )
    assert outcome.exit_code == 0
    report = outcome.reports[0]
    assert report.added == 2
    assert report.skipped == 1
    assert seen == ["gdrive://1AbCdefghij/a.txt", "gdrive://1AbCdefghij/sub/b.pdf"]


def test_listing_shape_uses_the_bound_folder_id():
    class _Files:
        def list_folder(self, folder_id: str) -> list[dict[str, object]]:
            if folder_id == "root-folder":
                return [{"id": "sub", "name": "sub", "folder": True}]
            return [{"id": "file", "name": "notes.txt", "folder": False}]

        def download(self, file_id: str, dest: Path) -> None:
            dest.write_text(file_id, encoding="utf-8")

    binding = parse_gdrive_binding(name="notes", folder="root-folder")
    backend = GDriveBackend(binding, "access", client=_Files())  # type: ignore[arg-type]
    assert backend.list_objects() == [
        RemoteObject(
            identity="gdrive://root-folder/sub/notes.txt",
            name="notes.txt",
            locator="file",
        )
    ]


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


def test_missing_google_names_the_extra():
    try:
        import googleapiclient  # noqa: F401
    except ImportError:
        with pytest.raises(ProviderError, match=r"openkb\[gdrive\]"):
            _load_google()
    else:
        pytest.skip("google-api-python-client is installed in this environment")


def test_bad_folder_does_not_sign_in(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb, refresh="refresh-1")
    called: list[str] = []
    with pytest.raises(ValueError, match="folder"):
        prepare_gdrive_binding(
            kb,
            name="notes",
            folder="bad id",
            non_interactive=False,
            sign_in_hook=lambda: called.append("opened") or "token",
            refresh=lambda _token: ("access", None),
        )
    assert called == []
    assert not (kb / ".openkb" / "config.yaml").exists()


def test_cli_non_interactive_add_does_not_save(tmp_path, monkeypatch):
    from click.testing import CliRunner

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
            "gdrive",
            "--name",
            "notes",
            "--folder",
            "1AbCdefghij",
            "--non-interactive",
        ],
    )
    assert result.exit_code != 0
    assert "Sign-in is required" in result.output
    assert not (kb / ".openkb" / "config.yaml").exists()


def test_api_add_is_non_interactive(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from openkb.api import create_app

    _isolate(monkeypatch, tmp_path)
    kb = _kb(tmp_path)
    _client_env(kb)
    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", lambda name: kb)
    monkeypatch.setenv("OPENKB_API_TOKEN", "secret")
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/connectors",
        headers={"Authorization": "Bearer secret"},
        json={"kb": "demo", "name": "notes", "provider": "gdrive", "folder": "1AbCdefghij"},
    )
    assert response.status_code == 400
    assert "Sign-in is required" in response.text
    assert not (kb / ".openkb" / "config.yaml").exists()
