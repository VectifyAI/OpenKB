"""Direct unit tests for ``openkb.agent.chat._handle_slash_critique``.

The ``/critique <path>`` slash command is the user-facing entry point
for the html-critic skill. It does path resolution, file-not-found
gating, and translates SkillNotFoundError / RuntimeError into
user-visible error messages — none of that was exercised before.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from prompt_toolkit.styles import Style

from openkb.agent.chat import _handle_slash_critique
from openkb.agent.skill_runner import SkillNotFoundError


def _make_kb_with_config(tmp_path: Path) -> Path:
    """Critique needs a config.yaml to read the model from."""
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text(
        "model: openai/gpt-4o\nlanguage: en\n", encoding="utf-8"
    )
    return tmp_path


_STYLE = Style.from_dict({})


@pytest.mark.asyncio
async def test_critique_no_arg_prints_usage(tmp_path: Path, capsys):
    """``/critique`` with no arg must print Usage and NOT call run_skill."""
    kb_dir = _make_kb_with_config(tmp_path)
    with patch("openkb.agent.skill_runner.run_skill", new=AsyncMock()) as run_skill:
        await _handle_slash_critique("", kb_dir, _STYLE)
        # whitespace only — same as empty
        await _handle_slash_critique("   ", kb_dir, _STYLE)

    out = capsys.readouterr().out
    assert "Usage" in out or "usage" in out
    run_skill.assert_not_called()


@pytest.mark.asyncio
async def test_critique_missing_file_prints_error(tmp_path: Path, capsys):
    """When the target file doesn't exist, print an ERROR and skip
    run_skill — no point asking the critic to read a nonexistent file."""
    kb_dir = _make_kb_with_config(tmp_path)
    with patch("openkb.agent.skill_runner.run_skill", new=AsyncMock()) as run_skill:
        await _handle_slash_critique(
            "output/decks/ghost/index.html", kb_dir, _STYLE
        )

    out = capsys.readouterr().out
    assert "[ERROR]" in out
    assert "not found" in out.lower() or "ghost" in out
    run_skill.assert_not_called()


@pytest.mark.asyncio
async def test_critique_invokes_html_critic_skill(tmp_path: Path, capsys):
    """Happy path: file exists → run_skill called with the html-critic
    skill name and a path that includes the relative target."""
    kb_dir = _make_kb_with_config(tmp_path)
    target = kb_dir / "output" / "decks" / "real" / "index.html"
    target.parent.mkdir(parents=True)
    target.write_text("<html>existing deck</html>", encoding="utf-8")

    with patch("openkb.agent.skill_runner.run_skill", new=AsyncMock()) as run_skill:
        await _handle_slash_critique(
            "output/decks/real/index.html", kb_dir, _STYLE
        )

    run_skill.assert_called_once()
    kwargs = run_skill.call_args.kwargs
    assert kwargs["skill_name"] == "openkb-html-critic"
    assert "output/decks/real/index.html" in kwargs["intent"]
    assert kwargs["kb_dir"] == kb_dir
    out = capsys.readouterr().out
    assert "Critique pass complete" in out


@pytest.mark.asyncio
async def test_critique_accepts_absolute_path_inside_kb(tmp_path: Path):
    """Absolute paths under the KB are accepted and converted to the
    relative form for the skill's intent."""
    kb_dir = _make_kb_with_config(tmp_path)
    target = kb_dir / "output" / "decks" / "abs" / "index.html"
    target.parent.mkdir(parents=True)
    target.write_text("<html></html>", encoding="utf-8")

    with patch("openkb.agent.skill_runner.run_skill", new=AsyncMock()) as run_skill:
        await _handle_slash_critique(str(target), kb_dir, _STYLE)

    run_skill.assert_called_once()
    intent = run_skill.call_args.kwargs["intent"]
    # Either the relative-to-kb form or the absolute path is in the
    # intent — implementation may choose either, both reach the skill.
    assert "abs/index.html" in intent or str(target) in intent


@pytest.mark.asyncio
async def test_critique_catches_skill_not_found(tmp_path: Path, capsys):
    """If the openkb-html-critic skill is missing, surface a friendly
    [ERROR] line instead of crashing the chat turn."""
    kb_dir = _make_kb_with_config(tmp_path)
    target = kb_dir / "output" / "test.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<html></html>", encoding="utf-8")

    async def missing(**_):
        raise SkillNotFoundError(
            "Skill 'openkb-html-critic' not found. Available: foo."
        )

    with patch("openkb.agent.skill_runner.run_skill", new=AsyncMock(side_effect=missing)):
        # Should NOT raise — chat turn must survive
        await _handle_slash_critique(str(target), kb_dir, _STYLE)

    out = capsys.readouterr().out
    assert "[ERROR]" in out
    assert "openkb-html-critic" in out or "not found" in out.lower()


@pytest.mark.asyncio
async def test_critique_catches_runtime_error_from_run_skill(tmp_path: Path, capsys):
    """RuntimeError from run_skill (e.g. MaxTurnsExceeded translation)
    is surfaced as [ERROR] not propagated."""
    kb_dir = _make_kb_with_config(tmp_path)
    target = kb_dir / "output" / "test.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("<html></html>", encoding="utf-8")

    async def hits_cap(**_):
        raise RuntimeError("Skill 'openkb-html-critic' hit the 40-step cap")

    with patch("openkb.agent.skill_runner.run_skill", new=AsyncMock(side_effect=hits_cap)):
        await _handle_slash_critique(str(target), kb_dir, _STYLE)

    out = capsys.readouterr().out
    assert "[ERROR]" in out
    assert "step cap" in out or "step" in out.lower()
