"""Q&A agent for querying the OpenKB knowledge base."""
from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner, function_tool

from agents import ToolOutputImage, ToolOutputText
from openkb.agent.tools import read_wiki_file, read_wiki_image

MAX_TURNS = 50
from openkb.schema import get_agents_md

_QUERY_INSTRUCTIONS_TEMPLATE = """\
You are a knowledge-base Q&A agent. You answer questions by searching the wiki.

{schema_md}

## Search strategy
1. Read index.md to see all documents and concepts with brief summaries.
   Each document is marked (short) or (pageindex) to indicate its type.
2. Read relevant summary pages (summaries/) for document overviews.
   Note: summaries may omit details.
3. Read concept pages (concepts/) for cross-document synthesis.
4. When you need detailed source document content, each summary page has a
   `full_text` frontmatter field with the path to the original document content:
   - Short documents (doc_type: short): read_file with that path.
   - PageIndex documents (doc_type: pageindex): use get_page_content(doc_name, pages)
     with tight page ranges. The summary shows document tree structure with page
     ranges to help you target. Never fetch the whole document.
5. When source content references images (e.g. ![image](sources/images/doc/file.png)),
   use get_image to view them. Always view images when the question asks about
   a figure, chart, diagram, or visual content.
6. Synthesize a clear, concise, well-cited answer grounded in wiki content.

Answer based only on wiki content. Be concise.
Before each tool call, output one short sentence explaining the reason.

If you cannot find relevant information, say so clearly.
"""


def build_query_agent(wiki_root: str, model: str, language: str = "en") -> Agent:
    """Build and return the Q&A agent."""
    schema_md = get_agents_md(Path(wiki_root))
    instructions = _QUERY_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
    instructions += f"\n\nIMPORTANT: Write all wiki content in {language} language."

    @function_tool
    def read_file(path: str) -> str:
        """Read a Markdown file from the wiki.
        Args:
            path: File path relative to wiki root (e.g. 'summaries/paper.md').
        """
        return read_wiki_file(path, wiki_root)

    @function_tool
    def get_page_content_tool(doc_name: str, pages: str) -> str:
        """Get text content of specific pages from a PageIndex (long) document.
        Only use for documents with doc_type: pageindex. For short documents,
        use read_file instead.
        Args:
            doc_name: Document name (e.g. 'attention-is-all-you-need').
            pages: Page specification (e.g. '3-5,7,10-12').
        """
        from openkb.agent.tools import get_page_content
        return get_page_content(doc_name, pages, wiki_root)

    @function_tool
    def get_image(image_path: str) -> ToolOutputImage | ToolOutputText:
        """View an image from the wiki.
        Use when source content references images you need to see.
        Args:
            image_path: Image path relative to wiki root (e.g. 'sources/images/doc/p1_img1.png').
        """
        result = read_wiki_image(image_path, wiki_root)
        if result["type"] == "image":
            return ToolOutputImage(image_url=result["image_url"])
        return ToolOutputText(text=result["text"])

    from agents.model_settings import ModelSettings

    return Agent(
        name="wiki-query",
        instructions=instructions,
        tools=[read_file, get_page_content_tool, get_image],
        model=f"litellm/{model}",
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

    agent = build_query_agent(wiki_root, model, language=language)

    if not stream:
        result = await Runner.run(agent, question, max_turns=MAX_TURNS)
        return result.final_output or ""

    result = Runner.run_streamed(agent, question, max_turns=MAX_TURNS)
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
                sys.stdout.write(f"\n[tool call] {raw.name}({args})\n\n")
                sys.stdout.flush()
            elif item.type == "tool_call_output_item":
                pass
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(collected) if collected else result.final_output or ""
