"""Wiki compilation agent for OpenKB.

Provides an agent that reads converted documents, generates summaries,
updates concept pages, and maintains the wiki index.
"""
from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner, function_tool
import os

from pageindex import PageIndexClient

from openkb.agent.tools import list_wiki_files, read_wiki_file, write_wiki_file
from openkb.schema import SCHEMA_MD, get_agents_md

_COMPILER_INSTRUCTIONS_TEMPLATE = """\
You are a wiki compilation agent for a personal knowledge base.

{schema_md}

## Your job
When given a new document, you must:
1. Write a summary page to summaries/<doc_name>.md with:
   - A YAML frontmatter block: `sources: [filename]`
   - Key concepts, findings, and ideas from the document
   - [[wikilinks]] to related concepts
2. Update or create concept pages in concepts/ for any significant cross-document themes.
3. Update index.md:
   - Under ## Documents: add a one-liner entry for the new document
   - Under ## Concepts: add/update entries for any concepts you touched

Always use the provided tools to read existing wiki pages before writing,
so you can append or update without losing prior content.
Use [[wikilinks]] consistently to connect related pages.
"""

_LONG_DOC_INSTRUCTIONS_TEMPLATE = """\
You are a wiki compilation agent for a personal knowledge base.

{schema_md}

## Your job for long documents (already summarised by PageIndex)
The summary and source pages are already written. Your tasks are:
1. Update or create concept pages in concepts/ for significant themes.
2. Update index.md:
   - Under ## Documents: add a one-liner entry referencing the document
   - Under ## Concepts: add/update entries for any concepts you touched
3. Do NOT regenerate or overwrite the existing summary page.

Use get_page_content to fetch specific page ranges from long documents when
you need more detail before writing concept pages.
Always read existing wiki pages before writing to preserve prior content.
Use [[wikilinks]] consistently to connect related pages.
"""


def build_compiler_agent(wiki_root: str, model: str, language: str = "en") -> Agent:
    """Build and return the wiki-compiler agent.

    Creates @function_tool wrappers that bind *wiki_root* so the agent
    doesn't need to supply it explicitly.

    Args:
        wiki_root: Absolute path to the wiki directory.
        model: LLM model name to use for the agent.
        language: Language code for wiki content (e.g. 'en', 'fr').

    Returns:
        Configured :class:`~agents.Agent` instance.
    """
    schema_md = get_agents_md(Path(wiki_root))
    instructions = _COMPILER_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
    instructions += f"\n\nIMPORTANT: Write all wiki content in {language} language."

    @function_tool
    def list_files(directory: str) -> str:
        """List all Markdown files in a wiki subdirectory.

        Args:
            directory: Subdirectory path relative to wiki root (e.g. 'sources').
        """
        return list_wiki_files(directory, wiki_root)

    @function_tool
    def read_file(path: str) -> str:
        """Read a Markdown file from the wiki.

        Args:
            path: File path relative to wiki root (e.g. 'sources/notes.md').
        """
        return read_wiki_file(path, wiki_root)

    @function_tool
    def write_file(path: str, content: str) -> str:
        """Write or overwrite a Markdown file in the wiki.

        Args:
            path: File path relative to wiki root (e.g. 'concepts/attention.md').
            content: Markdown content to write.
        """
        return write_wiki_file(path, content, wiki_root)

    return Agent(
        name="wiki-compiler",
        instructions=instructions,
        tools=[list_files, read_file, write_file],
        model=model,
    )


