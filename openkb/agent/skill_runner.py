"""Generic skill runner — the shared core between CLI and chat surfaces.

A skill (Anthropic-style ``SKILL.md`` with YAML frontmatter and a body of
agent instructions) is loaded by ``run_skill``, which builds an Agent
whose ``instructions`` are that body. The agent gets the standard wiki
read-tool set plus a constrained ``write_file`` tool scoped to
``wiki/explorations/**`` and ``output/**``, and ``read_output_or_skill_file``
for inspecting prior artifacts.

This decouples generators from hard-coded prompts. ``openkb deck new`` /
``openkb skill new`` / any future ``openkb <type> new`` command becomes a
two-line wrapper around ``run_skill(skill_name=..., intent=...)``.

Skill frontmatter that ``run_skill`` honours (all optional; under the
top-level ``od:`` key):

* ``mode`` — the artifact type. ``"deck"`` triggers deck-specific
  post-run handling (output-path templating + validation). Unknown modes
  are accepted; they just don't trigger extra hooks.
* ``output_path_template`` — a path string with ``{slug}`` placeholder,
  relative to KB root. When set, ``run_skill`` injects the resolved path
  into the agent's intent and verifies the file exists post-run.
* ``deck_grammar`` — passed to :func:`openkb.deck.validator.validate_deck`
  when ``mode == "deck"``. See that module for the contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agents import Runner, function_tool

from openkb.agent.query import build_query_agent
from openkb.agent.skills import _parse_frontmatter, scan_local_skills
from openkb.agent.tools import read_kb_file, write_kb_file


MAX_TURNS = 80
MAX_TURNS_WITH_CRITIQUE = 120


class SkillNotFoundError(RuntimeError):
    """Raised when the requested skill can't be located in any skill root."""


@dataclass
class SkillRunResult:
    """Outcome of a :func:`run_skill` call.

    ``validation`` is populated only when the skill declared a
    deck-mode artifact and the post-run validator was therefore invoked;
    otherwise ``None``. ``output_path`` is the KB-relative path the
    runner enforced via ``output_path_template``, or ``None`` if the
    skill left output placement to itself.
    """

    skill_name: str
    output_path: Optional[Path] = None
    validation: Optional[Any] = None  # openkb.deck.validator.ValidationResult
    metadata: dict = field(default_factory=dict)  # skill's ``od:`` block


