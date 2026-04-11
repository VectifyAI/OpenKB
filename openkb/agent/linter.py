"""Knowledge lint agent for semantic quality checks on the wiki."""
from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner, function_tool

from openkb.agent.tools import list_wiki_files, read_wiki_file

MAX_TURNS = 50
from openkb.schema import SCHEMA_MD, get_agents_md

_LINTER_INSTRUCTIONS_TEMPLATE = """\
You are OpenKB's semantic lint agent. Your job is to audit the wiki
for quality issues that structural tools cannot detect.

{schema_md}

## Checks to perform
1. **Contradictions** — Do any pages make conflicting claims about the same fact?
2. **Gaps** — Are there obvious missing topics or unexplained references?
3. **Staleness** — Are there references to "recent" work, dates, or versions that
   may be outdated?
4. **Redundancy** — Are there multiple pages that cover the same content and
   could be merged?
5. **Concept coverage** — Are important themes in the summaries missing concept pages?

## Process
1. Start with index.md to understand scope.
2. Read summary pages to understand document content.
3. Read concept pages to check for contradictions and gaps.
4. Produce a structured Markdown report listing issues found with references
   to the specific pages where each issue occurs.

Be thorough but concise. If the wiki is small or sparse, say so.
If no issues are found in a category, say "None found."
"""


def build_lint_agent(wiki_root: str, model: str, language: str = "en") -> Agent:
    """Build the semantic knowledge-lint agent.

    Args:
        wiki_root: Absolute path to the wiki directory.
        model: LLM model name.
        language: Language code for wiki content (e.g. 'en', 'fr').

    Returns:
        Configured :class:`~agents.Agent` instance.
    """
    schema_md = get_agents_md(Path(wiki_root))
    instructions = _LINTER_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
    instructions += f"\n\nIMPORTANT: Write the lint report in {language} language."

    @function_tool
    def list_files(directory: str) -> str:
        """List all Markdown files in a wiki subdirectory.

        Args:
            directory: Subdirectory path relative to wiki root (e.g. 'summaries').
        """
        return list_wiki_files(directory, wiki_root)

    @function_tool
    def read_file(path: str) -> str:
        """Read a Markdown file from the wiki.

        Args:
            path: File path relative to wiki root (e.g. 'summaries/paper.md').
        """
        return read_wiki_file(path, wiki_root)

    return Agent(
        name="wiki-linter",
        instructions=instructions,
        tools=[list_files, read_file],
        model=f"litellm/{model}",
    )


async def run_knowledge_lint(kb_dir: Path, model: str) -> str:
    """Run the semantic knowledge lint agent against the wiki.

    Args:
        kb_dir: Root of the knowledge base.
        model: LLM model name.

    Returns:
        The agent's lint report as a Markdown string.
    """
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_root = str(kb_dir / "wiki")
    agent = build_lint_agent(wiki_root, model, language=language)

    prompt = (
        "Please audit this knowledge base wiki for semantic quality issues: "
        "contradictions, gaps, staleness, redundancy, and missing concept pages. "
        "Start with index.md, then read summaries and concepts as needed. "
        "Produce a structured Markdown report."
    )

    result = await Runner.run(agent, prompt, max_turns=MAX_TURNS)
    return result.final_output or "Knowledge lint completed. No output produced."
