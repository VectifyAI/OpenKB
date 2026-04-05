"""Q&A agent for querying the OpenKB knowledge base."""
from __future__ import annotations

from pathlib import Path

import litellm
from agents import Agent, Runner, function_tool
import os

from pageindex import PageIndexClient

from openkb.agent.tools import list_wiki_files, read_wiki_file
from openkb.schema import SCHEMA_MD, get_agents_md

_QUERY_INSTRUCTIONS_TEMPLATE = """\
You are a knowledge-base Q&A agent. You answer questions by searching the wiki.

{schema_md}

## Search strategy
1. Start by reading index.md to understand what documents and concepts are available.
2. Read relevant summary pages (summaries/) to get document overviews.
3. Read concept pages (concepts/) for cross-document synthesis.
4. For long documents indexed with PageIndex, call pageindex_retrieve with the
   document ID and the user's question to get detailed page-level content.
5. Synthesise a clear, well-cited answer.

Always ground your answer in the wiki content. If you cannot find relevant
information, say so clearly.
"""


def _pageindex_retrieve_impl(doc_id: str, question: str, okb_dir: str, model: str) -> str:
    """Retrieve relevant content from a long document via PageIndex.

    Args:
        doc_id: PageIndex document identifier.
        question: The user's question.
        okb_dir: Path to the .okb/ state directory.
        model: LLM model to use for relevance selection.

    Returns:
        Formatted string with retrieved page content.
    """
    from openkb.config import load_config
    config = load_config(Path(okb_dir) / "config.yaml")
    pi_api_key = os.environ.get(config.get("pageindex_api_key_env", ""), "")
    client = PageIndexClient(
        api_key=pi_api_key or None,
        model=model,
        storage_path=okb_dir,
    )
    col = client.collection()

    # 1. Get document structure
    try:
        structure = col.get_document_structure(doc_id)
    except Exception as exc:
        return f"Error retrieving document structure: {exc}"

    if not structure:
        return "No structure found for document."

    # Build a text summary of sections for the LLM
    sections = []
    for idx, node in enumerate(structure):
        title = node.get("title", f"Section {idx + 1}")
        node_id = node.get("node_id", str(idx))
        summary = node.get("summary", "")
        start = node.get("start_index", idx)
        end = node.get("end_index", idx)
        sections.append(
            f"node_id={node_id} title='{title}' pages={start}-{end} summary='{summary}'"
        )

    sections_text = "\n".join(sections)
    prompt = (
        f"Given the following document sections:\n{sections_text}\n\n"
        f"Which page ranges are most relevant to this question: '{question}'?\n"
        "Reply with a comma-separated list of page numbers or ranges (e.g. '1-3,7,10-12'). "
        "Return ONLY the page specification, nothing else."
    )

    # 2. Ask LLM which pages are relevant
    try:
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=100,
        )
        page_spec = response.choices[0].message.content.strip()
    except Exception as exc:
        return f"Error selecting relevant pages: {exc}"

    if not page_spec:
        return "Could not determine relevant pages."

    # 3. Fetch those pages
    try:
        pages = col.get_page_content(doc_id, page_spec)
    except Exception as exc:
        return f"Error fetching page content: {exc}"

    if not pages:
        return f"No content found for pages: {page_spec}"

    parts = []
    for item in pages:
        page_num = item.get("page_index", "?")
        text = item.get("text", "")
        parts.append(f"[Page {page_num}]\n{text}")

    return "\n\n".join(parts)


def build_query_agent(wiki_root: str, okb_dir: str, model: str, language: str = "en") -> Agent:
    """Build and return the Q&A agent.

    Args:
        wiki_root: Absolute path to the wiki directory.
        okb_dir: Path to the .okb/ state directory.
        model: LLM model name.
        language: Language code for wiki content (e.g. 'en', 'fr').

    Returns:
        Configured :class:`~agents.Agent` instance.
    """
    schema_md = get_agents_md(Path(wiki_root))
    instructions = _QUERY_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
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
            path: File path relative to wiki root (e.g. 'summaries/paper.md').
        """
        return read_wiki_file(path, wiki_root)

    @function_tool
    def pageindex_retrieve(doc_id: str, question: str) -> str:
        """Retrieve relevant content from a long document via PageIndex.

        Use this when you need detailed content from a document that was
        indexed with PageIndex (long documents).

        Args:
            doc_id: PageIndex document identifier (found in index.md).
            question: The question you are trying to answer.
        """
        return _pageindex_retrieve_impl(doc_id, question, okb_dir, model)

    return Agent(
        name="wiki-query",
        instructions=instructions,
        tools=[list_files, read_file, pageindex_retrieve],
        model=model,
    )


async def run_query(question: str, kb_dir: Path, model: str) -> str:
    """Run a Q&A query against the knowledge base.

    Args:
        question: The user's question.
        kb_dir: Root of the knowledge base.
        model: LLM model name.

    Returns:
        The agent's final answer as a string.
    """
    from openkb.config import load_config

    okb_dir = kb_dir / ".okb"
    config = load_config(okb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_root = str(kb_dir / "wiki")
    okb_path = str(okb_dir)

    agent = build_query_agent(wiki_root, okb_path, model, language=language)
    result = await Runner.run(agent, question)
    return result.final_output or ""
