"""The skill-create agent.

Builds on the same openai-agents-SDK + LiteLLM stack as the query agent
in ``openkb.agent.query``. The differences:

* System prompt comes from ``openkb/prompts/skill_create.md`` and is
  interpolated with the user's intent and the target skill name.
* Tools are scoped: read the wiki, query the wiki, write only within
  the target skill directory.
* The runner verifies that ``SKILL.md`` was written before declaring
  success — an agent that gets confused and never writes the required
  output should fail loudly rather than silently produce an empty dir.
"""
from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner, function_tool
from agents.model_settings import ModelSettings

from openkb.agent.skill_tools import (
    list_wiki_dir as _list_wiki_dir_impl,
    read_wiki_file_for_skill as _read_wiki_file_impl,
    write_skill_file as _write_skill_file_impl,
)
from openkb.prompts import load_prompt
from openkb.schema import get_agents_md

MAX_TURNS = 80  # higher than query (50) because compile can write multiple files


def build_skill_create_agent(
    *,
    wiki_root: str,
    skill_root: str,
    skill_name: str,
    intent: str,
    model: str,
) -> Agent:
    """Build the openai-agents Agent for compiling one skill.

    Args:
        wiki_root: ``<kb>/wiki`` absolute path.
        skill_root: ``<kb>/output/skills/<name>`` absolute path. Will be
            created if it does not exist.
        skill_name: kebab-case slug, also the ``name:`` frontmatter value.
        intent: the user's natural-language description of what this skill
            should do.
        model: LiteLLM-formatted model name from KB config.
    """
    Path(skill_root).mkdir(parents=True, exist_ok=True)

    wiki_schema = get_agents_md(Path(wiki_root))
    instructions = load_prompt("skill_create").format(
        intent=intent,
        skill_name=skill_name,
        wiki_schema=wiki_schema,
    )

    @function_tool
    def list_wiki_dir(directory: str) -> str:
        """List .md files in a wiki subdirectory (e.g. 'concepts')."""
        return _list_wiki_dir_impl(directory, wiki_root)

    @function_tool
    def read_wiki_file(path: str) -> str:
        """Read a wiki markdown file by path relative to wiki/ (e.g. 'concepts/attention.md')."""
        return _read_wiki_file_impl(path, wiki_root)

    @function_tool
    async def query_wiki(question: str) -> str:
        """Run a semantic query over the wiki and return the answer.

        Use sparingly — this is itself an LLM call. Prefer reading specific
        files when you already know which one you want.
        """
        # Lazy import to avoid a circular dependency at module load time.
        from openkb.agent.query import run_query
        kb_dir = Path(wiki_root).parent
        return await run_query(question, kb_dir, model, stream=False)

    @function_tool
    def write_skill_file(path: str, content: str) -> str:
        """Write a file under the skill directory."""
        return _write_skill_file_impl(path, content, skill_root)

    @function_tool
    def done(summary: str) -> str:
        """Signal that the skill is complete. Call exactly once when finished."""
        return f"Compilation marked done: {summary}"

    return Agent(
        name="skill-creator",
        instructions=instructions,
        tools=[list_wiki_dir, read_wiki_file, query_wiki, write_skill_file, done],
        model=f"litellm/{model}",
        model_settings=ModelSettings(parallel_tool_calls=False),
    )


async def run_skill_create(
    *,
    kb_dir: Path,
    skill_name: str,
    intent: str,
    model: str,
) -> Path:
    """Compile a single skill from the KB's wiki.

    Returns the path to the produced skill directory. Raises
    ``RuntimeError`` if the agent finishes without writing ``SKILL.md``,
    or if the SDK hits its turn cap before the agent declares done.
    """
    wiki_root = str(kb_dir / "wiki")
    skill_root = kb_dir / "output" / "skills" / skill_name

    agent = build_skill_create_agent(
        wiki_root=wiki_root,
        skill_root=str(skill_root),
        skill_name=skill_name,
        intent=intent,
        model=model,
    )

    # Single user message kicks off the compile. The system prompt already
    # contains the intent — this just nudges the agent to start.
    seed = (
        f"Compile the skill '{skill_name}'. Follow the system prompt's "
        f"working method. Read the wiki, then write the skill files."
    )

    # Lazy import: keeps the top of this module independent of the SDK's
    # internal exception layout in case its export path moves.
    from agents.exceptions import MaxTurnsExceeded

    try:
        await Runner.run(agent, seed, max_turns=MAX_TURNS)
    except MaxTurnsExceeded as exc:
        raise RuntimeError(
            f"Skill compilation hit the {MAX_TURNS}-step cap before finishing. "
            f"The wiki may be too large for a single skill, or the intent may "
            f"be too broad. Try splitting into multiple skills with narrower "
            f"intents, or pass a smaller wiki subset."
        ) from exc

    if not (skill_root / "SKILL.md").exists():
        raise RuntimeError(
            f"Skill compilation finished but agent did not write SKILL.md "
            f"at {skill_root}. The skill is incomplete; check the wiki has "
            f"content related to your intent."
        )

    return skill_root
