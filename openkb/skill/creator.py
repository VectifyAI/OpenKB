"""The skill-create agent.

Builds on the same openai-agents-SDK + LiteLLM stack as the query agent
in ``openkb.agent.query``. The differences:

* System prompt comes from ``openkb/prompts/skill_create.md`` and is
  interpolated with the user's intent and the target skill name.
* Tools are scoped: the same deep-retrieval primitives the query agent
  uses (``list``, ``read``, ``get_page_content``, ``get_image``), plus
  one write tool restricted to the target skill directory.
* The runner verifies that ``SKILL.md`` was written before declaring
  success — an agent that gets confused and never writes the required
  output should fail loudly rather than silently produce an empty dir.
"""

from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner, ToolOutputImage, ToolOutputText, function_tool
from agents.model_settings import ModelSettings

from openkb.config import get_extra_headers, get_timeout_extra_args
from openkb.prompts import load_prompt
from openkb.schema import get_agents_md
from openkb.skill import skill_dir
from openkb.skill.tools import (
    get_skill_page_content as _get_page_content_impl,
)
from openkb.skill.tools import (
    list_wiki_dir as _list_wiki_dir_impl,
)
from openkb.skill.tools import (
    read_skill_image as _read_image_impl,
)
from openkb.skill.tools import (
    read_wiki_file_for_skill as _read_wiki_file_impl,
)
from openkb.skill.tools import (
    write_skill_file as _write_skill_file_impl,
)

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
    def get_page_content(doc_name: str, pages: str) -> str:
        """Get text content of specific pages from a PageIndex (long) document.

        Use this to read the *source* of a long document at page-range
        granularity. The summary page for the doc has a ``full_text``
        frontmatter pointer plus a tree of section page ranges — use those
        to target tight ranges, not the whole document.

        Args:
            doc_name: Document name without extension
                (e.g. ``"attention-is-all-you-need"``).
            pages: Page spec such as ``"3-5,7,10-12"``.
        """
        return _get_page_content_impl(doc_name, pages, wiki_root)

    @function_tool
    def get_image(image_path: str) -> ToolOutputImage | ToolOutputText:
        """View an image from the wiki.

        Use when a wiki page references a figure, chart, or diagram you
        need to see in order to distil it correctly into the skill.

        Args:
            image_path: Path relative to wiki/
                (e.g. ``"sources/images/doc/p1_img1.png"``).
        """
        result = _read_image_impl(image_path, wiki_root)
        if result["type"] == "image":
            return ToolOutputImage(image_url=result["image_url"])
        return ToolOutputText(text=result["text"])

    @function_tool
    async def query_wiki(question: str) -> str:
        """Semantic search over the wiki — narrow follow-ups only.

        This is a nested LLM call: each invocation spawns a separate
        query agent with its own turn budget. Use ONLY when you have a
        specific sub-question that direct file reads can't easily answer
        (e.g. "what does the book say about X across multiple
        chapters?"). For primary traversal, use list/read/get_page_content
        instead — they are cheaper and give you the raw text, not
        another LLM's summary.
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
        tools=[
            list_wiki_dir,
            read_wiki_file,
            get_page_content,
            get_image,
            query_wiki,
            write_skill_file,
            done,
        ],
        model=f"litellm/{model}",
        # Allow the model to issue multiple read tool calls in one turn —
        # the compile's early phase is a fan-out (list dir -> read N
        # summaries -> read N source page-ranges), and serialising each
        # read into its own turn costs roughly 5-10 extra round-trips per
        # compile. Writes serialise naturally because each
        # `write_skill_file` depends on accumulated reads; the model has
        # no reason to issue parallel writes to the same path.
        model_settings=ModelSettings(
            parallel_tool_calls=True,
            extra_headers=get_extra_headers() or None,
            extra_args=get_timeout_extra_args(),
        ),
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
    skill_root = skill_dir(kb_dir, skill_name)

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
