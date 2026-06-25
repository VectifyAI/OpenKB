"""Q&A agent for querying the OpenKB knowledge base."""
from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner, function_tool

from agents import ToolOutputImage, ToolOutputText
from openkb.config import get_extra_headers, get_timeout_extra_args
from openkb.agent.tools import (
    get_wiki_page_content,
    read_wiki_file,
    read_wiki_image,
    write_kb_file,
)

MAX_TURNS = 50
from openkb.schema import get_agents_md

_QUERY_INSTRUCTIONS_TEMPLATE = """\
You are OpenKB, a knowledge-base Q&A agent. You answer questions by searching the wiki.

{schema_md}

## Search strategy
1. Read index.md to see all documents and concepts with brief summaries.
   Each document is marked (short) or (pageindex) to indicate its type.
2. Read relevant summary pages (summaries/) for document overviews.
   Summaries may omit details — if you need more, follow the summary's
   `full_text` frontmatter field to the source (see step 4).
3. Read concept pages (concepts/) for cross-document synthesis.
4. For "who/what is X" questions about a specific named person, organization,
   place, or product, read the matching page in entities/ first.
5. When you need detailed source document content, each summary page has a
   `full_text` frontmatter field with the path to the original document content:
   - Short documents (doc_type: short): read_file with that path.
   - PageIndex documents (doc_type: pageindex): use get_page_content(doc_name, pages)
     with tight page ranges. The summary shows document tree structure with page
     ranges to help you target. Never fetch the whole document.
6. Source content may reference images (e.g. ![image](sources/images/doc/file.png)).
   Use the get_image tool to view them when needed.
7. Synthesize a clear, concise, well-cited answer grounded in wiki content.

Answer based only on wiki content. Be concise.
Before each tool call, output one short sentence explaining the reason.

If you cannot find relevant information, say so clearly.
"""


