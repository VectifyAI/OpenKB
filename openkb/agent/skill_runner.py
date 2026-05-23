"""Generic skill runner — the shared core between CLI and chat surfaces.

A skill (Anthropic-style ``SKILL.md`` with YAML frontmatter and a body of
agent instructions) is loaded by ``run_skill``, which builds an Agent
whose ``instructions`` are that body. The agent gets the standard wiki
read-tool set plus a constrained ``write_file`` tool scoped to
``wiki/explorations/**`` and ``output/**``.

This decouples generators from hard-coded prompts. ``openkb deck new`` /
``openkb skill new`` / any future ``openkb <type> new`` command becomes a
two-line wrapper around ``run_skill(skill_name=..., intent=...)``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from agents import Agent, Runner, function_tool

from openkb.agent.query import build_query_agent
from openkb.agent.skills import _parse_frontmatter, scan_local_skills
from openkb.agent.tools import read_kb_file, write_kb_file


MAX_TURNS = 80
MAX_TURNS_WITH_CRITIQUE = 120


class SkillNotFoundError(RuntimeError):
    """Raised when the requested skill can't be located in any skill root."""


async def run_skill(
    *,
    skill_name: str,
    intent: str,
    kb_dir: Path,
    model: str,
    language: str = "en",
    max_turns: int = MAX_TURNS,
    seed: Optional[str] = None,
    extra_skill_roots: tuple[str | Path, ...] = (),
) -> None:
    """Load skill ``skill_name`` and run it as an agent with ``intent``.

    Args:
        skill_name: Name (frontmatter ``name:``) of the skill to invoke.
        intent: Natural-language brief for what to produce. Appended to
            the skill body as a "## User intent" section.
        kb_dir: KB root. Used both for skill discovery and for the
            agent's wiki read-tools / write-file scoping.
        model: LiteLLM-formatted model string from KB config.
        language: Passed through to the underlying query agent for
            answer-language consistency.
        max_turns: Hard cap on agent loop iterations.
        seed: Optional kick-off user message. Defaults to a short nudge
            that points the agent at its own instructions.
        extra_skill_roots: Additional directories to scan beyond the
            built-in ``<kb>/skills``, ``~/.openkb/skills``,
            ``~/.claude/skills``.

    Raises:
        SkillNotFoundError: if no skill with ``skill_name`` is found.
        RuntimeError: if the agent run fails (turn-cap, model error).
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
    _meta, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))

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
