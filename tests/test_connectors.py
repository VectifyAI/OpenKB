"""Connector store, credentials, sync, and the S3 backend. No live AWS calls."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from openkb.connectors.backend import ProviderError, RemoteObject, TransientError
from openkb.connectors.credentials import resolve_connector_credential
from openkb.connectors.model import S3Binding, parse_s3_binding, s3_object_identity
from openkb.connectors.s3 import S3Backend, _load_boto3
from openkb.connectors.state import (
    LOCK_TIMEOUT_SECONDS,
    KbBusyError,
    SecretInStateError,
    read_state,
    update_state,
)
from openkb.connectors.store import load_bindings, remove_binding, upsert_binding
from openkb.connectors.sync import recover_auth, sync_bindings
from openkb.locks import kb_ingest_lock, kb_ingest_lock_held
from openkb.state import HashRegistry


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    (kb / "wiki").mkdir()
    (kb / "raw").mkdir()
    (kb / "wiki" / "summaries").mkdir()
    return kb


def _binding(**kwargs) -> S3Binding:
    values = {"name": "papers", "region": "us-east-1", "bucket": "acme", "prefix": "docs/"}
    values.update(kwargs)
    return parse_s3_binding(**values)


def _creds(kb: Path) -> None:
    (kb / ".env").write_text(
        "OPENKB_S3_ACCESS_KEY_ID=AKIA_TEST\nOPENKB_S3_SECRET_ACCESS_KEY=secret-test\n",
        encoding="utf-8",
    )


class _Backend:
    def __init__(self, objects: list[RemoteObject], *, download_failures: int = 0) -> None:
        self.objects = objects
        self.download_failures = download_failures
        self.downloads = 0

    def list_objects(self) -> list[RemoteObject]:
        return list(self.objects)

    def download(self, obj: RemoteObject, dest: Path) -> None:
        self.downloads += 1
        if self.downloads <= self.download_failures:
            raise TransientError("slow down")
        dest.write_bytes(b"bytes:" + obj.identity.encode())


def test_credential_order_is_process_then_kb_then_global(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    global_dir = tmp_path / "global"
    global_dir.mkdir()
    monkeypatch.setattr("openkb.connectors.credentials.GLOBAL_CONFIG_DIR", global_dir)
    (global_dir / ".env").write_text("OPENKB_S3_ACCESS_KEY_ID=from-global\n", encoding="utf-8")
    (kb / ".env").write_text("OPENKB_S3_ACCESS_KEY_ID=from-kb\n", encoding="utf-8")
    monkeypatch.setenv("OPENKB_S3_ACCESS_KEY_ID", "from-env")

    assert resolve_connector_credential(kb, "OPENKB_S3_ACCESS_KEY_ID") == "from-env"
    monkeypatch.delenv("OPENKB_S3_ACCESS_KEY_ID")
    assert resolve_connector_credential(kb, "OPENKB_S3_ACCESS_KEY_ID") == "from-kb"
    (kb / ".env").write_text("", encoding="utf-8")
    assert resolve_connector_credential(kb, "OPENKB_S3_ACCESS_KEY_ID") == "from-global"


def test_replace_clears_state_and_remove_keeps_wiki(tmp_path):
    kb = _kb(tmp_path)
    message = upsert_binding(kb, _binding())
    assert message.startswith("Binding `papers` saved")
    update_state(
        kb,
        "papers",
        {
            "provider": "s3",
            "location": _binding().location(),
            "last_sync_at": "2026-11-05T00:00:00Z",
            "added": 2,
            "skipped": 0,
            "updated": 0,
            "failed": 0,
            "error": None,
        },
    )
    page = kb / "wiki" / "summaries" / "kept.md"
    raw = kb / "raw" / "kept.pdf"
    page.write_text("stay", encoding="utf-8")
    raw.write_bytes(b"stay")

    replaced = upsert_binding(kb, _binding(bucket="other"))
    assert "remain in the wiki" in replaced
    assert "papers" not in read_state(kb)

    removed = remove_binding(kb, "papers")
    assert "remain in the wiki" in removed
    assert load_bindings(kb) == []
    assert page.read_text(encoding="utf-8") == "stay"
    assert raw.read_bytes() == b"stay"


def test_state_refuses_a_credential(tmp_path):
    kb = _kb(tmp_path)
    with pytest.raises(SecretInStateError):
        update_state(
            kb,
            "papers",
            {"error": "leaked secret-test"},
            secrets=["secret-test"],
        )
    assert read_state(kb) == {}


def test_state_write_times_out_while_add_holds_the_lock(tmp_path):
    assert LOCK_TIMEOUT_SECONDS == 30
    kb = _kb(tmp_path)
    started = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with kb_ingest_lock(kb / ".openkb"):
            started.set()
            assert release.wait(timeout=2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert started.wait(timeout=2)
    try:
        with pytest.raises(KbBusyError, match="knowledge base is busy"):
            update_state(
                kb,
                "papers",
                {"provider": "s3", "location": {}, "error": None},
                timeout=0.2,
            )
    finally:
        release.set()
        thread.join(timeout=2)


def test_sync_skips_known_hash_and_updates_same_key(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    _creds(kb)
    monkeypatch.delenv("OPENKB_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("OPENKB_S3_SECRET_ACCESS_KEY", raising=False)
    upsert_binding(kb, _binding(prefix=""))
    identity = s3_object_identity("acme", "docs/a.txt")
    HashRegistry(kb / ".openkb" / "hashes.json").add(
        "old",
        {"doc_name": "a", "path": identity, "name": "a.txt"},
    )
    calls: list[str] = []

    def add(path: Path, kb_dir: Path, identity: str) -> str:
        assert not kb_ingest_lock_held(kb_dir / ".openkb")
        calls.append(identity)
        if path.read_bytes() == b"same":
            return "skipped"
        return "added"

    same = RemoteObject(identity=identity, name="a.txt")
    backend = _Backend([same])
    backend.download = lambda obj, dest: dest.write_bytes(b"same")  # type: ignore[method-assign]
    outcome = sync_bindings(
        kb,
        add=add,
        backend_for=lambda binding, access, secret: backend,
    )
    assert outcome.exit_code == 0
    assert outcome.reports[0].skipped == 1
    assert calls == [identity]

    backend.download = lambda obj, dest: dest.write_bytes(b"new")  # type: ignore[method-assign]
    outcome = sync_bindings(
        kb,
        add=add,
        backend_for=lambda binding, access, secret: backend,
    )
    assert outcome.reports[0].updated == 1
    assert outcome.reports[0].added == 0


def test_new_key_adds_and_one_failure_keeps_the_rest(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    _creds(kb)
    monkeypatch.delenv("OPENKB_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("OPENKB_S3_SECRET_ACCESS_KEY", raising=False)
    upsert_binding(kb, _binding(prefix=""))
    good = RemoteObject(identity="s3://acme/a.txt", name="a.txt")
    bad = RemoteObject(identity="s3://acme/b.txt", name="b.txt")
    noise = RemoteObject(identity="s3://acme/c.bin", name="c.bin")

    def add(path: Path, kb_dir: Path, identity: str) -> str:
        if identity.endswith("b.txt"):
            raise ProviderError("compile failed")
        return "added"

    backend = _Backend([good, bad, noise])
    outcome = sync_bindings(kb, add=add, backend_for=lambda *_args: backend)
    report = outcome.reports[0]
    assert report.added == 1
    assert report.failed == 1
    assert report.skipped == 1
    assert outcome.exit_code == 1


def test_transient_download_retries_then_fails(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    _creds(kb)
    monkeypatch.delenv("OPENKB_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("OPENKB_S3_SECRET_ACCESS_KEY", raising=False)
    upsert_binding(kb, _binding(prefix=""))
    obj = RemoteObject(identity="s3://acme/a.txt", name="a.txt")
    backend = _Backend([obj], download_failures=2)
    outcome = sync_bindings(
        kb,
        add=lambda path, kb_dir, identity: "added",
        backend_for=lambda *_args: backend,
    )
    assert outcome.exit_code == 0
    assert backend.downloads == 3
    assert outcome.reports[0].added == 1

    backend = _Backend([obj], download_failures=3)
    outcome = sync_bindings(
        kb,
        add=lambda path, kb_dir, identity: "added",
        backend_for=lambda *_args: backend,
    )
    assert outcome.reports[0].failed == 1
    assert outcome.exit_code == 1


def test_non_interactive_does_not_call_sign_in():
    called: list[str] = []
    with pytest.raises(ProviderError):
        recover_auth(non_interactive=True, sign_in=lambda: called.append("opened"))
    assert called == []


def test_s3_lists_pages_and_empty_prefix_is_the_bucket_root(tmp_path):
    class _Paginator:
        def __init__(self) -> None:
            self.kwargs: dict[str, str] = {}

        def paginate(self, **kwargs):
            self.kwargs = kwargs
            yield {"Contents": [{"Key": "notes.txt"}, {"Key": "dir/"}]}
            yield {"Contents": [{"Key": "sub/paper.pdf"}]}

    class _Client:
        def __init__(self) -> None:
            self.paginator = _Paginator()
            self.downloaded: list[str] = []

        def get_paginator(self, name: str):
            assert name == "list_objects_v2"
            return self.paginator

        def download_file(self, bucket: str, key: str, dest: str) -> None:
            self.downloaded.append(f"{bucket}/{key}")
            Path(dest).write_text(key, encoding="utf-8")

    client = _Client()
    binding = _binding(prefix="")
    backend = S3Backend(binding, "a", "b", client=client)
    found = backend.list_objects()
    assert "Prefix" not in client.paginator.kwargs
    assert [item.identity for item in found] == [
        "s3://acme/notes.txt",
        "s3://acme/sub/paper.pdf",
    ]
    out = tmp_path / found[0].name
    backend.download(found[0], out)
    assert out.read_text(encoding="utf-8") == "notes.txt"
    assert client.downloaded == ["acme/notes.txt"]


def test_missing_boto3_names_the_extra():
    try:
        import boto3  # noqa: F401
    except ImportError:
        with pytest.raises(ProviderError, match=r"openkb\[s3\]"):
            _load_boto3()
    else:
        pytest.skip("boto3 is installed in this environment")