def build_query_agent(wiki_root: str, model: str, language: str = "en") -> Agent:
    """Build and return the Q&A agent."""
    schema_md = get_agents_md(Path(wiki_root))
    instructions = _QUERY_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
    instructions += f"\n\nIMPORTANT: Answer in {language} language."

    @function_tool
    def read_file(path: str) -> str:
        """Read a Markdown file from the wiki.
        Args:
            path: File path relative to wiki root (e.g. 'summaries/paper.md').
        """
        return read_wiki_file(path, wiki_root)

    @function_tool
    def get_page_content(doc_name: str, pages: str) -> str:
        """Get text content of specific pages from a PageIndex (long) document.
        Only use for documents with doc_type: pageindex. For short documents,
        use read_file instead.
        Args:
            doc_name: Document name (e.g. 'attention-is-all-you-need').
            pages: Page specification (e.g. '3-5,7,10-12').
        """
        return get_wiki_page_content(doc_name, pages, wiki_root)

    @function_tool
    def get_image(image_path: str) -> ToolOutputImage | ToolOutputText:
        """View an image from the wiki.

        Use when a question asks about a specific figure, chart, or diagram
        you'd need to see to answer accurately.

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
        tools=[read_file, get_page_content, get_image],
        model=f"litellm/{model}",
        model_settings=ModelSettings(
            parallel_tool_calls=False,
            extra_headers=get_extra_headers() or None,
            extra_args=get_timeout_extra_args(),
        ),
    )


def build_chat_agent(
    kb_dir: Path,
    model: str,
    language: str = "en",
) -> Agent:
    """Build the chat agent: query agent + a write tool restricted to
    ``<kb>/wiki/explorations/**`` and ``<kb>/output/**`` + a ``ShellTool``
    advertising locally-installed Anthropic-style skills.

    This is the variant used by the interactive ``openkb chat`` REPL so users
    can iterate on generated artifacts (e.g. ``output/skills/<name>/``) via
    natural-language follow-ups without giving the agent unrestricted write
    access to the wiki.

    Skill discovery: ``openkb/agent/skills.scan_local_skills`` looks in
    ``<kb>/skills/``, ``~/.openkb/skills/``, ``~/.claude/skills/`` for
    ``SKILL.md`` files. Any found skill is exposed to the agent via
    ``ShellTool.environment.skills`` so the model can ``cat`` the skill body
    and follow its instructions when the user's request matches.
    """
    wiki_root = str(kb_dir / "wiki")
    kb_root = str(kb_dir)
    base = build_query_agent(wiki_root, model, language=language)

    @function_tool
    def write_file(path: str, content: str) -> str:
        """Write a text file under the KB.

        Allowed paths (relative to KB root):
          * ``wiki/explorations/**`` — chat-derived notes.
          * ``output/**``            — generator artifacts (skills, etc.).

        Any other path is rejected. Parent directories are created.

        Args:
            path: File path relative to KB root
                (e.g. ``"output/skills/demo/SKILL.md"``).
            content: Full text content to write (overwrites if file exists).
        """
        return write_kb_file(path, content, kb_root)

    extra_tools: list = [write_file]
    skill_instructions_addendum = ""

    # Skill discovery via function tools. The agents SDK has a richer
    # ``ShellTool``+``ShellToolLocalSkill`` mechanism for this, but those
    # are OpenAI Responses-API hosted tools; LiteLLM routes through
    # ChatCompletions which rejects hosted tools. So we use plain
    # ``function_tool`` primitives that work with any LiteLLM-routed model.
    from openkb.agent.skills import scan_local_skills

    skills = scan_local_skills(kb_dir)
    skill_index = {s["name"]: s for s in skills}

    if skill_index:
        skill_list_text = _format_skill_list(skills)

        @function_tool
        def list_skills() -> str:
            """List skills available in this environment.

            Returns a text catalog of installed Anthropic-style skills.
            Each entry has a name and a one-line description; use the
            description to decide whether the skill matches the user's
            request, then call ``read_skill(name)`` to load its body.
            """
            return skill_list_text

        @function_tool
        def read_skill(name: str) -> str:
            """Read a skill's ``SKILL.md`` body.

            Call this once you've decided a skill matches the user's
            request. The returned text is the full skill instructions
            (frontmatter stripped). Follow it as your working method
            and write outputs via the ``write_file`` tool.

            Args:
                name: skill name as listed by ``list_skills``.
            """
            entry = skill_index.get(name)
            if entry is None:
                return (
                    f"Unknown skill: {name!r}. Call list_skills() to see "
                    f"available skills."
                )
            md_path = Path(entry["path"]) / "SKILL.md"
            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError as exc:
                return f"Could not read {md_path}: {exc}"
            # Strip frontmatter, return body only.
            from openkb.agent.skills import _parse_frontmatter
            _, body = _parse_frontmatter(text)
            return body

        extra_tools.extend([list_skills, read_skill])

        # Build the prompt addendum listing skill names + descriptions
        # right inside the system prompt so the model sees them up front
        # and knows what to look for, even before deciding to call
        # list_skills(). This is the difference between "agent
        # eventually discovers skills" and "agent treats skill use as
        # the default for matching requests".
        skill_lines = []
        for s in skills:
            desc_one_line = " ".join(s["description"].split())
            skill_lines.append(f"- **{s['name']}** — {desc_one_line}")
        skill_instructions_addendum = (
            "\n\n## Available skills\n\n"
            "The following Anthropic-style skill packages are installed in "
            "this environment. **When a user request matches a skill's "
            "description (e.g. 'make a deck', 'generate slides', 'draft a "
            "report'), you MUST call `read_skill(name)` to load that "
            "skill's full instructions and follow them strictly** — do not "
            "freestyle the output format if a skill covers it.\n\n"
            + "\n".join(skill_lines)
            + "\n\nIf no listed skill matches the request, proceed with "
            "your default tools."
        )

    new_instructions = (base.instructions or "") + skill_instructions_addendum
    return base.clone(
        tools=[*base.tools, *extra_tools],
        instructions=new_instructions,
    )


def _format_skill_list(skills: list[dict[str, str]]) -> str:
    """Render the skill catalog as a compact text block for the agent."""
    if not skills:
        return "No skills installed."
    lines = [f"{len(skills)} skill(s) available:\n"]
    for s in skills:
        lines.append(f"- {s['name']}")
        # Indent description; keep it one paragraph so the agent reads it fast.
        desc = " ".join(s["description"].split())
        lines.append(f"    {desc}")
    lines.append(
        "\nTo use a skill, call read_skill(name) and follow its instructions."
    )
    return "\n".join(lines)


async def run_query(
    question: str,
    kb_dir: Path,
    model: str,
    stream: bool = False,
    *,
    raw: bool = False,
) -> str:
    """Run a Q&A query against the knowledge base.

    Args:
        question: The user's question.
        kb_dir: Root of the knowledge base.
        model: LLM model name.
        stream: If True, print response tokens to stdout as they arrive.
        raw: If True, write raw markdown source instead of rendering it
            (still keeps tool-call line styling).

    Returns:
        The agent's final answer as a string.
    """
    import sys
    from agents import RawResponsesStreamEvent, RunItemStreamEvent
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

    import os
    use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR", "")

    from openkb.agent.chat import (
        _build_style,
        _fmt,
        _format_tool_line,
        _make_markdown,
        _make_rich_console,
    )

    style = _build_style(use_color)

    from rich.live import Live

    if use_color and not raw:
        console = _make_rich_console()
    else:
        console = None  # type: ignore[assignment]

    def _start_live() -> Live | None:
        if console is None:
            return None
        lv = Live(console=console, vertical_overflow="visible")
        lv.start()
        return lv

    live: Live | None = None
    last_was_text = False
    need_blank_before_text = False
    result = Runner.run_streamed(agent, question, max_turns=MAX_TURNS)
    collected: list[str] = []
    segment: list[str] = []
    try:
        live = _start_live()
        async for event in result.stream_events():
            if isinstance(event, RawResponsesStreamEvent):
                if isinstance(event.data, ResponseTextDeltaEvent):
                    text = event.data.delta
                    if text:
                        if need_blank_before_text:
                            if console is not None:
                                print()
                                segment = []
                                live = _start_live()
                            else:
                                sys.stdout.write("\n")
                            need_blank_before_text = False
                        collected.append(text)
                        segment.append(text)
                        last_was_text = True
                        if live:
                            if "\n" in text:
                                joined = "".join(segment)
                                visible = joined[: joined.rfind("\n") + 1]
                                if visible:
                                    live.update(_make_markdown(visible))
                        else:
                            sys.stdout.write(text)
                            sys.stdout.flush()
            elif isinstance(event, RunItemStreamEvent):
                item = event.item
                if item.type == "tool_call_item":
                    if last_was_text:
                        if live:
                            if segment:
                                live.update(_make_markdown("".join(segment)))
                            live.stop()
                            live = None
                        else:
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                        last_was_text = False
                    raw_item = item.raw_item
                    name = getattr(raw_item, "name", "?")
                    args = getattr(raw_item, "arguments", "") or ""
                    if live:
                        live.stop()
                        live = None
                    _fmt(style, ("class:tool", _format_tool_line(name, args) + "\n"))
                    need_blank_before_text = True
                elif item.type == "tool_call_output_item":
                    pass
    finally:
        if live:
            if segment:
                live.update(_make_markdown("".join(segment)))
            live.stop()
        print()
    return "".join(collected) if collected else result.final_output or ""
