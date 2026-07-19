from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import yaml
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


def _events_from_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append({"event": event, "data": data})
    return events


def test_no_token_configured_disables_auth(monkeypatch, tmp_path):
    """Local-first default: with no OPENKB_API_TOKEN set, auth is off and an
    unauthenticated request is allowed (not 500/401)."""
    monkeypatch.setenv("OPENKB_KB_ROOT", str(tmp_path))
    client = _client(monkeypatch, token=None)

    response = client.get("/api/v1/kbs")  # no Authorization header

    assert response.status_code == 200


def test_configured_token_is_enforced(monkeypatch, tmp_path):
    """Setting OPENKB_API_TOKEN opts into bearer auth: an unauthenticated
    request is then rejected."""
    monkeypatch.setenv("OPENKB_KB_ROOT", str(tmp_path))
    client = _client(monkeypatch, token="secret")

    response = client.get("/api/v1/kbs")  # no Authorization header

    assert response.status_code == 401


def test_api_rejects_missing_or_invalid_token(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    missing = client.post(
        "/api/v1/query",
        json={"kb": kb, "question": "What?"},
    )
    invalid = client.post(
        "/api/v1/query",
        json={"kb": kb, "question": "What?"},
        headers=_auth("wrong"),
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401


def test_query_non_stream_returns_json(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    async def fake_run_query(question, kb, model, stream=False, **kwargs):
        assert question == "What is OpenKB?"
        assert kb == kb_dir
        assert stream is False
        return "A knowledge base."

    monkeypatch.setattr("openkb.api.run_query", fake_run_query)

    response = client.post(
        "/api/v1/query",
        json={"kb": kb, "question": "What is OpenKB?", "stream": False},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json() == {"answer": "A knowledge base.", "saved_path": None}


def test_query_stream_returns_sse_events(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    async def fake_events(agent, question, **kwargs):
        assert question == "What is OpenKB?"
        yield {"event": "delta", "data": {"text": "A knowledge"}}
        yield {"event": "delta", "data": {"text": " base."}}
        yield {"event": "final", "data": {"answer": "A knowledge base.", "history": []}}

    monkeypatch.setattr("openkb.api_helpers.build_query_agent", lambda *args, **kwargs: object())
    monkeypatch.setattr("openkb.api_helpers.iter_agent_response_events", fake_events)

    response = client.post(
        "/api/v1/query",
        json={"kb": kb, "question": "What is OpenKB?"},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events_from_sse(response.text)
    assert [event["event"] for event in events] == ["start", "delta", "delta", "final", "done"]
    assert events[-2]["data"]["answer"] == "A knowledge base."


def test_query_endpoint_uses_global_model(monkeypatch, kb_dir, tmp_path):
    """The non-streaming /query path builds its run config with the global.yaml
    model when the KB config is silent — proving api.py:281 uses the resolver."""
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_PATH", tmp_path / "global.yaml")
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", tmp_path)
    from openkb.config import save_global_config

    save_global_config({"model": "global-model"})

    captured = {}

    async def _fake_run_query(question, kb, model, *args, **kwargs):
        captured["model"] = model
        return "ok"

    monkeypatch.setattr("openkb.api.run_query", _fake_run_query)
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post(
        "/api/v1/query", json={"kb": kb, "question": "hi", "stream": False}, headers=_auth()
    )
    assert response.status_code == 200
    assert captured["model"] == "global-model"


def test_chat_non_stream_creates_and_persists_session(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    async def fake_agent_events(agent, input_data, *, max_turns, **kwargs):
        yield {"event": "delta", "data": {"text": "Hello"}}
        yield {
            "event": "final",
            "data": {
                "answer": "Hello",
                "history": [{"role": "assistant", "content": "Hello"}],
            },
        }

    monkeypatch.setattr(
        "openkb.api_helpers.build_chat_session_agent",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("openkb.agent.chat.iter_agent_response_events", fake_agent_events)

    response = client.post(
        "/api/v1/chat",
        json={"kb": kb, "message": "Hi", "stream": False},
        headers=_auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "Hello"
    assert payload["turn_count"] == 1
    session_path = kb_dir / ".openkb" / "chats" / f"{payload['session_id']}.json"
    assert session_path.exists()


def test_chat_stream_resumes_session(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    from openkb.agent.chat_session import ChatSession

    session = ChatSession.new(kb_dir, "gpt-4o-mini", "en")
    session.record_turn("Hi", "Hello", [{"role": "assistant", "content": "Hello"}])

    async def fake_chat_events(agent, loaded_session, message, **kwargs):
        assert loaded_session.id == session.id
        assert message == "Again"
        yield {"event": "delta", "data": {"text": "Again"}}
        yield {
            "event": "final",
            "data": {
                "answer": "Again",
                "session_id": loaded_session.id,
                "turn_count": 2,
            },
        }

    monkeypatch.setattr(
        "openkb.api_helpers.build_chat_session_agent",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("openkb.api_helpers.iter_chat_turn_events", fake_chat_events)

    response = client.post(
        "/api/v1/chat",
        json={"kb": kb, "message": "Again", "session_id": session.id},
        headers=_auth(),
    )

    assert response.status_code == 200
    events = _events_from_sse(response.text)
    assert events[0]["data"]["session_id"] == session.id
    assert events[-2]["data"]["turn_count"] == 2


def test_chat_stream_forwards_artifact_event(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    async def fake_chat_events(agent, session, message, **kwargs):
        yield {
            "event": "artifact",
            "data": {"kind": "file", "path": "output/x.html", "name": "x.html"},
        }
        yield {"event": "final", "data": {"answer": "done", "session_id": "s1", "turn_count": 1}}

    monkeypatch.setattr("openkb.api_helpers.build_chat_session_agent", lambda *a, **k: object())
    monkeypatch.setattr("openkb.api_helpers.iter_chat_turn_events", fake_chat_events)

    response = client.post(
        "/api/v1/chat", json={"kb": kb, "message": "hi", "stream": True}, headers=_auth()
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events_from_sse(response.text)
    artifact = next(e for e in events if e["event"] == "artifact")
    assert artifact["data"] == {"kind": "file", "path": "output/x.html", "name": "x.html"}


def test_init_endpoint_creates_named_kb_under_env_root(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    root = tmp_path / "api-kbs"
    kb_dir = root / "postman-kb"
    # Run from a clean CWD so the KB does not inherit the real project-root
    # config.yaml/.env (initialize_kb seeds from Path.cwd()).
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENKB_KB_ROOT", str(root))
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_PATH", tmp_path / "global.yaml")
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", tmp_path)

    response = client.post(
        "/api/v1/init",
        json={"kb": "postman-kb", "model": "gpt-5.4-mini"},
        headers=_auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kb"] == "postman-kb"
    assert payload["created"] is True
    assert payload["env_written"] == {"api_key": False, "openai_api_base": False}
    assert (kb_dir / ".openkb" / "config.yaml").is_file()
    assert (kb_dir / "wiki" / "AGENTS.md").is_file()
    global_config = yaml.safe_load((tmp_path / "global.yaml").read_text(encoding="utf-8"))
    assert global_config["kb_aliases"] == {"postman-kb": str(kb_dir.resolve())}


def test_init_endpoint_ignores_stale_alias_for_creation(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    root = tmp_path / "api-kbs"
    kb_dir = root / "postman-kb"
    stale_path = tmp_path / "stale-kb"
    stale_path.joinpath(".openkb").mkdir(parents=True)
    stale_path.joinpath("wiki").mkdir()
    stale_path.joinpath("wiki", "AGENTS.md").write_text("# stale\n", encoding="utf-8")
    global_dir = tmp_path / "global-config"
    global_path = global_dir / "global.yaml"
    global_dir.mkdir()
    global_path.write_text(
        yaml.safe_dump(
            {
                "default_kb": str(stale_path),
                "known_kbs": [str(stale_path)],
                "kb_aliases": {"postman-kb": str(stale_path)},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENKB_KB_ROOT", str(root))
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_PATH", global_path)
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", global_dir)

    response = client.post(
        "/api/v1/init",
        json={"kb": "postman-kb"},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert (kb_dir / ".openkb" / "config.yaml").is_file()
    assert (kb_dir / "wiki" / "AGENTS.md").is_file()
    global_config = yaml.safe_load(global_path.read_text(encoding="utf-8"))
    assert global_config["kb_aliases"]["postman-kb"] == str(kb_dir.resolve())


def test_init_endpoint_rejects_invalid_kb_name(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/v1/init",
        json={"kb": "../bad"},
        headers=_auth(),
    )

    assert response.status_code == 400
    assert "KB name" in response.json()["detail"]


def test_init_endpoint_writes_env_without_leaking_values(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    root = tmp_path / "api-kbs"
    kb_dir = root / "new-kb"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENKB_KB_ROOT", str(root))
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_PATH", tmp_path / "global.yaml")
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", tmp_path)

    response = client.post(
        "/api/v1/init",
        json={
            "kb": "new-kb",
            "api_key": "sk-secret",
            "openai_api_base": "https://gateway.example/v1",
        },
        headers=_auth(),
    )

    assert response.status_code == 200
    payload_text = response.text
    assert "sk-secret" not in payload_text
    assert "https://gateway.example/v1" not in payload_text
    assert response.json()["env_written"] == {"api_key": True, "openai_api_base": True}
    assert (kb_dir / ".env").read_text(encoding="utf-8") == (
        "LITELLM_LOCAL_MODEL_COST_MAP=true\n"
        "LLM_API_KEY=sk-secret\n"
        "OPENAI_API_BASE=https://gateway.example/v1\n"
    )


def test_init_endpoint_inherits_project_root_config(monkeypatch, tmp_path):
    # A KB created from the REST UI (no explicit params) should inherit the
    # operator's project-root config.yaml and LLM credentials from .env, so it
    # can run queries/compiles out of the box. Server-level OPENKB_* vars are
    # filtered out of the inherited .env.
    client = _client(monkeypatch)
    root = tmp_path / "api-kbs"
    kb_dir = root / "templated-kb"
    # Simulate the project root: deploy a config.yaml + .env at CWD.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text(
        "model: openai/deepseek-v4-flash\nlanguage: zh\npageindex_threshold: 20\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "LLM_API_KEY=sk-inherited\n"
        "OPENAI_API_BASE=https://gateway.example/v1\n"
        "OPENKB_API_TOKEN=secret\n"
        "OPENKB_KB_ROOT=" + str(root) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENKB_KB_ROOT", str(root))
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_PATH", tmp_path / "global.yaml")
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", tmp_path)

    response = client.post("/api/v1/init", json={"kb": "templated-kb"}, headers=_auth())

    assert response.status_code == 200
    payload = response.json()
    assert payload["env_written"] == {"api_key": True, "openai_api_base": True}
    # config.yaml inherited verbatim (model/language preserved, not gpt-5.4/en).
    config = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text("utf-8"))
    assert config["model"] == "openai/deepseek-v4-flash"
    assert config["language"] == "zh"
    # .env inherited LLM creds but dropped server-level OPENKB_* vars.
    env_text = (kb_dir / ".env").read_text(encoding="utf-8")
    assert "LLM_API_KEY=sk-inherited" in env_text
    assert "OPENAI_API_BASE=https://gateway.example/v1" in env_text
    assert "OPENKB_API_TOKEN" not in env_text
    assert "OPENKB_KB_ROOT" not in env_text


def test_init_endpoint_rejects_existing_kb(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    root = tmp_path / "api-kbs"
    kb_dir = root / "existing-kb"
    (kb_dir / ".openkb").mkdir(parents=True)
    monkeypatch.setenv("OPENKB_KB_ROOT", str(root))
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_PATH", tmp_path / "global.yaml")
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", tmp_path)

    response = client.post(
        "/api/v1/init",
        json={"kb": "existing-kb"},
        headers=_auth(),
    )

    assert response.status_code == 400
    assert "already initialized" in response.json()["detail"]


def test_add_endpoint_rejects_missing_files(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb, "stream": "false"},
        headers=_auth(),
    )

    assert response.status_code == 400
    assert "No files uploaded" in response.json()["detail"]


def test_add_endpoint_rejects_unsupported_extension(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb, "stream": "false"},
        files=[("files", ("bad.xyz", b"bad", "application/octet-stream"))],
        headers=_auth(),
    )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_add_endpoint_rejects_oversized_file(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    monkeypatch.setattr("openkb.api_helpers.MAX_UPLOAD_FILE_BYTES", 4)
    monkeypatch.setattr("openkb.api_helpers.MAX_UPLOAD_REQUEST_BYTES", 100)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb},
        files=[("files", ("paper.md", b"12345", "text/markdown"))],
        headers=_auth(),
    )

    assert response.status_code == 413
    assert "Uploaded file exceeds limit" in response.json()["detail"]
    assert not (kb_dir / "raw" / "paper.md").exists()


def test_add_endpoint_rejects_oversized_aggregate_request(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    monkeypatch.setattr("openkb.api_helpers.MAX_UPLOAD_FILE_BYTES", 10)
    monkeypatch.setattr("openkb.api_helpers.MAX_UPLOAD_REQUEST_BYTES", 8)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb, "stream": "false"},
        files=[
            ("files", ("one.md", b"12345", "text/markdown")),
            ("files", ("two.md", b"67890", "text/markdown")),
        ],
        headers=_auth(),
    )

    assert response.status_code == 413
    assert "Upload request exceeds limit" in response.json()["detail"]
    assert not (kb_dir / "raw" / "one.md").exists()
    assert not (kb_dir / "raw" / "two.md").exists()


def test_add_endpoint_uploads_and_adds_single_file(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    from openkb.cli import AddFileResult

    calls = []

    def fake_add(path, target_kb, **kwargs):
        calls.append((path, target_kb))
        return AddFileResult(path.name, str(path), "added", f"{path.name} added to knowledge base.")

    monkeypatch.setattr("openkb.api_helpers._add_for_api", fake_add)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb, "stream": "false"},
        files=[("files", ("paper.md", b"# Paper", "text/markdown"))],
        headers=_auth(),
    )

    assert response.status_code == 200
    saved_path = kb_dir / "raw" / "paper.md"
    assert saved_path.read_bytes() == b"# Paper"
    assert calls == [(saved_path, kb_dir)]
    assert response.json()["files"][0] == {
        "original_name": "paper.md",
        "saved_path": str(saved_path),
        "status": "added",
        "message": "paper.md added to knowledge base.",
    }
    assert response.json()["added_count"] == 1


def test_add_endpoint_runs_real_add_helper_outside_event_loop(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    from openkb.converter import ConvertResult

    def fake_convert(path, target_kb, *, staging_dir=None):
        assert target_kb == kb_dir
        return ConvertResult(
            raw_path=path,
            source_path=target_kb / "wiki" / "sources" / path.name,
            is_long_doc=False,
            file_hash="abc123",
        )

    async def fake_compile_short_doc(doc_name, source_path, target_kb, model, **kwargs):
        assert doc_name == "paper"
        assert target_kb == kb_dir

    monkeypatch.setattr("openkb.cli._setup_llm_key", lambda kb: None)
    monkeypatch.setattr("openkb.cli.convert_document", fake_convert)
    monkeypatch.setattr("openkb.agent.compiler.compile_short_doc", fake_compile_short_doc)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb, "stream": "false"},
        files=[("files", ("paper.md", b"# Paper", "text/markdown"))],
        headers=_auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["added_count"] == 1
    assert payload["files"][0]["status"] == "added"
    assert payload["files"][0]["saved_path"] == str(kb_dir / "raw" / "paper.md")


def test_add_endpoint_uploads_and_adds_multiple_files(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    from openkb.cli import AddFileResult

    calls = []

    def fake_add(path, target_kb, **kwargs):
        calls.append((path, target_kb))
        if path.name == "notes.txt":
            return AddFileResult(
                path.name,
                None,
                "skipped",
                "Already in knowledge base: notes.txt",
            )
        return AddFileResult(
            path.name,
            str(path),
            "added",
            f"{path.name} added to knowledge base.",
        )

    monkeypatch.setattr("openkb.api_helpers._add_for_api", fake_add)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb, "stream": "false"},
        files=[
            ("files", ("paper.md", b"# Paper", "text/markdown")),
            ("files", ("notes.txt", b"Notes", "text/plain")),
        ],
        headers=_auth(),
    )

    paper_path = kb_dir / "raw" / "paper.md"
    notes_path = kb_dir / "raw" / "notes.txt"
    assert response.status_code == 200
    assert paper_path.read_bytes() == b"# Paper"
    assert not notes_path.exists()
    assert calls == [(paper_path, kb_dir), (notes_path, kb_dir)]
    assert response.json() == {
        "kb": kb,
        "files": [
            {
                "original_name": "paper.md",
                "saved_path": str(paper_path),
                "status": "added",
                "message": "paper.md added to knowledge base.",
            },
            {
                "original_name": "notes.txt",
                "saved_path": None,
                "status": "skipped",
                "message": "Already in knowledge base: notes.txt",
            },
        ],
        "added_count": 1,
        "skipped_count": 1,
        "failed_count": 0,
    }


def test_add_endpoint_uses_unique_raw_filename(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / "raw" / "paper.md").write_text("existing", encoding="utf-8")

    from openkb.cli import AddFileResult

    def fake_add(path, target_kb, **kwargs):
        return AddFileResult(path.name, str(path), "added", f"{path.name} added to knowledge base.")

    monkeypatch.setattr("openkb.api_helpers._add_for_api", fake_add)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb, "stream": "false"},
        files=[("files", ("paper.md", b"# New", "text/markdown"))],
        headers=_auth(),
    )

    assert response.status_code == 200
    assert (kb_dir / "raw" / "paper.md").read_text(encoding="utf-8") == "existing"
    assert (kb_dir / "raw" / "paper-1.md").read_bytes() == b"# New"
    assert response.json()["files"][0]["original_name"] == "paper.md"
    assert response.json()["files"][0]["saved_path"] == str(kb_dir / "raw" / "paper-1.md")


def test_add_endpoint_removes_skipped_upload(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    from openkb.cli import AddFileResult

    skipped_path = None

    def fake_add(path, target_kb, **kwargs):
        nonlocal skipped_path
        skipped_path = path
        return AddFileResult(path.name, None, "skipped", "Already in knowledge base: paper.md")

    monkeypatch.setattr("openkb.api_helpers._add_for_api", fake_add)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb, "stream": "false"},
        files=[("files", ("paper.md", b"# Paper", "text/markdown"))],
        headers=_auth(),
    )

    assert response.status_code == 200
    assert skipped_path == kb_dir / "raw" / "paper.md"
    assert not skipped_path.exists()
    assert response.json()["files"][0] == {
        "original_name": "paper.md",
        "saved_path": None,
        "status": "skipped",
        "message": "Already in knowledge base: paper.md",
    }
    assert response.json()["skipped_count"] == 1


def test_add_endpoint_streams_events(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    from openkb.cli import AddFileResult

    def fake_add(path, target_kb, **kwargs):
        return AddFileResult(path.name, str(path), "added", f"{path.name} added to knowledge base.")

    monkeypatch.setattr("openkb.api_helpers._add_for_api", fake_add)

    response = client.post(
        "/api/v1/add",
        data={"kb": kb, "stream": "true"},
        files=[("files", ("paper.md", b"# Paper", "text/markdown"))],
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events_from_sse(response.text)
    assert [event["event"] for event in events] == [
        "start",
        "uploaded",
        "file_start",
        "file_done",
        "final",
        "done",
    ]
    assert events[0]["data"]["kb"] == kb
    assert events[-2]["data"]["added_count"] == 1


def test_unknown_kb_returns_400(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", lambda kb: tmp_path)

    response = client.post(
        "/api/v1/query",
        json={"kb": "missing-kb", "question": "What?"},
        headers=_auth(),
    )

    assert response.status_code == 400
    assert "Not a knowledge base" in response.json()["detail"]


def test_kb_endpoints_reject_unknown_kb(monkeypatch, tmp_path):
    client = _client(monkeypatch)
    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", lambda kb: tmp_path)

    for path in ("/api/v1/list", "/api/v1/status", "/api/v1/lint"):
        response = client.post(
            path,
            json={"kb": "missing-kb"},
            headers=_auth(),
        )
        assert response.status_code == 400


def test_kb_endpoints_reject_invalid_kb_name(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/api/v1/query",
        json={"kb": "../bad", "question": "What?"},
        headers=_auth(),
    )

    assert response.status_code == 400
    assert "KB name" in response.json()["detail"]


def test_unknown_chat_session_returns_404(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post(
        "/api/v1/chat",
        json={
            "kb": kb,
            "message": "Hi",
            "session_id": "missing-session",
            "stream": False,
        },
        headers=_auth(),
    )

    assert response.status_code == 404


def test_list_endpoint_returns_empty_inventory(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post(
        "/api/v1/list",
        json={"kb": kb},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "documents": [],
        "document_count": 0,
        "summaries": [],
        "concepts": [],
        "entities": [],
        "reports": [],
    }


def test_list_endpoint_returns_structured_inventory(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    hashes = {
        "abc123": {"name": "paper.pdf", "type": "pdf", "pages": 12},
        "def456": {"name": "notes.md", "type": "md"},
    }
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes), encoding="utf-8")
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text("# Paper", encoding="utf-8")
    (kb_dir / "wiki" / "concepts" / "attention.md").write_text("# Attention", encoding="utf-8")
    reports_dir = kb_dir / "wiki" / "reports"
    (reports_dir / "lint_20260101_000000.md").write_text("# Report", encoding="utf-8")

    response = client.post(
        "/api/v1/list",
        json={"kb": kb},
        headers=_auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_count"] == 2
    assert payload["documents"] == [
        {
            "hash": "abc123",
            "name": "paper.pdf",
            "type": "pdf",
            "display_type": "short",
            "pages": 12,
        },
        {
            "hash": "def456",
            "name": "notes.md",
            "type": "md",
            "display_type": "short",
            "pages": None,
        },
    ]
    assert payload["summaries"] == ["paper"]
    assert payload["concepts"] == ["attention"]
    assert payload["reports"] == ["lint_20260101_000000.md"]


def test_list_endpoint_includes_entities(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"h1": {"name": "p.pdf", "type": "pdf"}}), encoding="utf-8"
    )
    (kb_dir / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
    (kb_dir / "wiki" / "entities" / "nvidia.md").write_text("# NVIDIA", encoding="utf-8")
    (kb_dir / "wiki" / "entities" / "anthropic.md").write_text("# Anthropic", encoding="utf-8")

    response = client.post("/api/v1/list", json={"kb": kb}, headers=_auth())

    assert response.status_code == 200
    assert response.json()["entities"] == ["anthropic", "nvidia"]


def test_status_endpoint_returns_structured_counts(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / "raw" / "paper.pdf").write_bytes(b"pdf")
    (kb_dir / "wiki" / "sources" / "paper.md").write_text("# Source", encoding="utf-8")
    (kb_dir / "wiki" / "summaries" / "paper.md").write_text("# Summary", encoding="utf-8")
    (kb_dir / "wiki" / "concepts" / "attention.md").write_text("# Attention", encoding="utf-8")
    (kb_dir / "wiki" / "reports" / "lint_20260101_000000.md").write_text(
        "# Report", encoding="utf-8"
    )
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"abc123": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )

    response = client.post(
        "/api/v1/status",
        json={"kb": kb},
        headers=_auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["directories"] == {
        "sources": 1,
        "summaries": 1,
        "concepts": 1,
        "reports": 1,
    }
    assert payload["raw_count"] == 1
    assert payload["total_indexed"] == 1
    assert payload["last_compile"] is not None
    assert payload["last_lint"] is not None


def test_lint_endpoint_skips_empty_kb(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post(
        "/api/v1/lint",
        json={"kb": kb},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert response.json() == {
        "skipped": True,
        "reason": "no_documents_indexed",
        "message": "Nothing to lint - no documents indexed yet. Run `openkb add` first.",
        "structural_report": None,
        "knowledge_report": None,
        "report_path": None,
        "lint_files_changed": None,
        "lint_ghosts_removed": None,
    }
    assert list((kb_dir / "wiki" / "reports").glob("*.md")) == []


def test_lint_endpoint_runs_and_writes_report(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"abc123": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )

    async def fake_knowledge_lint(kb, model, **kwargs):
        assert kb == kb_dir
        return "No semantic issues."

    monkeypatch.setattr("openkb.cli._setup_llm_key", lambda kb: None)
    monkeypatch.setattr("openkb.agent.linter.run_knowledge_lint", fake_knowledge_lint)

    response = client.post(
        "/api/v1/lint",
        json={"kb": kb},
        headers=_auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["skipped"] is False
    assert payload["reason"] is None
    assert payload["message"] == "Lint report written."
    assert "Structural Lint Report" in payload["structural_report"]
    assert payload["knowledge_report"] == "No semantic issues."
    # Without `fix`, the new count fields are absent (None).
    assert payload["lint_files_changed"] is None
    assert payload["lint_ghosts_removed"] is None
    reports = list((kb_dir / "wiki" / "reports").glob("lint_*.md"))
    assert len(reports) == 1
    assert payload["report_path"] == str(reports[0])
    assert "No semantic issues." in reports[0].read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# POST /api/v1/remove
# ---------------------------------------------------------------------------


def test_lint_endpoint_fix_rewrites_broken_wikilinks(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    # Non-empty hashes so the report path (not the skip path) runs.
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"abc123": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )
    # Valid target + a page with a broken link that has no match.
    (kb_dir / "wiki" / "concepts" / "a.md").write_text("A valid page.", encoding="utf-8")
    page = kb_dir / "wiki" / "summaries" / "s.md"
    page.write_text("See [[concepts/a]] and [[Ghost Link]].", encoding="utf-8")

    async def fake_knowledge_lint(kb, model, **kwargs):
        return "No semantic issues."

    monkeypatch.setattr("openkb.cli._setup_llm_key", lambda kb: None)
    monkeypatch.setattr("openkb.agent.linter.run_knowledge_lint", fake_knowledge_lint)

    response = client.post(
        "/api/v1/lint",
        json={"kb": kb, "fix": True},
        headers=_auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    # The broken link was stripped from its file.
    assert payload["lint_files_changed"] >= 1
    assert payload["lint_ghosts_removed"] >= 1
    assert "[[Ghost Link]]" not in page.read_text(encoding="utf-8")
    # The valid link is left intact.
    assert "[[concepts/a]]" in page.read_text(encoding="utf-8")
    # A lint report was still written, reflecting the post-fix state.
    assert payload["skipped"] is False
    assert "Fixed" in payload["message"]
    reports = list((kb_dir / "wiki" / "reports").glob("lint_*.md"))
    assert len(reports) == 1


def test_lint_endpoint_fix_noop_when_clean(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps({"abc123": {"name": "paper.pdf", "type": "pdf"}}),
        encoding="utf-8",
    )
    # Clean wiki: only a valid, resolving link.
    (kb_dir / "wiki" / "concepts" / "a.md").write_text("A valid page.", encoding="utf-8")
    (kb_dir / "wiki" / "summaries" / "s.md").write_text("See [[concepts/a]].", encoding="utf-8")

    async def fake_knowledge_lint(kb, model, **kwargs):
        return "No semantic issues."

    monkeypatch.setattr("openkb.cli._setup_llm_key", lambda kb: None)
    monkeypatch.setattr("openkb.agent.linter.run_knowledge_lint", fake_knowledge_lint)

    response = client.post(
        "/api/v1/lint",
        json={"kb": kb, "fix": True},
        headers=_auth(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["lint_files_changed"] == 0
    assert payload["lint_ghosts_removed"] == 0
    assert "Nothing to fix" in payload["message"]
    assert payload["skipped"] is False


def test_remove_non_stream_success(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    payload = {
        "status": "removed",
        "name": "paper.pdf",
        "doc_name": "paper",
        "actions": [{"tag": "DELETE", "target": "wiki/summaries/paper.md"}],
        "concepts_deleted": ["transformer"],
        "entities_deleted": [],
        "lint_files_changed": 1,
        "lint_ghosts_removed": 2,
        "pageindex_message": None,
        "pageindex_error": None,
        "message": "paper.pdf removed from knowledge base.",
    }
    monkeypatch.setattr(
        "openkb.api.run_remove_for_api",
        lambda kb_dir, identifier, **kw: payload,
    )

    response = client.post(
        "/api/v1/remove",
        json={"kb": kb, "identifier": "paper.pdf"},
        headers=_auth(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "removed"
    assert body["doc_name"] == "paper"
    assert body["actions"][0]["tag"] == "DELETE"
    assert body["lint_files_changed"] == 1


def test_remove_not_found_returns_404(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    monkeypatch.setattr(
        "openkb.api.run_remove_for_api",
        lambda kb_dir, identifier, **kw: {"status": "not_found", "message": "Document not found."},
    )
    response = client.post(
        "/api/v1/remove",
        json={"kb": kb, "identifier": "nope"},
        headers=_auth(),
    )
    assert response.status_code == 404


def test_remove_multiple_matches_returns_409(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    monkeypatch.setattr(
        "openkb.api.run_remove_for_api",
        lambda kb_dir, identifier, **kw: {
            "status": "multiple",
            "candidates": [
                {"name": "a.pdf", "doc_name": "a-h1"},
                {"name": "b.pdf", "doc_name": "b-h2"},
            ],
        },
    )
    response = client.post(
        "/api/v1/remove",
        json={"kb": kb, "identifier": "h"},
        headers=_auth(),
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["candidates"][0]["doc_name"] == "a-h1"


def test_remove_dry_run_returns_plan(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    captured = {}

    def fake(kb_dir, identifier, *, keep_raw, keep_empty, dry_run):
        captured["dry_run"] = dry_run
        captured["keep_raw"] = keep_raw
        return {
            "status": "dry_run",
            "name": "paper.pdf",
            "doc_name": "paper",
            "actions": [{"tag": "DELETE", "target": "wiki/summaries/paper.md"}],
            "concepts_deleted": [],
            "entities_deleted": [],
        }

    monkeypatch.setattr("openkb.api.run_remove_for_api", fake)

    response = client.post(
        "/api/v1/remove",
        json={"kb": kb, "identifier": "paper.pdf", "dry_run": True, "keep_raw": True},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "dry_run"
    assert captured["dry_run"] is True
    assert captured["keep_raw"] is True


def test_remove_passes_keep_empty(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    captured = {}

    def fake(kb_dir, identifier, *, keep_raw, keep_empty, dry_run):
        captured["keep_empty"] = keep_empty
        return {"status": "removed", "name": "p", "doc_name": "p", "actions": []}

    monkeypatch.setattr("openkb.api.run_remove_for_api", fake)
    client.post(
        "/api/v1/remove",
        json={"kb": kb, "identifier": "p", "keep_empty": True},
        headers=_auth(),
    )
    assert captured["keep_empty"] is True


def test_remove_stream_returns_sse(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    monkeypatch.setattr(
        "openkb.api_helpers.run_remove_for_api",
        lambda kb_dir, identifier, **kw: {
            "status": "removed",
            "name": "paper.pdf",
            "doc_name": "paper",
            "actions": [{"tag": "DELETE", "target": "wiki/summaries/paper.md"}],
            "concepts_deleted": [],
            "entities_deleted": [],
            "message": "done",
        },
    )

    response = client.post(
        "/api/v1/remove",
        json={"kb": kb, "identifier": "paper.pdf", "stream": True},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events_from_sse(response.text)
    names = [e["event"] for e in events]
    assert names[0] == "start"
    assert names[-1] == "done"
    assert "plan" in names and "final" in names


def test_remove_stream_not_found(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    monkeypatch.setattr(
        "openkb.api_helpers.run_remove_for_api",
        lambda kb_dir, identifier, **kw: {"status": "not_found"},
    )
    response = client.post(
        "/api/v1/remove",
        json={"kb": kb, "identifier": "x", "stream": True},
        headers=_auth(),
    )
    events = _events_from_sse(response.text)
    assert any(e["event"] == "error" and e["data"].get("code") == 404 for e in events)


# ---------------------------------------------------------------------------
# recompile endpoint
# ---------------------------------------------------------------------------
#
# Seed helpers mirror tests/test_recompile.py (_seed_short/_seed_long) but are
# kept local to avoid coupling the two test modules.


def _seed_short(kb_dir: Path, *, slug: str = "notes", name: str = "notes.md") -> None:
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                "h_s": {"name": name, "doc_name": slug, "type": "md"},
            }
        )
    )
    (kb_dir / "wiki" / "sources" / f"{slug}.md").write_text("# Notes\n\nbody\n", encoding="utf-8")
    (kb_dir / "wiki" / "log.md").write_text("# Log\n\n", encoding="utf-8")


def _seed_long(
    kb_dir: Path, *, slug: str = "paper", name: str = "paper.pdf", doc_id: str = "doc-abc123"
) -> None:
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                "h_l": {"name": name, "doc_name": slug, "type": "long_pdf", "doc_id": doc_id},
            }
        )
    )
    (kb_dir / "wiki" / "summaries" / f"{slug}.md").write_text(
        "---\nsources: [raw/paper.pdf]\nbrief: P\n---\n# Paper\n",
        encoding="utf-8",
    )
    (kb_dir / "wiki" / "log.md").write_text("# Log\n\n", encoding="utf-8")


def _patch_recompile(monkeypatch):
    monkeypatch.setattr("openkb.cli._setup_llm_key", lambda kb: None)
    short = AsyncMock()
    long_ = AsyncMock()
    monkeypatch.setattr("openkb.agent.compiler.compile_short_doc", short)
    monkeypatch.setattr("openkb.agent.compiler.compile_long_doc", long_)
    return short, long_


def test_recompile_non_stream_short_doc(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir)
    short, long_ = _patch_recompile(monkeypatch)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "notes.md"},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "done"
    assert body["recompiled"] == 1
    assert body["skipped"] == 0
    assert body["docs"][0]["status"] == "ok"
    short.assert_called_once()
    assert short.call_args.args[0] == "notes"  # doc_name
    assert short.call_args.args[2] == kb_dir  # kb_dir
    long_.assert_not_called()


def test_recompile_non_stream_long_doc(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_long(kb_dir)
    short, long_ = _patch_recompile(monkeypatch)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "paper.pdf"},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    long_.assert_called_once()
    args = long_.call_args.args
    assert args[0] == "paper"  # doc_name
    assert args[2] == "doc-abc123"  # doc_id
    assert args[3] == kb_dir  # kb_dir
    short.assert_not_called()


def test_recompile_not_found_404(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "ghost"},
        headers=_auth(),
    )
    assert response.status_code == 404


def test_recompile_multiple_409(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    # Two entries whose slug share a substring.
    (kb_dir / ".openkb" / "hashes.json").write_text(
        json.dumps(
            {
                "h1": {"name": "a.pdf", "doc_name": "rep-x", "type": "md"},
                "h2": {"name": "b.pdf", "doc_name": "rep-y", "type": "md"},
            }
        )
    )

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "rep"},
        headers=_auth(),
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["candidates"][0]["doc_name"] == "rep-x"


def test_recompile_requires_doc_or_all_400(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb},
        headers=_auth(),
    )
    assert response.status_code == 400


def test_recompile_both_doc_and_all_400(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "notes.md", "all_docs": True},
        headers=_auth(),
    )
    assert response.status_code == 400


def test_recompile_dry_run_returns_plan(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir)
    short, _ = _patch_recompile(monkeypatch)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "notes.md", "dry_run": True},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "dry_run"
    assert body["targets"][0]["doc_name"] == "notes"
    short.assert_not_called()


def test_recompile_all_recompiles_every_doc(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir, slug="a", name="a.md")
    (kb_dir / "wiki" / "sources" / "a.md").write_text("# A\n", encoding="utf-8")
    # add a second short doc to the registry
    data = json.loads((kb_dir / ".openkb" / "hashes.json").read_text())
    data["h2"] = {"name": "b.md", "doc_name": "b", "type": "md"}
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(data))
    (kb_dir / "wiki" / "sources" / "b.md").write_text("# B\n", encoding="utf-8")
    short, _ = _patch_recompile(monkeypatch)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "all_docs": True},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 2
    assert body["recompiled"] == 2
    assert short.call_count == 2


def test_recompile_refresh_schema_invoked(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir)
    _patch_recompile(monkeypatch)
    called = {"n": 0}

    def fake_refresh(wiki_dir):
        called["n"] += 1
        return False

    monkeypatch.setattr("openkb.cli._refresh_schema", fake_refresh)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "notes.md", "refresh_schema": True},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    assert called["n"] == 1


def test_recompile_skip_missing_source(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir, slug="good", name="good.md")
    (kb_dir / "wiki" / "sources" / "good.md").write_text("# Good\n", encoding="utf-8")
    # second doc whose source file is absent -> should be skipped
    data = json.loads((kb_dir / ".openkb" / "hashes.json").read_text())
    data["h2"] = {"name": "bad.md", "doc_name": "bad", "type": "md"}
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(data))
    short, _ = _patch_recompile(monkeypatch)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "all_docs": True},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recompiled"] == 1
    assert body["skipped"] == 1
    assert short.call_count == 1


def test_recompile_compile_error_counts_as_skipped(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir)
    short = AsyncMock(side_effect=RuntimeError("boom"))
    long_ = AsyncMock()
    monkeypatch.setattr("openkb.cli._setup_llm_key", lambda kb: None)
    monkeypatch.setattr("openkb.agent.compiler.compile_short_doc", short)
    monkeypatch.setattr("openkb.agent.compiler.compile_long_doc", long_)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "notes.md"},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recompiled"] == 0
    assert body["skipped"] == 1
    assert body["docs"][0]["status"] == "error"


def test_recompile_stream_per_doc_events(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir)
    _patch_recompile(monkeypatch)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "notes.md", "stream": True},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events_from_sse(response.text)
    names = [e["event"] for e in events]
    assert names[0] == "start"
    assert names[-1] == "done"
    assert "doc" in names and "final" in names


def test_recompile_stream_not_found(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    _seed_short(kb_dir)

    response = client.post(
        "/api/v1/recompile",
        json={"kb": kb, "doc_name": "ghost", "stream": True},
        headers=_auth(),
    )
    events = _events_from_sse(response.text)
    assert any(e["event"] == "error" and e["data"].get("code") == 404 for e in events)


def test_cors_wildcard_disables_credentials(monkeypatch):
    """allow_origins=[*] must force allow_credentials=False (CORS spec).
    A wildcard with credentials lets any site send credentialed requests."""
    monkeypatch.setenv("OPENKB_CORS_ORIGINS", "*")
    from fastapi import FastAPI

    from openkb.api import _configure_cors

    app = FastAPI()
    _configure_cors(app)
    # Find the CORS middleware in the stack
    cors_mw = None
    for mw in app.user_middleware:
        if "CORSMiddleware" in str(mw.cls):
            cors_mw = mw
            break
    assert cors_mw is not None
    assert cors_mw.kwargs.get("allow_credentials") is False
    assert cors_mw.kwargs.get("allow_origins") == ["*"]


def test_cors_explicit_origins_keep_credentials(monkeypatch):
    """Explicit origins allow credentials (normal case)."""
    monkeypatch.setenv("OPENKB_CORS_ORIGINS", "http://localhost:3000")
    from fastapi import FastAPI

    from openkb.api import _configure_cors

    app = FastAPI()
    _configure_cors(app)
    cors_mw = None
    for mw in app.user_middleware:
        if "CORSMiddleware" in str(mw.cls):
            cors_mw = mw
            break
    assert cors_mw.kwargs.get("allow_credentials") is True


def test_token_compare_digest_accepts_correct(monkeypatch, kb_dir):
    """compare_digest should accept the correct token and reject wrong ones."""
    monkeypatch.setenv("OPENKB_API_TOKEN", "secret")
    client = TestClient(create_app())
    # Wrong token -> 401
    r = client.get("/api/v1/kbs", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    # Correct token -> 200
    r = client.get("/api/v1/kbs", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200


def test_meta_endpoint_returns_real_version(monkeypatch):
    import openkb

    client = _client(monkeypatch)
    response = client.get("/api/v1/meta", headers=_auth())

    assert response.status_code == 200
    body = response.json()
    assert body["version"] == openkb.__version__
    assert isinstance(body["version"], str) and body["version"]


def test_concurrent_same_kb_lint_serialized(monkeypatch, kb_dir):
    """Two concurrent lint requests to the same KB must not overlap.

    Drives the real _kb_mutation_lock closure inside create_app() via two
    concurrent ASGI requests. Without per-KB serialization, both would run
    simultaneously and max_seen would be 2.
    """
    import asyncio

    import httpx

    monkeypatch.setenv("OPENKB_API_TOKEN", "secret")
    _use_named_kb(monkeypatch, kb_dir)

    active = 0
    max_seen = 0

    async def slow_lint(kb_dir_arg, *, fix, **kwargs):
        nonlocal active, max_seen
        active += 1
        max_seen = max(max_seen, active)
        await asyncio.sleep(0.1)
        active -= 1
        return {"skipped": False, "message": "ok"}

    monkeypatch.setattr("openkb.api.run_lint_report", slow_lint)

    app = create_app()

    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1, r2 = await asyncio.gather(
                client.post("/api/v1/lint", json={"kb": "test-kb", "fix": True}, headers=_auth()),
                client.post("/api/v1/lint", json={"kb": "test-kb", "fix": True}, headers=_auth()),
            )
        return r1, r2

    r1, r2 = asyncio.run(main())
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert max_seen == 1, f"expected max 1 concurrent lint, got {max_seen} (lock not serializing)"


def test_concurrent_readonly_lint_not_serialized(monkeypatch, kb_dir):
    """Two concurrent read-only lint (fix=False) requests to the same KB may overlap.

    Only fix=True takes the per-KB mutation lock; read-only lint is a report
    and must not be serialized, otherwise read-only lint is over-constrained.
    """
    import asyncio

    import httpx

    monkeypatch.setenv("OPENKB_API_TOKEN", "secret")
    _use_named_kb(monkeypatch, kb_dir)

    active = 0
    max_seen = 0

    async def slow_lint(kb_dir_arg, *, fix, **kwargs):
        nonlocal active, max_seen
        active += 1
        max_seen = max(max_seen, active)
        await asyncio.sleep(0.1)
        active -= 1
        return {"skipped": False, "message": "ok"}

    monkeypatch.setattr("openkb.api.run_lint_report", slow_lint)

    app = create_app()

    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1, r2 = await asyncio.gather(
                client.post("/api/v1/lint", json={"kb": "test-kb", "fix": False}, headers=_auth()),
                client.post("/api/v1/lint", json={"kb": "test-kb", "fix": False}, headers=_auth()),
            )
        return r1, r2

    r1, r2 = asyncio.run(main())
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert max_seen == 2, (
        f"expected max 2 concurrent read-only lint, got {max_seen} (over-serialized)"
    )


def test_resolve_credential_bundle_reads_kb_key(monkeypatch, kb_dir):
    """resolve_credential_bundle reads the KB's LLM_API_KEY without polluting os.environ."""
    import os

    from openkb.config import resolve_credential_bundle

    monkeypatch.setenv("LLM_API_KEY", "server-key")
    (kb_dir / ".env").write_text("LLM_API_KEY=kb-specific-key\n", encoding="utf-8")

    bundle = resolve_credential_bundle(kb_dir)
    assert bundle.api_key == "kb-specific-key"
    # os.environ must NOT be polluted (unlike the old _scoped_llm_key).
    assert os.environ.get("LLM_API_KEY") == "server-key"


def test_query_endpoint_passes_kb_key_in_bundle(monkeypatch, kb_dir):
    """The query endpoint must pass the KB's key in the bundle, not via os.environ."""
    import os

    monkeypatch.setenv("OPENKB_API_TOKEN", "secret")
    monkeypatch.setenv("LLM_API_KEY", "server-key")
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / ".env").write_text("LLM_API_KEY=kb-specific-key\n", encoding="utf-8")

    captured = {}

    async def fake_run_query(question, kbd, model, stream=False, **kwargs):
        bundle = kwargs.get("bundle")
        captured["bundle_key"] = bundle.api_key if bundle else None
        captured["env_key"] = os.environ.get("LLM_API_KEY")
        return "answer"

    monkeypatch.setattr("openkb.api.run_query", fake_run_query)

    client = TestClient(create_app())
    r = client.post(
        "/api/v1/query",
        json={"kb": kb, "question": "q", "stream": False},
        headers=_auth(),
    )
    assert r.status_code == 200
    assert captured.get("bundle_key") == "kb-specific-key"
    # os.environ must remain the server key (no env mutation).
    assert captured.get("env_key") == "server-key"


def test_resolve_credential_bundle_no_env_returns_none(monkeypatch, kb_dir):
    """With no KB .env, no process env, and no global .env, api_key is None."""
    from openkb.config import resolve_credential_bundle

    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_BASE", raising=False)
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", kb_dir / "no-global")
    bundle = resolve_credential_bundle(kb_dir)
    assert bundle.api_key is None


def test_resolve_credential_bundle_falls_back_to_process_env(monkeypatch, kb_dir):
    """With no KB-local key, the bundle falls back to the process environment.

    Keeps Docker/systemd deployments that set LLM_API_KEY in the server's
    environment working, without mutating os.environ.
    """
    import os

    from openkb.config import resolve_credential_bundle

    monkeypatch.setenv("LLM_API_KEY", "server-key")
    # No kb_dir/.env written — the KB has no local key of its own. The process
    # env wins over any global .env, so this is deterministic on any machine.
    bundle = resolve_credential_bundle(kb_dir)
    assert bundle.api_key == "server-key"
    assert os.environ.get("LLM_API_KEY") == "server-key"


def test_concurrent_same_kb_recompile_serialized(monkeypatch, kb_dir):
    """Two concurrent non-streaming recompile requests to the same KB must not overlap.

    Mirrors test_concurrent_same_kb_lint_serialized but for the recompile path,
    which also relies on the per-KB asyncio.Lock. Without serialization both
    async generators would be iterated concurrently and max_seen would be 2.
    """
    import asyncio

    import httpx

    monkeypatch.setenv("OPENKB_API_TOKEN", "secret")
    _use_named_kb(monkeypatch, kb_dir)

    active = 0
    max_seen = 0

    async def slow_iter_recompile(
        kb_dir_arg, doc_name, *, all_docs, dry_run, refresh_schema, **kwargs
    ):
        nonlocal active, max_seen
        active += 1
        max_seen = max(max_seen, active)
        await asyncio.sleep(0.1)
        active -= 1
        yield {"event": "final", "recompiled": 0, "skipped": 0}

    monkeypatch.setattr("openkb.api.iter_recompile", slow_iter_recompile)

    app = create_app()

    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1, r2 = await asyncio.gather(
                client.post(
                    "/api/v1/recompile", json={"kb": "test-kb", "all_docs": True}, headers=_auth()
                ),
                client.post(
                    "/api/v1/recompile", json={"kb": "test-kb", "all_docs": True}, headers=_auth()
                ),
            )
        return r1, r2

    r1, r2 = asyncio.run(main())
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert max_seen == 1, (
        f"expected max 1 concurrent recompile, got {max_seen} (lock not serializing)"
    )


def test_concurrent_different_kbs_do_not_block(monkeypatch, kb_dir, tmp_path_factory):
    """Concurrent recompiles on different KBs must not serialize against each other.

    The per-KB lock is keyed by KB name, so two distinct KBs should run
    concurrently (max_seen == 2). This guards against an accidental single
    global lock that would serialize cross-KB traffic.
    """
    import asyncio

    import httpx

    monkeypatch.setenv("OPENKB_API_TOKEN", "secret")
    kb_dir_a = kb_dir
    kb_dir_b = tmp_path_factory.mktemp("kb-b")
    for d in (kb_dir_a, kb_dir_b):
        (d / ".openkb").mkdir(parents=True, exist_ok=True)
        (d / "wiki").mkdir(parents=True, exist_ok=True)

    def resolve(kb):
        return kb_dir_a if kb == "kb-a" else kb_dir_b

    monkeypatch.setattr("openkb.api_helpers.resolve_kb_alias", resolve)

    active = 0
    max_seen = 0

    async def slow_iter_recompile(
        kb_dir_arg, doc_name, *, all_docs, dry_run, refresh_schema, **kwargs
    ):
        nonlocal active, max_seen
        active += 1
        max_seen = max(max_seen, active)
        await asyncio.sleep(0.1)
        active -= 1
        yield {"event": "final", "recompiled": 0, "skipped": 0}

    monkeypatch.setattr("openkb.api.iter_recompile", slow_iter_recompile)

    app = create_app()

    async def main():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1, r2 = await asyncio.gather(
                client.post(
                    "/api/v1/recompile", json={"kb": "kb-a", "all_docs": True}, headers=_auth()
                ),
                client.post(
                    "/api/v1/recompile", json={"kb": "kb-b", "all_docs": True}, headers=_auth()
                ),
            )
        return r1, r2

    r1, r2 = asyncio.run(main())
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert max_seen == 2, (
        f"expected 2 concurrent cross-KB recompiles, got {max_seen} (cross-KB serialized)"
    )


def test_graph_endpoint_returns_build_graph_shape(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / "wiki" / "concepts" / "a.md").write_text(
        "---\ntype: concept\n---\nSee [[concepts/b]].", encoding="utf-8"
    )
    (kb_dir / "wiki" / "concepts" / "b.md").write_text(
        "---\ntype: concept\n---\nNo links here.", encoding="utf-8"
    )

    response = client.post("/api/v1/graph", json={"kb": kb}, headers=_auth())

    assert response.status_code == 200
    body = response.json()
    assert {n["id"] for n in body["nodes"]} == {"concepts/a", "concepts/b"}
    assert body["edges"] == [{"source": "concepts/a", "target": "concepts/b"}]
    assert body["types"] == ["concept"]


def test_graph_endpoint_empty_wiki_returns_empty_shape(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post("/api/v1/graph", json={"kb": kb}, headers=_auth())

    assert response.status_code == 200
    assert response.json() == {"nodes": [], "edges": [], "types": []}


def test_graph_html_endpoint_returns_self_contained_html(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / "wiki" / "concepts" / "a.md").write_text(
        "---\ntype: concept\n---\nSee [[concepts/b]].", encoding="utf-8"
    )
    (kb_dir / "wiki" / "concepts" / "b.md").write_text(
        "---\ntype: concept\n---\nNo links here.", encoding="utf-8"
    )

    response = client.get("/api/v1/graph/html", params={"kb": kb}, headers=_auth())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    # Self-contained template rendered, placeholder substituted with real data.
    assert "<title>openkb · knowledge graph</title>" in body
    assert "__GRAPH_DATA__" not in body
    assert "concepts/a" in body  # a node id from the injected graph JSON


def test_graph_html_endpoint_empty_wiki_still_renders(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.get("/api/v1/graph/html", params={"kb": kb}, headers=_auth())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>openkb · knowledge graph</title>" in response.text


def test_output_endpoint_serves_output_html(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    out = kb_dir / "output"
    out.mkdir(parents=True, exist_ok=True)
    (out / "nvda-guizang-test.html").write_text("<html><body>hi</body></html>", encoding="utf-8")

    response = client.get(
        "/api/v1/output",
        params={"kb": kb, "path": "output/nvda-guizang-test.html"},
        headers=_auth(),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "hi" in response.text


def test_output_endpoint_404_for_missing_file(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    response = client.get(
        "/api/v1/output", params={"kb": kb, "path": "output/nope.html"}, headers=_auth()
    )
    assert response.status_code == 404


def test_output_endpoint_400_for_traversal(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    response = client.get(
        "/api/v1/output",
        params={"kb": kb, "path": "output/../../etc/passwd"},
        headers=_auth(),
    )
    assert response.status_code == 400


def test_output_endpoint_400_for_non_viewable_type(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / "wiki" / "index.md").write_text("# hi\n", encoding="utf-8")
    response = client.get(
        "/api/v1/output", params={"kb": kb, "path": "wiki/index.md"}, headers=_auth()
    )
    assert response.status_code == 400


def test_output_endpoint_400_for_wiki_prefix(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / "wiki" / "x.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    response = client.get(
        "/api/v1/output", params={"kb": kb, "path": "wiki/x.html"}, headers=_auth()
    )
    assert response.status_code == 400


def test_output_endpoint_400_for_absolute_path(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    response = client.get(
        "/api/v1/output", params={"kb": kb, "path": "/etc/passwd"}, headers=_auth()
    )
    assert response.status_code == 400


def test_output_endpoint_requires_token(monkeypatch, kb_dir):
    client = _client(monkeypatch)  # token enforced (default "secret")
    kb = _use_named_kb(monkeypatch, kb_dir)
    response = client.get("/api/v1/output", params={"kb": kb, "path": "output/x.html"})
    assert response.status_code == 401


def test_page_endpoint_returns_content(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / "wiki" / "concepts" / "a.md").write_text("# A\n\nHello.", encoding="utf-8")

    response = client.post("/api/v1/page", json={"kb": kb, "path": "concepts/a"}, headers=_auth())

    assert response.status_code == 200
    assert response.json() == {"path": "concepts/a", "content": "# A\n\nHello."}


def test_page_endpoint_404_on_missing_page(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post(
        "/api/v1/page", json={"kb": kb, "path": "concepts/nope"}, headers=_auth()
    )

    assert response.status_code == 404


def test_page_endpoint_rejects_path_traversal(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post(
        "/api/v1/page", json={"kb": kb, "path": "../../../etc/passwd"}, headers=_auth()
    )

    assert response.status_code == 400


def test_page_endpoint_rejects_absolute_path(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post("/api/v1/page", json={"kb": kb, "path": "/etc/passwd"}, headers=_auth())

    assert response.status_code == 400


def test_page_endpoint_rejects_mid_path_escape(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.post(
        "/api/v1/page",
        json={"kb": kb, "path": "concepts/../../../secret"},
        headers=_auth(),
    )

    assert response.status_code == 400


def test_deck_endpoint_non_stream_generates_artifact(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / "wiki" / "concepts" / "a.md").write_text("# A\n\nContent.", encoding="utf-8")

    async def fake_run(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "index.html").write_text("<html>deck</html>", encoding="utf-8")
        self.validation = None
        return self.output_dir

    monkeypatch.setattr("openkb.skill.generator.Generator.run", fake_run)

    response = client.post(
        "/api/v1/deck",
        json={"kb": kb, "name": "my-deck", "intent": "explain X", "stream": False},
        headers=_auth(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "my-deck"
    assert (kb_dir / "output" / "decks" / "my-deck" / "index.html").exists()


def test_deck_list_endpoint(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    deck_dir = kb_dir / "output" / "decks" / "existing-deck"
    deck_dir.mkdir(parents=True)
    (deck_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    response = client.get("/api/v1/deck", params={"kb": kb}, headers=_auth())

    assert response.status_code == 200
    assert [d["name"] for d in response.json()["decks"]] == ["existing-deck"]


def test_deck_download_endpoint_serves_html(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    deck_dir = kb_dir / "output" / "decks" / "my-deck"
    deck_dir.mkdir(parents=True)
    (deck_dir / "index.html").write_text("<html>hi</html>", encoding="utf-8")

    response = client.get("/api/v1/deck/my-deck", params={"kb": kb}, headers=_auth())

    assert response.status_code == 200
    assert response.text == "<html>hi</html>"


def test_deck_download_endpoint_404_unknown_name(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.get("/api/v1/deck/nope", params={"kb": kb}, headers=_auth())

    assert response.status_code == 404


def test_deck_download_rejects_path_traversal_name(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.get("/api/v1/deck/..%2F..%2Fetc", params={"kb": kb}, headers=_auth())

    assert response.status_code in (400, 404)


def test_skill_endpoint_non_stream_generates_artifact(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / "wiki" / "concepts" / "a.md").write_text("# A\n\nContent.", encoding="utf-8")

    async def fake_run(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "SKILL.md").write_text("---\nname: x\n---\nbody", encoding="utf-8")
        self.validation = None
        return self.output_dir

    monkeypatch.setattr("openkb.skill.generator.Generator.run", fake_run)

    response = client.post(
        "/api/v1/skill",
        json={"kb": kb, "name": "my-skill", "intent": "be an expert", "stream": False},
        headers=_auth(),
    )

    assert response.status_code == 200
    assert (kb_dir / "output" / "skills" / "my-skill" / "SKILL.md").exists()


def test_skill_archive_endpoint_returns_zip(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    skill_dir = kb_dir / "output" / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: my-skill\n---\nbody", encoding="utf-8")

    response = client.get("/api/v1/skill/my-skill/archive", params={"kb": kb}, headers=_auth())

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


# ---------------------------------------------------------------------------
# GET/PATCH /api/v1/kb/config
# ---------------------------------------------------------------------------


def test_kb_config_get_returns_fields_and_key_presence(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / ".env").write_text("LLM_API_KEY=secret123\n", encoding="utf-8")

    response = client.get("/api/v1/kb/config", params={"kb": kb}, headers=_auth())

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "gpt-5.4"
    assert body["has_api_key"] is True
    assert "secret123" not in response.text


def test_kb_config_patch_updates_model_only(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.patch(
        "/api/v1/kb/config", json={"kb": kb, "config": {"model": "claude-opus"}}, headers=_auth()
    )

    assert response.status_code == 200
    assert response.json()["model"] == "claude-opus"
    saved = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text())
    assert saved["model"] == "claude-opus"


def test_kb_config_patch_unknown_field_is_400(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.patch(
        "/api/v1/kb/config", json={"kb": kb, "config": {"modle": "x"}}, headers=_auth()
    )

    assert response.status_code == 400


def test_kb_config_patch_clears_api_key_on_explicit_null(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / ".env").write_text("LLM_API_KEY=secret123\n", encoding="utf-8")

    response = client.patch("/api/v1/kb/config", json={"kb": kb, "api_key": None}, headers=_auth())

    assert response.status_code == 200
    assert response.json()["has_api_key"] is False
    assert "LLM_API_KEY" not in (kb_dir / ".env").read_text()


def test_kb_config_patch_leaves_api_key_unchanged_when_field_absent(monkeypatch, kb_dir):
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / ".env").write_text("LLM_API_KEY=secret123\n", encoding="utf-8")

    response = client.patch(
        "/api/v1/kb/config", json={"kb": kb, "config": {"language": "en"}}, headers=_auth()
    )

    assert response.status_code == 200
    assert response.json()["has_api_key"] is True
    assert "LLM_API_KEY=secret123" in (kb_dir / ".env").read_text()


def test_kb_config_patch_rejects_bad_value_type(monkeypatch, kb_dir):
    """A wrong-typed config VALUE is a client error (400), not a 500, and must
    NOT be persisted — otherwise every future GET would re-read the corrupt
    value and 500 (a persisted-corruption + endpoint-DoS)."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.patch(
        "/api/v1/kb/config",
        json={"kb": kb, "config": {"pageindex_threshold": "abc"}},
        headers=_auth(),
    )

    assert response.status_code == 400
    assert "pageindex_threshold" in response.json()["detail"]

    # The bad value must not have reached disk: a follow-up GET still succeeds
    # and returns the original int threshold (proving no config.yaml corruption).
    follow_up = client.get("/api/v1/kb/config", params={"kb": kb}, headers=_auth())
    assert follow_up.status_code == 200
    assert follow_up.json()["pageindex_threshold"] == 20


def test_kb_config_patch_coerces_numeric_string_threshold(monkeypatch, kb_dir):
    """A coercible numeric-string threshold (``"20"``) is a VALID int patch and
    must persist as a native ``int`` on disk. Pydantic lax-coerces ``"20"→20``,
    but that coerced value MUST be what reaches config.yaml — persisting the raw
    string ``'20'`` would make ``converter.py`` do ``int >= str`` and crash the
    next long-document ingestion (persisted corruption, different vector)."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    response = client.patch(
        "/api/v1/kb/config",
        json={"kb": kb, "config": {"pageindex_threshold": "20"}},
        headers=_auth(),
    )

    # A numeric string is a coercible, valid int — this succeeds (not a 400).
    assert response.status_code == 200

    # The coerced NATIVE int must be what landed on disk, not the string "20".
    saved = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text())
    assert type(saved["pageindex_threshold"]) is int
    assert saved["pageindex_threshold"] == 20

    # Belt-and-suspenders: a follow-up GET reads it back as the int 20.
    follow_up = client.get("/api/v1/kb/config", params={"kb": kb}, headers=_auth())
    assert follow_up.status_code == 200
    assert follow_up.json()["pageindex_threshold"] == 20


def test_kb_config_patch_api_key_rotation_preserves_other_env_lines(monkeypatch, kb_dir):
    """Rotating LLM_API_KEY must rewrite only that line and leave co-existing
    ``.env`` entries intact, never leaking the key value in the GET response."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    (kb_dir / ".env").write_text("LLM_API_KEY=old\nPAGEINDEX_API_KEY=pk-123\n", encoding="utf-8")

    response = client.patch("/api/v1/kb/config", json={"kb": kb, "api_key": "new"}, headers=_auth())

    assert response.status_code == 200
    env_text = (kb_dir / ".env").read_text()
    assert "LLM_API_KEY=new" in env_text
    assert "PAGEINDEX_API_KEY=pk-123" in env_text
    assert "new" not in response.text

    follow_up = client.get("/api/v1/kb/config", params={"kb": kb}, headers=_auth())
    assert follow_up.status_code == 200
    assert follow_up.json()["has_api_key"] is True
    assert "new" not in follow_up.text


def test_kb_config_patch_null_removes_key(monkeypatch, kb_dir):
    """config: {model: null} removes the key from config.yaml (revert to
    inherited), not persist model: None."""
    from openkb.config import DEFAULT_CONFIG, resolve_effective_config

    # Isolate from any real ~/.config/openkb/global.yaml on the host so the
    # "falls back to default" assertion below is deterministic.
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_PATH", kb_dir / "global" / "global.yaml")
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", kb_dir / "global")

    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    client.patch(
        "/api/v1/kb/config", json={"kb": kb, "config": {"model": "kb-model"}}, headers=_auth()
    )
    response = client.patch(
        "/api/v1/kb/config", json={"kb": kb, "config": {"model": None}}, headers=_auth()
    )
    assert response.status_code == 200
    saved = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text())
    assert "model" not in saved

    # The key must be truly gone, not merely nulled: resolve_effective_config
    # now falls back to the default layer (no global override present here).
    effective, sources = resolve_effective_config(kb_dir)
    assert sources["model"] == "default"
    assert effective["model"] == DEFAULT_CONFIG["model"]


def test_kb_config_patch_field_omitted_leaves_key_unchanged(monkeypatch, kb_dir):
    """An OMITTED config field (not present in the patch at all) must be left
    unchanged — merge-patch semantics distinguish absent from explicit null."""
    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)
    client.patch(
        "/api/v1/kb/config", json={"kb": kb, "config": {"model": "kb-model"}}, headers=_auth()
    )

    response = client.patch(
        "/api/v1/kb/config", json={"kb": kb, "config": {"language": "en"}}, headers=_auth()
    )

    assert response.status_code == 200
    saved = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text())
    assert saved["model"] == "kb-model"
    assert saved["language"] == "en"


def test_kb_config_patch_does_not_materialize_defaults(monkeypatch, kb_dir):
    """A PATCH that sets only ONE scalar must NOT materialize the OTHER
    DEFAULT_CONFIG scalars into config.yaml.

    Regression test: ``apply_kb_config_patch`` used to read the KB config via
    ``load_config()`` — which returns ``dict(DEFAULT_CONFIG)`` merged with the
    KB's own file — and then ``save_config()`` the WHOLE merged dict back. That
    silently KB-pinned every default scalar (language, pageindex_threshold) to
    its default value on the very first single-field PATCH, permanently
    breaking global/default inheritance for fields the client never touched.
    The fix does a RAW read-modify-write of only the KB's explicit keys.
    """
    from openkb.config import DEFAULT_CONFIG, resolve_effective_config

    # Isolate from any real ~/.config/openkb/global.yaml on the host so the
    # "still inherits" assertions below are deterministic.
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_PATH", kb_dir / "global" / "global.yaml")
    monkeypatch.setattr("openkb.config.GLOBAL_CONFIG_DIR", kb_dir / "global")

    client = _client(monkeypatch)
    kb = _use_named_kb(monkeypatch, kb_dir)

    # The kb_dir fixture's config.yaml is silent on model/language/threshold —
    # it only carries its own unrelated explicit keys.
    before = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text())
    assert "model" not in before
    assert "language" not in before
    assert "pageindex_threshold" not in before

    response = client.patch(
        "/api/v1/kb/config", json={"kb": kb, "config": {"model": "claude-opus"}}, headers=_auth()
    )
    assert response.status_code == 200

    saved = yaml.safe_load((kb_dir / ".openkb" / "config.yaml").read_text())
    # The patched key landed...
    assert saved["model"] == "claude-opus"
    # ...the KB's pre-existing explicit keys survive untouched...
    for key, value in before.items():
        assert saved[key] == value
    # ...but NOTHING else was materialized: the only new key on disk is the
    # one the client actually patched. In particular the other DEFAULT_CONFIG
    # scalars must be absent, not pinned to their default values.
    assert set(saved) - set(before) == {"model"}
    assert "language" not in saved
    assert "pageindex_threshold" not in saved

    # And resolve_effective_config must still report the untouched scalars as
    # inheriting (source "default", since no global.yaml override is set here)
    # rather than "kb" — proving the KB config.yaml did not silently start
    # pinning them.
    effective, sources = resolve_effective_config(kb_dir)
    assert sources["model"] == "kb"
    assert sources["language"] == "default"
    assert sources["pageindex_threshold"] == "default"
    assert effective["language"] == DEFAULT_CONFIG["language"]
    assert effective["pageindex_threshold"] == DEFAULT_CONFIG["pageindex_threshold"]
