"""Quality evaluation for compiled skills.

Two metrics, two LLM passes:

**Trigger accuracy** — given *only* the description, does an external
agent decide correctly whether to load the skill for a given question?
This catches under-specific descriptions (false negatives) and
over-broad descriptions (false positives).

**Body alignment** — given the *full* SKILL.md (body + references), can
the skill actually answer the should-trigger questions it claims to
handle? This catches the failure mode where a well-written description
promises capability that the body doesn't deliver — a hollow skill that
would trigger but fail in practice.

Flow:
  1. Read the SKILL.md frontmatter (description) + body + references/*.
  2. Generator LLM produces N should-trigger + N should-not prompts,
     using description AND body so prompts reflect what the skill
     actually claims to cover, not just description vibes.
  3. Trigger grader (description-only, runs on every prompt) and
     alignment grader (body + references, runs on should-trigger
     prompts only) are dispatched concurrently via ``asyncio.gather``,
     bounded by ``EVAL_CONCURRENCY``. Each grader is independent so
     the two passes overlap completely.
  4. Failed gradings (e.g. ``MaxTurnsExceeded``) are captured per task
     via ``return_exceptions=True`` so a single failure doesn't discard
     the other ~29 successful gradings; errored prompts are surfaced
     separately and excluded from the rate denominators.
  5. Report both pass rates and the specific misses.

Uses the same LiteLLM model the rest of the KB uses (config.yaml). No
real LLM calls in tests — both generator and graders are patched.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from agents import Agent, Runner
from agents.exceptions import MaxTurnsExceeded
from agents.model_settings import ModelSettings

from openkb.config import get_extra_headers, get_timeout_extra_args
from openkb.skill import extract_body, extract_frontmatter

EVAL_DEFAULT_COUNT = 10  # 10 trigger + 10 no-trigger = 20 prompts
REFERENCES_PREVIEW_BYTES = 4000  # cap reference content fed to the eval LLM
# Bound on concurrent grader LLM calls in run_eval. Without this the
# default count=10 would fire ~30 simultaneous requests, which most
# providers rate-limit. 8 is a conservative starting point — the
# semaphore acts as a sliding window across the combined trigger + coverage
# pool, so realistic speedup ranges from 30/8 (~3.75x) to 8x depending
# on per-call latency variance. Bumping this up trades rate-limit risk
# for wall-clock latency.
EVAL_CONCURRENCY = 8


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
class CoverageMiss:
    """A should-trigger prompt the description promises but the body can't support."""

    prompt: EvalPrompt
    reason: str = ""


@dataclass
class EvalResult:
    prompts: list[EvalPrompt] = field(default_factory=list)
    misses: list[EvalMiss] = field(default_factory=list)
    coverage_misses: list[CoverageMiss] = field(default_factory=list)
    # Trigger prompts where the coverage grader returned an unparseable
    # verdict (neither SUPPORTED nor UNSUPPORTED). Tracked separately so
    # grader-malfunction doesn't silently inflate ``coverage_misses`` and
    # deflate ``coverage_rate``.
    coverage_ambiguous: list[CoverageMiss] = field(default_factory=list)
    # Prompts whose grader raised (typically ``RuntimeError`` from a
    # ``MaxTurnsExceeded`` wrap or a malformed-response failure inside
    # the SDK). Captured per-task via ``return_exceptions=True`` in
    # ``run_eval`` so one failure doesn't discard the other ~29
    # gradings. Errors are excluded from rate denominators — we don't
    # know what the verdict would have been.
    trigger_errors: list[CoverageMiss] = field(default_factory=list)
    coverage_errors: list[CoverageMiss] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.prompts)

    @property
    def passed(self) -> int:
        return self.trigger_scored - len(self.misses)

    @property
    def trigger_scored(self) -> int:
        """Trigger prompts the grader returned a verdict on (no error)."""
        return self.total - len(self.trigger_errors)

    @property
    def pass_rate(self) -> float:
        scored = self.trigger_scored
        return self.passed / scored if scored else 0.0

    @property
    def trigger_questions(self) -> int:
        return sum(1 for p in self.prompts if p.expected == "trigger")

    @property
    def coverage_passed(self) -> int:
        # Ambiguous and errored outputs are excluded from both numerator
        # and denominator — see ``coverage_rate``.
        scored = self.trigger_questions - len(self.coverage_ambiguous) - len(self.coverage_errors)
        return scored - len(self.coverage_misses)

    @property
    def coverage_rate(self) -> float:
        # Score only the trigger prompts the grader gave a clear verdict
        # on. A garbled run that flips half the outputs to ambiguous or
        # errors out should narrow the denominator, not pretend half the
        # body is hollow.
        scored = self.trigger_questions - len(self.coverage_ambiguous) - len(self.coverage_errors)
        return self.coverage_passed / scored if scored else 0.0