def build_long_doc_compiler_agent(wiki_root: str, kb_dir: str, model: str, language: str = "en") -> Agent:
    """Build the wiki-compiler agent with an extra get_page_content tool.

    Args:
        wiki_root: Absolute path to the wiki directory.
        kb_dir: Absolute path to the knowledge base root (contains .okb/).
        model: LLM model name to use for the agent.
        language: Language code for wiki content (e.g. 'en', 'fr').

    Returns:
        Configured :class:`~agents.Agent` instance.
    """
    from openkb.config import load_config

    okb_dir = Path(kb_dir) / ".okb"
    config = load_config(okb_dir / "config.yaml")
    _model = config.get("model", model)
    pi_key_env = config.get("pageindex_api_key_env", "") or "PAGEINDEX_API_KEY"
    pi_api_key = os.environ.get(pi_key_env, "")
    client = PageIndexClient(
        api_key=pi_api_key or None,
        model=_model,
        storage_path=str(okb_dir),
    )
    col = client.collection()

    schema_md = get_agents_md(Path(wiki_root))
    instructions = _LONG_DOC_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
    instructions += f"\n\nIMPORTANT: Write all wiki content in {language} language."

    @function_tool
    def list_files(directory: str) -> str:
        """List all Markdown files in a wiki subdirectory.

        Args:
            directory: Subdirectory path relative to wiki root (e.g. 'sources').
        """
        return list_wiki_files(directory, wiki_root)

    @function_tool
    def read_file(path: str) -> str:
        """Read a Markdown file from the wiki.

        Args:
            path: File path relative to wiki root (e.g. 'sources/notes.md').
        """
        return read_wiki_file(path, wiki_root)

    @function_tool
    def write_file(path: str, content: str) -> str:
        """Write or overwrite a Markdown file in the wiki.

        Args:
            path: File path relative to wiki root (e.g. 'concepts/attention.md').
            content: Markdown content to write.
        """
        return write_wiki_file(path, content, wiki_root)

    @function_tool
    def get_page_content(doc_id: str, pages: str) -> str:
        """Retrieve text content for specific pages of a long document.

        Args:
            doc_id: Document identifier from PageIndex.
            pages: Page range string, e.g. '1-5' or '3,7,12'.
        """
        results = col.get_page_content(doc_id, pages)
        if not results:
            return "No content found for the given pages."
        parts = []
        for item in results:
            page_num = item.get("page_index", "?")
            text = item.get("text", "")
            parts.append(f"[Page {page_num}]\n{text}")
        return "\n\n".join(parts)

    return Agent(
        name="wiki-compiler",
        instructions=instructions,
        tools=[list_files, read_file, write_file, get_page_content],
        model=model,
    )


async def compile_short_doc(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    model: str,
) -> None:
    """Run the compiler agent for a short (non-PageIndex) document.

    Reads the converted source Markdown, then asks the agent to generate a
    summary, update concept pages, and update the index.

    Args:
        doc_name: Document stem name (no extension).
        source_path: Path to the converted Markdown in wiki/sources/.
        kb_dir: Root of the knowledge base (contains wiki/ and .okb/).
        model: LLM model name.
    """
    from openkb.config import load_config

    okb_dir = kb_dir / ".okb"
    config = load_config(okb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_root = str(kb_dir / "wiki")
    agent = build_compiler_agent(wiki_root, model, language=language)

    content = source_path.read_text(encoding="utf-8")
    message = (
        f"New document: {doc_name}\n\n"
        f"Full text:\n{content}\n\n"
        "Generate summary, update concepts, update index."
    )

    await Runner.run(agent, message)


async def compile_long_doc(
    doc_name: str,
    summary_path: Path,
    doc_id: str,
    kb_dir: Path,
    model: str,
) -> None:
    """Run the compiler agent for a long (PageIndex) document.

    The summary page is already written. The agent updates concept pages and
    the index without regenerating the summary.

    Args:
        doc_name: Document stem name (no extension).
        summary_path: Path to the existing summary Markdown in wiki/summaries/.
        doc_id: PageIndex document identifier.
        kb_dir: Root of the knowledge base.
        model: LLM model name.
    """
    from openkb.config import load_config

    okb_dir = kb_dir / ".okb"
    config = load_config(okb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_root = str(kb_dir / "wiki")
    agent = build_long_doc_compiler_agent(wiki_root, str(kb_dir), model, language=language)

    content = summary_path.read_text(encoding="utf-8")
    message = (
        f"New long document: {doc_name} (doc_id: {doc_id})\n"
        f"Summary tree:\n{content}\n"
        "Update concepts and index. Do NOT regenerate summary."
    )

    await Runner.run(agent, message)
