"""Trigger-accuracy evaluator for compiled skills.

The description: field in SKILL.md is the activation signal — it's what
other agents read to decide whether to load the skill for a given user
question. A vague or off-target description fails to fire when it should
(false negatives) or fires when it shouldn't (false positives). This
module catches both.

Flow:
  1. Read description from the skill's SKILL.md frontmatter
  2. Ask a generator LLM: produce N should-trigger + N should-not prompts
     based purely on the description (no other context)
  3. For each prompt, ask a grader LLM: "given just this description,
     should an agent load this skill for this question? yes/no"
  4. Compare against expected labels (the ground truth from step 2)
  5. Report pass rate + the specific misses

Uses the same LiteLLM model the rest of the KB uses (config.yaml). No
real LLM calls in tests — both generator and grader are patched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from agents import Agent, Runner
from agents.model_settings import ModelSettings


EVAL_DEFAULT_COUNT = 10  # 10 trigger + 10 no-trigger = 20 prompts


@dataclass
class EvalPrompt:
    question: str
    expected: Literal["trigger", "no-trigger"]


@dataclass
class EvalMiss:
    prompt: EvalPrompt
    graded: Literal["trigger", "no-trigger"]

    @property
    def label(self) -> str:
        return f"[{self.prompt.expected} -> graded {self.graded}]"


@dataclass
class EvalResult:
    prompts: list[EvalPrompt] = field(default_factory=list)
    misses: list[EvalMiss] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.prompts)

    @property
    def passed(self) -> int:
        return self.total - len(self.misses)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


def _read_description(skill_dir: Path) -> str:
    """Extract the description: field from SKILL.md frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RuntimeError(f"{skill_md} has no YAML frontmatter.")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise RuntimeError(f"{skill_md} has no YAML frontmatter.")
    meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    desc = meta.get("description")
    if not isinstance(desc, str) or not desc:
        raise RuntimeError(f"{skill_md} has no description: field.")
    return desc


async def generate_eval_set(
    skill_dir: Path,
    *,
    model: str,
    count: int = EVAL_DEFAULT_COUNT,
) -> list[EvalPrompt]:
    """Use an LLM to generate ``count`` should-trigger + ``count`` should-not
    eval prompts based on the skill's description.
    """
    desc = _read_description(skill_dir)

    instructions = (
        "You are designing an evaluation set for a knowledge-base skill. "
        f"The skill's activation description is:\n\n"
        f"  {desc}\n\n"
        f"Produce exactly {count} 'should-trigger' user questions (questions where "
        f"an agent SHOULD load this skill to answer well) and exactly {count} "
        f"'should-not' user questions (plausible-sounding questions about other "
        f"topics where this skill is NOT the right tool).\n\n"
        f"Output ONLY a JSON object with this exact shape:\n"
        f'  {{"should_trigger": [...{count} strings...], '
        f'"should_not": [...{count} strings...]}}\n\n'
        f"No prose. No markdown. Just the JSON object."
    )

    agent = Agent(
        name="eval-set-generator",
        instructions=instructions,
        model=f"litellm/{model}",
        model_settings=ModelSettings(parallel_tool_calls=False),
    )
    from agents.exceptions import MaxTurnsExceeded
    try:
        result = await Runner.run(agent, "Generate the eval set now.", max_turns=3)
    except MaxTurnsExceeded as exc:
        raise RuntimeError(
            "Eval set generation hit the max-turn cap. The model may be "
            "looping; try a different model or a smaller --count."
        ) from exc
    raw = (result.final_output or "").strip()

    # Strip optional code fence
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if raw.startswith("json"):
            raw = raw[4:].lstrip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Eval set generator returned non-JSON output: {exc.msg}. "
            f"Try a more capable model — small models often ignore "
            f"'output only JSON' instructions. First 200 chars: {raw[:200]!r}"
        ) from exc
    prompts: list[EvalPrompt] = []
    for q in data.get("should_trigger", []):
        prompts.append(EvalPrompt(question=q, expected="trigger"))
    for q in data.get("should_not", []):
        prompts.append(EvalPrompt(question=q, expected="no-trigger"))
    return prompts


async def grade_one(
    description: str,
    question: str,
    *,
    model: str,
) -> Literal["trigger", "no-trigger"]:
    """Ask the grader LLM whether the description suggests this skill
    should be loaded for the given question."""
    instructions = (
        "You are deciding whether an agent should load a specific skill to "
        "answer a user question. You will be given the skill's activation "
        "description and a single user question. Answer with one word: "
        "TRIGGER (load the skill) or NO-TRIGGER (don't load).\n\n"
        f"Skill description:\n  {description}\n\n"
        "Reply with exactly one of: TRIGGER, NO-TRIGGER."
    )
    agent = Agent(
        name="trigger-grader",
        instructions=instructions,
        model=f"litellm/{model}",
        model_settings=ModelSettings(parallel_tool_calls=False),
    )
    from agents.exceptions import MaxTurnsExceeded
    try:
        result = await Runner.run(agent, f"Question: {question}", max_turns=2)
    except MaxTurnsExceeded as exc:
        raise RuntimeError(
            f"Trigger grader hit the max-turn cap on question: {question!r}. "
            f"Try a more capable model."
        ) from exc
    raw = (result.final_output or "").strip().upper()
    if "NO-TRIGGER" in raw or "NO TRIGGER" in raw:
        return "no-trigger"
    if "TRIGGER" in raw:
        return "trigger"
    # Default: assume no-trigger on ambiguous output
    return "no-trigger"


async def run_eval(
    skill_dir: Path,
    *,
    model: str,
    eval_set: list[EvalPrompt] | None = None,
    count: int = EVAL_DEFAULT_COUNT,
) -> EvalResult:
    """Run a trigger-accuracy evaluation.

    Args:
        skill_dir: ``<kb>/output/skills/<name>``
        model: LiteLLM model string from KB config
        eval_set: pre-generated prompts; if None, generate fresh
        count: how many should-trigger + should-not prompts to generate
    """
    if eval_set is None:
        eval_set = await generate_eval_set(skill_dir, model=model, count=count)

    desc = _read_description(skill_dir)
    result = EvalResult(prompts=eval_set)
    for prompt in eval_set:
        graded = await grade_one(desc, prompt.question, model=model)
        if graded != prompt.expected:
            result.misses.append(EvalMiss(prompt=prompt, graded=graded))
    return result


def save_eval_set(
    kb_dir: Path, skill_name: str, prompts: list[EvalPrompt],
) -> Path:
    """Persist an eval set to ``<kb>/.openkb/eval-sets/<skill_name>.json``."""
    out_dir = kb_dir / ".openkb" / "eval-sets"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{skill_name}.json"
    data = {
        "should_trigger": [p.question for p in prompts if p.expected == "trigger"],
        "should_not": [p.question for p in prompts if p.expected == "no-trigger"],
    }
    out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return out_path


def load_eval_set(path: Path) -> list[EvalPrompt]:
    """Load an eval set previously saved via ``save_eval_set``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[EvalPrompt] = []
    for q in data.get("should_trigger", []):
        out.append(EvalPrompt(question=q, expected="trigger"))
    for q in data.get("should_not", []):
        out.append(EvalPrompt(question=q, expected="no-trigger"))
    return out