def _read_description(skill_dir: Path) -> str:
    """Extract the description: field from SKILL.md frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    fm = extract_frontmatter(text)
    if fm is None:
        raise RuntimeError(f"{skill_md} has no YAML frontmatter.")
    meta = yaml.safe_load(fm) or {}
    desc = meta.get("description")
    if not isinstance(desc, str) or not desc:
        raise RuntimeError(f"{skill_md} has no description: field.")
    return desc


def _read_body(skill_dir: Path) -> str:
    """Return SKILL.md without the YAML frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    return extract_body(skill_md.read_text(encoding="utf-8")).lstrip()


def _read_references_preview(skill_dir: Path) -> str:
    """Concatenate references/*.md, capped per-file, for the eval LLM.

    The cap keeps token use bounded for large reference sets. Each file
    contributes its first N bytes (text is markdown so a byte cap is a
    reasonable proxy for tokens).
    """
    refs_dir = skill_dir / "references"
    if not refs_dir.is_dir():
        return ""
    chunks: list[str] = []
    for ref in sorted(refs_dir.rglob("*.md")):
        rel = ref.relative_to(skill_dir)
        text = ref.read_text(encoding="utf-8", errors="replace")
        if len(text) > REFERENCES_PREVIEW_BYTES:
            text = text[:REFERENCES_PREVIEW_BYTES] + "\n…[truncated]\n"
        chunks.append(f"--- {rel} ---\n{text}")
    return "\n\n".join(chunks)


def _skill_content_block(skill_dir: Path) -> str:
    """Bundle body + reference previews into one prompt-ready string."""
    body = _read_body(skill_dir)
    refs = _read_references_preview(skill_dir)
    if not refs:
        return f"SKILL.md body:\n{body}"
    return f"SKILL.md body:\n{body}\n\nReferences (excerpts):\n{refs}"


async def generate_eval_set(
    skill_dir: Path,
    *,
    model: str,
    count: int = EVAL_DEFAULT_COUNT,
) -> list[EvalPrompt]:
    """Use an LLM to generate ``count`` should-trigger + ``count`` should-not
    eval prompts grounded in what the skill actually claims to cover.

    The generator now sees the SKILL.md body and reference previews, not
    just the description. That keeps the should-trigger questions
    realistic (questions the skill is genuinely set up to answer) and the
    should-not questions plausibly-adjacent (so trigger accuracy actually
    catches over-broad descriptions, not just disjoint ones).
    """
    desc = _read_description(skill_dir)
    content = _skill_content_block(skill_dir)

    instructions = (
        "You are designing an evaluation set for a knowledge-base skill. "
        "The skill's activation description is:\n\n"
        f"  {desc}\n\n"
        "The skill's body (SKILL.md without frontmatter) and references "
        "are below. Use them to ground the questions in what the skill "
        "actually claims to cover.\n\n"
        f"{content}\n\n"
        f"Produce exactly {count} 'should-trigger' user questions "
        "(questions a user might ask where this skill genuinely helps — "
        "ones the body and references contain material for) and exactly "
        f"{count} 'should-not' user questions (plausibly-adjacent "
        "questions where this skill is NOT the right tool — they should "
        "be close enough that a sloppy description would mis-trigger, "
        "but the body clearly does not cover them).\n\n"
        "Output ONLY a JSON object with this exact shape:\n"
        f'  {{"should_trigger": [...{count} strings...], '
        f'"should_not": [...{count} strings...]}}\n\n'
        "No prose. No markdown. Just the JSON object."
    )

    agent = Agent(
        name="eval-set-generator",
        instructions=instructions,
        model=f"litellm/{model}",
        model_settings=ModelSettings(
            extra_headers=get_extra_headers() or None,
            extra_args=get_timeout_extra_args(),
        ),
    )
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
    """Ask the trigger grader LLM whether the description suggests this
    skill should be loaded for the given question.

    The grader deliberately sees ONLY the description — that is the
    trigger surface external agents will see when deciding to load the
    skill. The body is irrelevant to *this* metric; see
    :func:`grade_coverage` for the body-aware check.
    """
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
        model_settings=ModelSettings(
            extra_headers=get_extra_headers() or None,
            extra_args=get_timeout_extra_args(),
        ),
    )
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


