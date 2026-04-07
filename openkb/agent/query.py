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


def _pageindex_retrieve_impl(doc_id: str, question: str, openkb_dir: str, model: str) -> str:
    """Retrieve relevant content from a long document via PageIndex.

    For cloud-indexed docs: delegates to col.query() directly.
    For local docs: uses structure-based page selection + get_page_content.
    """
    from openkb.config import load_config
    config = load_config(Path(openkb_dir) / "config.yaml")
    pi_key_env = config.get("pageindex_api_key_env", "") or "PAGEINDEX_API_KEY"
    pi_api_key = os.environ.get(pi_key_env, "")
    # Determine if this doc was cloud-indexed (cloud doc_ids have "pi-" prefix)
    is_cloud_doc = doc_id.startswith("pi-")

    if is_cloud_doc:
        # Cloud doc: use PageIndex streaming query (avoids timeout, shows progress)
        import sys
        import asyncio
        import threading

        client = PageIndexClient(api_key=pi_api_key or None, model=model)
        col = client.collection()
        try:
            stream = col.query(question, doc_ids=[doc_id], stream=True)
            collected: list[str] = []
            done = threading.Event()

            async def _consume():
                try:
                    async for event in stream:
                        if event.type == "answer_delta":
                            sys.stdout.write(event.data)
                            sys.stdout.flush()
                            collected.append(event.data)
                        elif event.type == "tool_call":
                            name = event.data.get("name", "")
                            args = event.data.get("args", "")
                            sys.stdout.write(f"\n  [PageIndex] {name}({args})\n")
                            sys.stdout.flush()
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                finally:
                    done.set()

            # Run streaming in a separate thread with its own event loop
            def _run():
                loop = asyncio.new_event_loop()
                loop.run_until_complete(_consume())
                loop.close()

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=120)
            return "".join(collected) if collected else "No answer from PageIndex."
        except Exception as exc:
            return f"Error querying cloud PageIndex: {exc}"

    # Local doc: use local PageIndex with structure-based retrieval
    client = PageIndexClient(model=model, storage_path=openkb_dir)
    col = client.collection()

    try:
        structure = col.get_document_structure(doc_id)
    except Exception as exc:
        return f"Error retrieving document structure: {exc}"

    if not structure:
        return "No structure found for document."
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


def build_query_agent(wiki_root: str, openkb_dir: str, model: str, language: str = "en") -> Agent:
    """Build and return the Q&A agent.

    Args:
        wiki_root: Absolute path to the wiki directory.
        openkb_dir: Path to the .openkb/ state directory.
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
        return _pageindex_retrieve_impl(doc_id, question, openkb_dir, model)

    from agents.model_settings import ModelSettings

    return Agent(
        name="wiki-query",
        instructions=instructions,
        tools=[list_files, read_file, pageindex_retrieve],
        model=model,
        model_settings=ModelSettings(parallel_tool_calls=False),
    )


async def run_query(question: str, kb_dir: Path, model: str, stream: bool = False) -> str:
    """Run a Q&A query against the knowledge base.

    Args:
        question: The user's question.
        kb_dir: Root of the knowledge base.
        model: LLM model name.
        stream: If True, print response tokens to stdout as they arrive.

    Returns:
        The agent's final answer as a string.
    """
    import sys
    from agents import RawResponsesStreamEvent, RunItemStreamEvent, ItemHelpers
    from openai.types.responses import ResponseTextDeltaEvent
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_root = str(kb_dir / "wiki")
    openkb_path = str(openkb_dir)

    agent = build_query_agent(wiki_root, openkb_path, model, language=language)

    if not stream:
        result = await Runner.run(agent, question)
        return result.final_output or ""

    result = Runner.run_streamed(agent, question)
    collected = []
    async for event in result.stream_events():
        if isinstance(event, RawResponsesStreamEvent):
            if isinstance(event.data, ResponseTextDeltaEvent):
                text = event.data.delta
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    collected.append(text)
        elif isinstance(event, RunItemStreamEvent):
            item = event.item
            if item.type == "tool_call_item":
                raw = item.raw_item
                args = getattr(raw, "arguments", "{}")
                sys.stdout.write(f"\n[tool call] {raw.name}({args})\n")
                sys.stdout.flush()
            elif item.type == "tool_call_output_item":
                output = str(item.output)
                preview = output[:200] + "..." if len(output) > 200 else output
                sys.stdout.write(f"[tool output] {preview}\n\n")
                sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(collected) if collected else result.final_output or ""
