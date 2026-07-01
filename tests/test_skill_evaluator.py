"""Tests for openkb.skill.evaluator.

The Runner.run call is mocked everywhere — no real LLM tokens spent.
What we DO verify:
  * Description extraction from SKILL.md frontmatter
  * Generator output parsing (with + without code fences)
  * Grader response handling (uppercase / lowercase / ambiguous)
  * End-to-end run_eval with mocked grading
  * Save/load round-trip for persisted eval sets
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openkb.skill.evaluator import (
    EvalPrompt,
    EvalResult,
    _read_description,
    generate_eval_set,
    grade_coverage,
    grade_one,
    load_eval_set,
    run_eval,
    save_eval_set,
)


def _make_skill(tmp_path: Path, *, description: str | None = "Triggers for foo questions.") -> Path:
    """Create a minimal SKILL.md and return the skill directory."""
    skill_dir = tmp_path / "output" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    if description is None:
        body = "---\nname: demo\n---\n\n# demo\n"
    else:
        body = f"---\nname: demo\ndescription: {description}\n---\n\n# demo\n"
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    return skill_dir


# -------- _read_description ---------------------------------------------------


def test_read_description_extracts_field(tmp_path):
    skill_dir = _make_skill(tmp_path, description="Distill thoughts about transformers.")
    assert _read_description(skill_dir) == "Distill thoughts about transformers."


def test_read_description_raises_on_missing_frontmatter(tmp_path):
    skill_dir = tmp_path / "output" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# demo\n\nNo frontmatter at all.\n")
    with pytest.raises(RuntimeError, match="frontmatter"):
        _read_description(skill_dir)


def test_read_description_raises_on_missing_description_field(tmp_path):
    skill_dir = _make_skill(tmp_path, description=None)
    with pytest.raises(RuntimeError, match="description"):
        _read_description(skill_dir)


# -------- generate_eval_set ---------------------------------------------------


def _fake_generator_payload(count: int = 10) -> str:
    trig = [f"trigger question {i}" for i in range(count)]
    no = [f"unrelated question {i}" for i in range(count)]
    return json.dumps({"should_trigger": trig, "should_not": no})


@pytest.mark.asyncio
async def test_generate_eval_set_parses_plain_json(tmp_path):
    skill_dir = _make_skill(tmp_path)

    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output=_fake_generator_payload(10))

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        prompts = await generate_eval_set(skill_dir, model="gpt-4o-mini", count=10)

    assert len(prompts) == 20
    assert sum(1 for p in prompts if p.expected == "trigger") == 10
    assert sum(1 for p in prompts if p.expected == "no-trigger") == 10
    assert prompts[0].question == "trigger question 0"
    assert prompts[10].question == "unrelated question 0"


@pytest.mark.asyncio
async def test_generate_eval_set_strips_code_fences(tmp_path):
    skill_dir = _make_skill(tmp_path)
    fenced = "```json\n" + _fake_generator_payload(3) + "\n```"

    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output=fenced)

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        prompts = await generate_eval_set(skill_dir, model="gpt-4o-mini", count=3)

    assert len(prompts) == 6


# -------- grade_one -----------------------------------------------------------


@pytest.mark.asyncio
async def test_grade_one_returns_trigger_for_trigger_response():
    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output="TRIGGER")

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        out = await grade_one("desc", "question?", model="gpt-4o-mini")
    assert out == "trigger"


@pytest.mark.asyncio
async def test_grade_one_returns_no_trigger_for_negative_response():
    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output="NO-TRIGGER")

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        out = await grade_one("desc", "question?", model="gpt-4o-mini")
    assert out == "no-trigger"


@pytest.mark.asyncio
async def test_grade_one_handles_mixed_case():
    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output="trigger")

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        out = await grade_one("desc", "question?", model="gpt-4o-mini")
    assert out == "trigger"


@pytest.mark.asyncio
async def test_grade_one_handles_space_variant():
    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output="No Trigger")

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        out = await grade_one("desc", "question?", model="gpt-4o-mini")
    assert out == "no-trigger"


@pytest.mark.asyncio
async def test_grade_one_defaults_to_no_trigger_on_ambiguous_output():
    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output="hmm not sure")

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        out = await grade_one("desc", "question?", model="gpt-4o-mini")
    assert out == "no-trigger"


# -------- run_eval ------------------------------------------------------------


def _build_eval_set(n_trig: int = 3, n_no: int = 3) -> list[EvalPrompt]:
    prompts: list[EvalPrompt] = []
    for i in range(n_trig):
        prompts.append(EvalPrompt(question=f"trig {i}", expected="trigger"))
    for i in range(n_no):
        prompts.append(EvalPrompt(question=f"no {i}", expected="no-trigger"))
    return prompts


async def _supported_coverage(content, question, *, model):
    return "supported", ""


@pytest.mark.asyncio
async def test_run_eval_happy_path_all_correct(tmp_path):
    skill_dir = _make_skill(tmp_path)
    eval_set = _build_eval_set(3, 3)

    async def fake_grade(description, question, *, model):
        # Return the ground truth label — perfect grader.
        match = next(p for p in eval_set if p.question == question)
        return match.expected

    with (
        patch("openkb.skill.evaluator.grade_one", side_effect=fake_grade),
        patch("openkb.skill.evaluator.grade_coverage", side_effect=_supported_coverage),
    ):
        result = await run_eval(skill_dir, model="gpt-4o-mini", eval_set=eval_set)

    assert isinstance(result, EvalResult)
    assert result.total == 6
    assert result.passed == 6
    assert result.pass_rate == 1.0
    assert result.misses == []
    # Body coverage was graded only on the 3 trigger prompts, all supported.
    assert result.trigger_questions == 3
    assert result.coverage_passed == 3
    assert result.coverage_rate == 1.0
    assert result.coverage_misses == []


@pytest.mark.asyncio
async def test_run_eval_reports_misses(tmp_path):
    skill_dir = _make_skill(tmp_path)
    eval_set = _build_eval_set(3, 3)

    # Grader always says "trigger" — so the 3 no-trigger prompts will miss.
    async def fake_grade(description, question, *, model):
        return "trigger"

    with (
        patch("openkb.skill.evaluator.grade_one", side_effect=fake_grade),
        patch("openkb.skill.evaluator.grade_coverage", side_effect=_supported_coverage),
    ):
        result = await run_eval(skill_dir, model="gpt-4o-mini", eval_set=eval_set)

    assert result.total == 6
    assert result.passed == 3
    assert len(result.misses) == 3
    assert all(m.prompt.expected == "no-trigger" for m in result.misses)
    assert all(m.graded == "trigger" for m in result.misses)
    assert result.pass_rate == pytest.approx(0.5)
    # EvalMiss.label sanity check
    assert "no-trigger" in result.misses[0].label
    assert "trigger" in result.misses[0].label


@pytest.mark.asyncio
async def test_run_eval_reports_coverage_gaps(tmp_path):
    """Body alignment must catch the hollow-body case: description fires
    correctly but the body cannot support the questions it claims."""
    skill_dir = _make_skill(tmp_path)
    eval_set = _build_eval_set(3, 3)

    async def perfect_trigger(description, question, *, model):
        match = next(p for p in eval_set if p.question == question)
        return match.expected

    async def hollow_coverage(content, question, *, model):
        # Body claims to support but actually doesn't for the first two
        # trigger prompts.
        if question in {"trig 0", "trig 1"}:
            return "unsupported", "body has no material"
        return "supported", ""

    with (
        patch("openkb.skill.evaluator.grade_one", side_effect=perfect_trigger),
        patch("openkb.skill.evaluator.grade_coverage", side_effect=hollow_coverage),
    ):
        result = await run_eval(skill_dir, model="gpt-4o-mini", eval_set=eval_set)

    # Trigger accuracy is still perfect.
    assert result.passed == 6
    # But coverage catches the hollow shell.
    assert result.trigger_questions == 3
    assert len(result.coverage_misses) == 2
    assert {g.prompt.question for g in result.coverage_misses} == {"trig 0", "trig 1"}
    assert all(g.reason == "body has no material" for g in result.coverage_misses)
    assert result.coverage_rate == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_grade_coverage_parses_supported_verdict():
    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output="VERDICT: SUPPORTED\nREASON: body covers this directly")

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        verdict, reason = await grade_coverage("body content", "question?", model="gpt-4o-mini")
    assert verdict == "supported"
    assert reason == "body covers this directly"


@pytest.mark.asyncio
async def test_grade_coverage_reports_ambiguous_on_unparseable_output():
    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output="hmm not sure")

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        verdict, reason = await grade_coverage("body", "q?", model="gpt-4o-mini")
    # Ambiguous is a third state — not collapsed into unsupported, so
    # grader-malfunction doesn't silently inflate coverage_misses.
    assert verdict == "ambiguous"
    assert "unparseable grader output" in reason


@pytest.mark.asyncio
async def test_run_eval_segregates_ambiguous_from_coverage_misses(tmp_path):
    """An ambiguous coverage verdict goes into ``coverage_ambiguous``, not
    ``coverage_misses``, and is excluded from the coverage_rate denominator."""
    skill_dir = _make_skill(tmp_path)
    eval_set = _build_eval_set(3, 0)  # 3 trigger, 0 no-trigger

    async def perfect_trigger(description, question, *, model):
        return "trigger"

    async def mixed_coverage(content, question, *, model):
        # trig 0 -> supported, trig 1 -> unsupported, trig 2 -> ambiguous
        if question == "trig 0":
            return "supported", ""
        if question == "trig 1":
            return "unsupported", "body gap"
        return "ambiguous", "unparseable grader output: 'xxx'"

    with (
        patch("openkb.skill.evaluator.grade_one", side_effect=perfect_trigger),
        patch("openkb.skill.evaluator.grade_coverage", side_effect=mixed_coverage),
    ):
        result = await run_eval(skill_dir, model="gpt-4o-mini", eval_set=eval_set)

    assert result.trigger_questions == 3
    assert len(result.coverage_misses) == 1  # only the "unsupported" one
    assert len(result.coverage_ambiguous) == 1
    # Score 1 supported out of (3 - 1 ambiguous) = 1/2
    assert result.coverage_passed == 1
    assert result.coverage_rate == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_run_eval_captures_grader_failures_without_aborting(tmp_path):
    """A single grader failure must NOT abort the whole eval. The failed
    prompt goes into trigger_errors/coverage_errors and is excluded from
    rate denominators; the other prompts grade normally."""
    skill_dir = _make_skill(tmp_path)
    eval_set = _build_eval_set(2, 2)  # 2 trigger + 2 no-trigger

    async def flaky_trigger(description, question, *, model):
        if question == "trig 0":
            raise RuntimeError("max-turn cap hit")
        # Everything else: perfect grader
        match = next(p for p in eval_set if p.question == question)
        return match.expected

    async def flaky_coverage(content, question, *, model):
        if question == "trig 1":
            raise RuntimeError("malformed grader output")
        return "supported", ""

    with (
        patch("openkb.skill.evaluator.grade_one", side_effect=flaky_trigger),
        patch("openkb.skill.evaluator.grade_coverage", side_effect=flaky_coverage),
    ):
        result = await run_eval(skill_dir, model="gpt-4o-mini", eval_set=eval_set)

    # 4 prompts total; one trigger errored, one coverage errored.
    assert result.total == 4
    assert len(result.trigger_errors) == 1
    assert result.trigger_errors[0].prompt.question == "trig 0"
    assert "max-turn cap" in result.trigger_errors[0].reason
    assert len(result.coverage_errors) == 1
    assert result.coverage_errors[0].prompt.question == "trig 1"
    assert "malformed" in result.coverage_errors[0].reason

    # Trigger: 3 prompts scored (1 errored), all correct -> 3/3 = 100%
    assert result.trigger_scored == 3
    assert result.passed == 3
    assert result.pass_rate == pytest.approx(1.0)

    # Coverage: 2 trigger prompts, 1 errored, 1 supported -> 1/1 = 100%
    assert result.coverage_passed == 1
    assert result.coverage_rate == pytest.approx(1.0)


# -------- save/load round-trip ------------------------------------------------


def test_save_and_load_eval_set_round_trip(tmp_path):
    prompts = _build_eval_set(2, 2)
    path = save_eval_set(tmp_path, "demo", prompts)

    assert path == tmp_path / ".openkb" / "eval-sets" / "demo.json"
    assert path.is_file()

    data = json.loads(path.read_text())
    assert data["should_trigger"] == ["trig 0", "trig 1"]
    assert data["should_not"] == ["no 0", "no 1"]

    loaded = load_eval_set(path)
    assert len(loaded) == 4
    assert [p.question for p in loaded if p.expected == "trigger"] == ["trig 0", "trig 1"]
    assert [p.question for p in loaded if p.expected == "no-trigger"] == ["no 0", "no 1"]


# -------- RuntimeError translation for CLI catch -------------------------------


@pytest.mark.asyncio
async def test_generate_eval_set_translates_max_turns_to_runtime_error(tmp_path):
    """MaxTurnsExceeded from Runner.run should become RuntimeError."""
    from agents.exceptions import MaxTurnsExceeded

    skill_dir = _make_skill(tmp_path)

    async def fake_runner(*args, **kwargs):
        raise MaxTurnsExceeded("ran out")

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        with pytest.raises(RuntimeError, match="max-turn cap"):
            await generate_eval_set(skill_dir, model="gpt-4o-mini")


@pytest.mark.asyncio
async def test_generate_eval_set_translates_malformed_json_to_runtime_error(tmp_path):
    """Non-JSON LLM output should produce a friendly RuntimeError."""
    skill_dir = _make_skill(tmp_path)

    async def fake_runner(*args, **kwargs):
        return SimpleNamespace(final_output="this is not json at all")

    with patch("openkb.skill.evaluator.Runner.run", new=AsyncMock(side_effect=fake_runner)):
        with pytest.raises(RuntimeError, match="non-JSON output"):
            await generate_eval_set(skill_dir, model="gpt-4o-mini")
