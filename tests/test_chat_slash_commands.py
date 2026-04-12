"""Tests for slash commands in the chat REPL."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from prompt_toolkit.styles import Style

from openkb.agent.chat import _handle_slash, _run_add, run_chat
from openkb.agent.chat_session import ChatSession


def _setup_kb(tmp_path: Path) -> Path:
    """Create a minimal KB structure and return kb_dir."""
    kb_dir = tmp_path
    (kb_dir / "raw").mkdir()
    (kb_dir / "wiki" / "sources" / "images").mkdir(parents=True)
    (kb_dir / "wiki" / "summaries").mkdir(parents=True)
    (kb_dir / "wiki" / "concepts").mkdir(parents=True)
    (kb_dir / "wiki" / "reports").mkdir(parents=True)
    openkb_dir = kb_dir / ".openkb"
    openkb_dir.mkdir()
    (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
    (openkb_dir / "hashes.json").write_text(json.dumps({}))
    return kb_dir


def _make_session(kb_dir: Path) -> ChatSession:
    return ChatSession.new(kb_dir, "gpt-4o-mini", "en")


_STYLE = Style.from_dict({})


def _collect_fmt():
    """Return (patch, collected) where collected is a list of printed strings."""
    collected: list[str] = []

    def _fake_fmt(_style, *fragments):
        for _cls, text in fragments:
            collected.append(text)

    return patch("openkb.agent.chat._fmt", _fake_fmt), collected


# --- /status and /list use click.echo, captured by capsys ---


@pytest.mark.asyncio
async def test_slash_status(tmp_path, capsys):
    kb_dir = _setup_kb(tmp_path)
    session = _make_session(kb_dir)
    result = await _handle_slash("/status", kb_dir, session, _STYLE)
    assert result is None
    output = capsys.readouterr().out
    assert "Knowledge Base Status" in output


@pytest.mark.asyncio
async def test_slash_list_empty(tmp_path, capsys):
    kb_dir = _setup_kb(tmp_path)
    session = _make_session(kb_dir)
    result = await _handle_slash("/list", kb_dir, session, _STYLE)
    assert result is None
    output = capsys.readouterr().out
    assert "No documents indexed yet" in output


@pytest.mark.asyncio
async def test_slash_list_with_docs(tmp_path, capsys):
    kb_dir = _setup_kb(tmp_path)
    hashes = {"abc": {"name": "paper.pdf", "type": "pdf"}}
    (kb_dir / ".openkb" / "hashes.json").write_text(json.dumps(hashes))
    session = _make_session(kb_dir)
    result = await _handle_slash("/list", kb_dir, session, _STYLE)
    assert result is None
    output = capsys.readouterr().out
    assert "paper.pdf" in output


# --- /add, /exit, /clear, /help, /unknown use _fmt → need patching ---


@pytest.mark.asyncio
async def test_slash_add_missing_arg(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    session = _make_session(kb_dir)
    p, collected = _collect_fmt()
    with p:
        result = await _handle_slash("/add", kb_dir, session, _STYLE)
    assert result is None
    assert any("Usage: /add <path>" in s for s in collected)


@pytest.mark.asyncio
async def test_slash_add_nonexistent_path(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    session = _make_session(kb_dir)
    p, collected = _collect_fmt()
    with p:
        result = await _handle_slash("/add /no/such/path", kb_dir, session, _STYLE)
    assert result is None
    assert any("Path does not exist" in s for s in collected)


@pytest.mark.asyncio
async def test_slash_add_unsupported_type(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    bad_file = tmp_path / "file.xyz"
    bad_file.write_text("data")
    session = _make_session(kb_dir)
    p, collected = _collect_fmt()
    with p:
        result = await _handle_slash(f"/add {bad_file}", kb_dir, session, _STYLE)
    assert result is None
    assert any("Unsupported file type" in s for s in collected)


@pytest.mark.asyncio
async def test_slash_add_single_file(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    doc = tmp_path / "test.md"
    doc.write_text("# Hello")
    p, _collected = _collect_fmt()
    with p, patch("openkb.cli.add_single_file") as mock_add:
        await _run_add(str(doc), kb_dir, _STYLE)
        mock_add.assert_called_once_with(doc, kb_dir)


@pytest.mark.asyncio
async def test_slash_add_directory_with_progress(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a.md").write_text("# A")
    (docs_dir / "b.txt").write_text("B")
    (docs_dir / "skip.xyz").write_text("skip")
    p, collected = _collect_fmt()
    with p, patch("openkb.cli.add_single_file") as mock_add:
        await _run_add(str(docs_dir), kb_dir, _STYLE)
        assert mock_add.call_count == 2
    output = "".join(collected)
    assert "Found 2 supported file(s)" in output
    assert "[1/2]" in output
    assert "[2/2]" in output


@pytest.mark.asyncio
async def test_slash_lint(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    session = _make_session(kb_dir)
    with patch("openkb.cli.run_lint", new_callable=AsyncMock, return_value=tmp_path / "report.md"):
        result = await _handle_slash("/lint", kb_dir, session, _STYLE)
    assert result is None


@pytest.mark.asyncio
async def test_run_chat_handles_ctrl_c_during_slash_command(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    session = _make_session(kb_dir)

    class _FakePromptSession:
        def __init__(self) -> None:
            self.calls = 0

        async def prompt_async(self) -> str:
            self.calls += 1
            if self.calls == 1:
                return "/lint"
            raise EOFError

    prompt = _FakePromptSession()
    p, collected = _collect_fmt()
    with (
        p,
        patch("openkb.agent.chat.build_query_agent", return_value=object()),
        patch("openkb.agent.chat._print_header"),
        patch("openkb.agent.chat._make_prompt_session", return_value=prompt),
        patch("openkb.agent.chat._handle_slash", new_callable=AsyncMock, side_effect=KeyboardInterrupt),
    ):
        await run_chat(kb_dir, session, no_color=True)

    assert prompt.calls == 2
    assert any("[aborted]" in s for s in collected)


@pytest.mark.asyncio
async def test_slash_unknown(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    session = _make_session(kb_dir)
    p, collected = _collect_fmt()
    with p:
        result = await _handle_slash("/foobar", kb_dir, session, _STYLE)
    assert result is None
    assert any("Unknown command" in s for s in collected)


@pytest.mark.asyncio
async def test_slash_exit(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    session = _make_session(kb_dir)
    p, _collected = _collect_fmt()
    with p:
        result = await _handle_slash("/exit", kb_dir, session, _STYLE)
    assert result == "exit"


@pytest.mark.asyncio
async def test_slash_clear(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    session = _make_session(kb_dir)
    p, _collected = _collect_fmt()
    with p:
        result = await _handle_slash("/clear", kb_dir, session, _STYLE)
    assert result == "new_session"
