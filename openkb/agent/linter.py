"""Knowledge lint agent for semantic quality checks on the wiki."""
from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner, function_tool

from openkb.agent.tools import list_wiki_files, read_wiki_file
from openkb.schema import SCHEMA_MD

_LINTER_INSTRUCTIONS = f"""\
You are a knowledge-base semantic lint agent. Your job is to audit the wiki
for quality issues that structural tools cannot detect.

{SCHEMA_MD}

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


def build_lint_agent(wiki_root: str, model: str) -> Agent:
    """Build the semantic knowledge-lint agent.

    Args:
        wiki_root: Absolute path to the wiki directory.
        model: LLM model name.

    Returns:
        Configured :class:`~agents.Agent` instance.
    """

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
        instructions=_LINTER_INSTRUCTIONS,
        tools=[list_files, read_file],
        model=model,
    )


async def run_knowledge_lint(kb_dir: Path, model: str) -> str:
    """Run the semantic knowledge lint agent against the wiki.

    Args:
        kb_dir: Root of the knowledge base.
        model: LLM model name.

    Returns:
        The agent's lint report as a Markdown string.
    """
    wiki_root = str(kb_dir / "wiki")
    agent = build_lint_agent(wiki_root, model)

    prompt = (
        "Please audit this knowledge base wiki for semantic quality issues: "
        "contradictions, gaps, staleness, redundancy, and missing concept pages. "
        "Start with index.md, then read summaries and concepts as needed. "
        "Produce a structured Markdown report."
    )

    result = await Runner.run(agent, prompt)
    return result.final_output or "Knowledge lint completed. No output produced."