async def grade_coverage(
    skill_content: str,
    question: str,
    *,
    model: str,
) -> tuple[Literal["supported", "unsupported", "ambiguous"], str]:
    """Ask the alignment grader whether the SKILL.md body + references
    actually contain enough substance to answer the question.

    This is the orthogonal check to :func:`grade_one`. A skill can have a
    perfectly-firing description and still be a hollow shell — this catches
    that. Returns ``"supported"``, ``"unsupported"``, or ``"ambiguous"``
    (parser couldn't extract a verdict from the grader's output) plus a
    one-line reason. Callers should NOT collapse ``"ambiguous"`` into
    ``"unsupported"`` — see :class:`EvalResult.coverage_ambiguous`.
    """
    instructions = (
        "You are auditing a skill for content quality. You will be given "
        "the skill's body (SKILL.md without frontmatter) and any "
        "reference excerpts, plus a user question that the skill's "
        "description claims to handle. Decide whether the body has "
        "substantive material to answer the question.\n\n"
        "Answer with EXACTLY this two-line shape:\n"
        "VERDICT: SUPPORTED  (or UNSUPPORTED)\n"
        "REASON: <one short sentence>\n\n"
        f"{skill_content}"
    )
    agent = Agent(
        name="coverage-grader",
        instructions=instructions,
        model=f"litellm/{model}",
        model_settings=ModelSettings(
            extra_headers=get_extra_headers() or None,
            extra_args=get_timeout_extra_args(),
        ),
    )
    try:
        result = await Runner.run(agent, f"Question: {question}", max_turns=2)
    except MaxTurnsExceeded as exc:
        raise RuntimeError(
            f"Coverage grader hit the max-turn cap on question: {question!r}. "
            f"Try a more capable model."
        ) from exc
    raw = (result.final_output or "").strip()
    upper = raw.upper()
    verdict: Literal["supported", "unsupported", "ambiguous"]
    if "UNSUPPORTED" in upper:
        verdict = "unsupported"
    elif "SUPPORTED" in upper:
        verdict = "supported"
    else:
        # Grader didn't emit a parseable verdict — surface as a distinct
        # state so callers can report grader-malfunction separately from
        # "the body is hollow." See ``EvalResult.coverage_ambiguous``.
        verdict = "ambiguous"
    reason = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("REASON:"):
            reason = stripped.split(":", 1)[1].strip()
            break
    if not reason and verdict == "ambiguous":
        # Keep the first ~120 chars of the raw output so the user has
        # something to debug from.
        reason = f"unparseable grader output: {raw[:120]!r}"
    return verdict, reason


async def run_eval(
    skill_dir: Path,
    *,
    model: str,
    eval_set: list[EvalPrompt] | None = None,
    count: int = EVAL_DEFAULT_COUNT,
) -> EvalResult:
    """Run trigger-accuracy + body-alignment evaluation.

    Args:
        skill_dir: ``<kb>/output/skills/<name>``
        model: LiteLLM model string from KB config
        eval_set: pre-generated prompts; if None, generate fresh
        count: how many should-trigger + should-not prompts to generate
    """
    if eval_set is None:
        eval_set = await generate_eval_set(skill_dir, model=model, count=count)

    desc = _read_description(skill_dir)
    content = _skill_content_block(skill_dir)
    result = EvalResult(prompts=eval_set)

    # Run grading concurrently. Each prompt is independent — graders read
    # the same `desc`/`content` strings and produce results that are then
    # appended to `result` in eval_set order below, so concurrent
    # execution is correctness-preserving. A semaphore caps simultaneous
    # LLM calls to avoid hitting provider rate limits.
    sem = asyncio.Semaphore(EVAL_CONCURRENCY)

    async def _trigger(p: EvalPrompt) -> Literal["trigger", "no-trigger"]:
        async with sem:
            return await grade_one(desc, p.question, model=model)

    async def _coverage(
        p: EvalPrompt,
    ) -> tuple[Literal["supported", "unsupported", "ambiguous"], str]:
        async with sem:
            return await grade_coverage(content, p.question, model=model)

    trigger_tasks = [_trigger(p) for p in eval_set]
    # Body alignment only meaningful on questions the skill claims to
    # handle — for should-not questions the body is correctly empty of
    # relevant material.
    coverage_prompts = [p for p in eval_set if p.expected == "trigger"]
    coverage_tasks = [_coverage(p) for p in coverage_prompts]

    # return_exceptions=True so one failed grader doesn't discard the
    # other ~29 successful gradings. Errored prompts are surfaced
    # separately on the result and excluded from rate denominators.
    trigger_results, coverage_results = await asyncio.gather(
        asyncio.gather(*trigger_tasks, return_exceptions=True),
        asyncio.gather(*coverage_tasks, return_exceptions=True),
    )

    # Walk inputs in original order so `result.*` lists are deterministic
    # even though the gather() above completed out of order.
    for prompt, graded in zip(eval_set, trigger_results):
        if isinstance(graded, BaseException):
            result.trigger_errors.append(CoverageMiss(prompt=prompt, reason=str(graded)))
            continue
        if graded != prompt.expected:
            result.misses.append(EvalMiss(prompt=prompt, graded=graded))

    for prompt, outcome in zip(coverage_prompts, coverage_results):
        if isinstance(outcome, BaseException):
            result.coverage_errors.append(CoverageMiss(prompt=prompt, reason=str(outcome)))
            continue
        verdict, reason = outcome
        if verdict == "ambiguous":
            result.coverage_ambiguous.append(CoverageMiss(prompt=prompt, reason=reason))
        elif verdict == "unsupported":
            result.coverage_misses.append(CoverageMiss(prompt=prompt, reason=reason))

    return result


def save_eval_set(
    kb_dir: Path,
    skill_name: str,
    prompts: list[EvalPrompt],
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