async def run_skill(
    *,
    skill_name: str,
    intent: str,
    kb_dir: Path,
    model: str,
    language: str = "en",
    max_turns: int = MAX_TURNS,
    seed: Optional[str] = None,
    slug: Optional[str] = None,
    extra_skill_roots: tuple[str | Path, ...] = (),
) -> SkillRunResult:
    """Load ``skill_name`` and run it as an agent with ``intent``.

    When the skill's frontmatter declares ``od.mode == "deck"`` and
    ``od.output_path_template`` is set, ``run_skill``:

      1. Templates the path with the provided ``slug``.
      2. Adds a "Write to: <path>" line to the agent's intent so the
         skill knows the expected destination.
      3. After the agent run, checks the file exists and (if the skill
         declared ``od.deck_grammar``) runs ``validate_deck`` against it.

    Args:
        skill_name: Name (frontmatter ``name:``) of the skill to invoke.
        intent: Natural-language brief for what to produce.
        kb_dir: KB root. Used both for skill discovery and for the
            agent's wiki read-tools / write-file scoping.
        model: LiteLLM-formatted model string from KB config.
        language: Passed through to the underlying query agent for
            answer-language consistency.
        max_turns: Hard cap on agent loop iterations.
        seed: Optional kick-off user message. Defaults to a short nudge
            that points the agent at its own instructions.
        slug: Required when the skill declares
            ``od.output_path_template`` (it substitutes ``{slug}``).
            Otherwise ignored.
        extra_skill_roots: Additional directories to scan beyond the
            built-in ``<kb>/skills``, ``~/.openkb/skills``,
            ``~/.claude/skills``.

    Returns:
        A :class:`SkillRunResult` carrying the resolved output path (if
        any) and the validation result (for deck-mode skills).

    Raises:
        SkillNotFoundError: if no skill with ``skill_name`` is found.
        RuntimeError: on turn-cap, model error, or missing
            output file after a templated-path run.
    """
    skills = scan_local_skills(kb_dir, extra_roots=extra_skill_roots)
    match = next((s for s in skills if s["name"] == skill_name), None)
    if match is None:
        available = ", ".join(sorted(s["name"] for s in skills)) or "(none)"
        raise SkillNotFoundError(
            f"Skill {skill_name!r} not found. Available: {available}. "
            f"Drop a SKILL.md into ~/.openkb/skills/<name>/ or "
            f"<kb>/skills/<name>/ and re-run."
        )

    skill_md = Path(match["path"]) / "SKILL.md"
    meta, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    od_meta: dict = (meta.get("od") or {}) if isinstance(meta, dict) else {}

    # Resolve output path if the skill templated one.
    output_path: Optional[Path] = None
    template = od_meta.get("output_path_template")
    if template and slug:
        rel = template.format(slug=slug)
        output_path = (kb_dir / rel).resolve()
        intent = (
            f"Output file (write the artifact here, full file in one "
            f"write_file call): {rel}\n\n{intent}"
        )

    wiki_root = str(kb_dir / "wiki")
    kb_root = str(kb_dir)
    base = build_query_agent(wiki_root, model, language=language)

    @function_tool
    def write_file(path: str, content: str) -> str:
        """Write a text file under the KB.

        Allowed paths (relative to KB root):
          * ``wiki/explorations/**`` — chat-derived notes.
          * ``output/**``            — generator artifacts (skills, decks, etc.).

        Any other path is rejected. Parent directories are created.
        """
        return write_kb_file(path, content, kb_root)

    @function_tool
    def read_output_or_skill_file(path: str) -> str:
        """Read any text file under the KB's ``output/`` or ``skills/``.

        Use this when the skill needs to inspect a previously-generated
        artifact (e.g. critique an existing deck) or another skill's
        body. For wiki content, prefer the dedicated wiki read tools.

        Args:
            path: File path relative to the KB root, e.g.
                ``"output/decks/foo/index.html"``.
        """
        return read_kb_file(path, kb_root)

    agent = base.clone(
        name=f"skill::{skill_name}",
        instructions=(base.instructions or "")
        + "\n\n# Skill instructions (you are this skill)\n\n"
        + body
        + "\n\n## User intent\n\n"
        + intent,
        tools=[*base.tools, write_file, read_output_or_skill_file],
    )

    user_seed = seed or (
        f"Follow the skill instructions above. Begin work now. "
        f"User intent: {intent}"
    )

    from agents.exceptions import MaxTurnsExceeded

    try:
        await Runner.run(agent, user_seed, max_turns=max_turns)
    except MaxTurnsExceeded as exc:
        raise RuntimeError(
            f"Skill {skill_name!r} hit the {max_turns}-step cap before "
            f"finishing. The intent may be too broad or the wiki too large; "
            f"try a tighter intent or split into smaller skills."
        ) from exc

    result = SkillRunResult(
        skill_name=skill_name,
        output_path=output_path,
        metadata=od_meta if isinstance(od_meta, dict) else {},
    )

    # Post-run hooks driven by skill frontmatter.
    if output_path is not None and not output_path.is_file():
        raise RuntimeError(
            f"Skill {skill_name!r} finished but did not write the expected "
            f"output file at {output_path}. The skill is either misconfigured "
            f"or the wiki lacks content matching the intent."
        )

    if od_meta.get("mode") == "deck" and output_path is not None:
        # Lazy import — skill_runner shouldn't pull in deck-specific code
        # at import time, only when actually running a deck skill.
        from openkb.deck.validator import DeckGrammar, validate_deck

        grammar: Optional[DeckGrammar] = od_meta.get("deck_grammar")
        result.validation = validate_deck(output_path.parent, grammar=grammar)

    return result
