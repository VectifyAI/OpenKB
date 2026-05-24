"""Click CLI tests for `openkb deck new`. Mocks Generator.run; no LLM."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from click.testing import CliRunner

from openkb.cli import cli


def _init_kb(tmp_path: Path) -> Path:
    """Create a minimal valid KB on disk so cli's _find_kb_dir resolves.

    Includes a non-empty ``wiki/concepts/`` so the shared
    ``_preflight_skill_new`` wiki-content gate passes.
    """
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text(
        "model: openai/gpt-4o\nlanguage: en\n", encoding="utf-8"
    )
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "AGENTS.md").write_text("schema", encoding="utf-8")
    (tmp_path / "wiki" / "concepts").mkdir()
    (tmp_path / "wiki" / "concepts" / "foo.md").write_text("# foo\n", encoding="utf-8")
    return tmp_path


def test_deck_new_help(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["deck", "new", "--help"])
    assert result.exit_code == 0
    assert "kebab-case" in result.output.lower() or "name" in result.output.lower()


def test_deck_new_rejects_no_kb(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no .openkb here
    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=None):
        result = runner.invoke(cli, ["deck", "new", "my-deck", "An intent."])
    assert result.exit_code != 0
    assert "knowledge base" in result.output.lower()


def test_deck_new_rejects_invalid_name(tmp_path: Path, monkeypatch):
    _init_kb(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["deck", "new", "Invalid_Name", "An intent."])
    assert result.exit_code != 0
    assert "kebab" in result.output.lower() or "invalid" in result.output.lower()


def test_deck_new_happy_path(tmp_path: Path, monkeypatch):
    _init_kb(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    fake_validation = type("V", (), {"errors": [], "warnings": [], "ok": True})()

    with patch("openkb.skill.generator.Generator") as gen_cls:
        gen = gen_cls.return_value
        gen.run = AsyncMock(return_value=tmp_path / "output" / "decks" / "my-deck")
        gen.validation = fake_validation
        gen.output_dir = tmp_path / "output" / "decks" / "my-deck"

        runner = CliRunner()
        result = runner.invoke(cli, ["deck", "new", "my-deck", "An intent."])

    assert result.exit_code == 0, result.output
    gen_cls.assert_called_once()
    kwargs = gen_cls.call_args.kwargs
    assert kwargs["target_type"] == "deck"
    assert kwargs["name"] == "my-deck"
    assert kwargs["intent"] == "An intent."
    assert kwargs["critique"] is False


def test_deck_new_passes_critique_flag(tmp_path: Path, monkeypatch):
    _init_kb(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    fake_validation = type("V", (), {"errors": [], "warnings": [], "ok": True})()

    with patch("openkb.skill.generator.Generator") as gen_cls:
        gen = gen_cls.return_value
        gen.run = AsyncMock(return_value=tmp_path / "output" / "decks" / "my-deck")
        gen.validation = fake_validation
        gen.output_dir = tmp_path / "output" / "decks" / "my-deck"

        runner = CliRunner()
        result = runner.invoke(cli, ["deck", "new", "--critique", "my-deck", "An intent."])

    assert result.exit_code == 0, result.output
    assert gen_cls.call_args.kwargs["critique"] is True


def test_deck_new_surfaces_validation_errors(tmp_path: Path, monkeypatch):
    _init_kb(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    fake_validation = type(
        "V", (), {"errors": ["bad slide count"], "warnings": [], "ok": False}
    )()

    with patch("openkb.skill.generator.Generator") as gen_cls:
        gen = gen_cls.return_value
        gen.run = AsyncMock(return_value=tmp_path / "output" / "decks" / "my-deck")
        gen.validation = fake_validation
        gen.output_dir = tmp_path / "output" / "decks" / "my-deck"

        runner = CliRunner()
        result = runner.invoke(cli, ["deck", "new", "my-deck", "An intent."])

    # Non-zero exit, error printed, but file preserved (validator semantics).
    assert result.exit_code != 0
    assert "bad slide count" in result.output


def test_deck_new_rejects_empty_wiki(tmp_path: Path, monkeypatch):
    """CLI must refuse to compile when the KB exists but the wiki is empty
    (parity with chat /deck new and CLI openkb skill new)."""
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text(
        "model: openai/gpt-4o\nlanguage: en\n", encoding="utf-8"
    )
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "AGENTS.md").write_text("schema", encoding="utf-8")
    # No wiki/concepts/, no wiki/summaries/ — wiki is "empty".
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    # Patch Generator so that, if preflight is missing, the test would still
    # exit 0 — making this test only pass once the wiki-content gate is wired.
    fake_validation = type("V", (), {"errors": [], "warnings": [], "ok": True})()
    with patch("openkb.skill.generator.Generator") as gen_cls:
        gen = gen_cls.return_value
        gen.run = AsyncMock(return_value=tmp_path / "output" / "decks" / "my-deck")
        gen.validation = fake_validation
        gen.output_dir = tmp_path / "output" / "decks" / "my-deck"

        runner = CliRunner()
        result = runner.invoke(cli, ["deck", "new", "my-deck", "An intent."])

    # Should exit non-zero before ever instantiating Generator.
    assert result.exit_code != 0, result.output
    gen_cls.assert_not_called()
    # Error message should point at the empty wiki, not at name validation.
    assert "wiki" in result.output.lower()
    assert "kebab" not in result.output.lower()
