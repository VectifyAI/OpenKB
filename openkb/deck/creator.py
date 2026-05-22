"""The deck-create agent.

Builds on the same openai-agents SDK + LiteLLM stack as the skill-create
agent in ``openkb.skill.creator``. Differences:

* System prompt comes from ``openkb/prompts/deck_create.md`` and is
  interpolated with intent, deck_name, and wiki schema.
* Output is one self-contained ``index.html`` (not a SKILL.md + refs/).
* Optional ``critique=True`` mode adds a critic agent via SDK handoff
  (see Task 7 — until then, only ``critique=False`` is wired).
* The runner verifies that ``index.html`` was written before declaring
  success.
"""
from __future__ import annotations

from pathlib import Path

from agents import Agent, Runner, ToolOutputImage, ToolOutputText, function_tool
from agents.model_settings import ModelSettings

from openkb.deck import deck_dir
from openkb.deck.tools import write_deck_file as _write_deck_file_impl
from openkb.prompts import load_prompt
from openkb.schema import get_agents_md
from openkb.skill.tools import (
    get_skill_page_content as _get_page_content_impl,
    list_wiki_dir as _list_wiki_dir_impl,
    read_skill_image as _read_image_impl,
    read_wiki_file_for_skill as _read_wiki_file_impl,
)

MAX_TURNS = 80
MAX_TURNS_WITH_CRITIQUE = 120


def build_deck_create_agent(
    *,
    wiki_root: str,
    deck_root: str,
    deck_name: str,
    intent: str,
    model: str,
    critique: bool,
) -> Agent:
    """Build the openai-agents Agent for generating one deck.

    Args:
        wiki_root: ``<kb>/wiki`` absolute path.
        deck_root: ``<kb>/output/decks/<name>`` absolute path. Created
            if it does not exist.
        deck_name: kebab-case slug.
        intent: the user's natural-language brief.
        model: LiteLLM-formatted model name from KB config.
        critique: when True, wire the critic agent as a handoff target
            (see Task 7).
    """
    Path(deck_root).mkdir(parents=True, exist_ok=True)

    wiki_schema = get_agents_md(Path(wiki_root))
    instructions = load_prompt("deck_create").format(
        intent=intent,
        deck_name=deck_name,
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

        Args:
            doc_name: Document name without extension.
            pages: Page spec such as ``"3-5,7,10-12"``.
        """
        return _get_page_content_impl(doc_name, pages, wiki_root)

    @function_tool
    def get_image(image_path: str) -> ToolOutputImage | ToolOutputText:
        """View an image referenced in the wiki.

        Args:
            image_path: Path relative to wiki/.
        """
        result = _read_image_impl(image_path, wiki_root)
        if result["type"] == "image":
            return ToolOutputImage(image_url=result["image_url"])
        return ToolOutputText(text=result["text"])

    @function_tool
    async def query_wiki(question: str) -> str:
        """Semantic search over the wiki — narrow follow-ups only.

        Nested LLM call. Use only when direct file reads can't easily
        answer a specific sub-question.
        """
        # Lazy import to avoid a circular dependency at module load time.
        from openkb.agent.query import run_query
        kb_dir = Path(wiki_root).parent
        return await run_query(question, kb_dir, model, stream=False)

    @function_tool
    def write_deck_file(path: str, content: str) -> str:
        """Write a file under the deck directory."""
        return _write_deck_file_impl(path, content, deck_root)

    @function_tool
    def done(summary: str) -> str:
        """Signal the deck is complete. Call exactly once when finished."""
        return f"Deck marked done: {summary}"

    # critique=True wiring is added in Task 7. For now, no handoffs.
    if critique:
        raise NotImplementedError(
            "--critique wiring lands in Task 7. Until then, build_deck_create_agent "
            "only supports critique=False."
        )

    return Agent(
        name="deck-creator",
        instructions=instructions,
        tools=[
            list_wiki_dir,
            read_wiki_file,
            get_page_content,
            get_image,
            query_wiki,
            write_deck_file,
            done,
        ],
        model=f"litellm/{model}",
        # Parallel reads (early fan-out: list dir → read N summaries → read N
        # source page-ranges) saves 5-10 round-trips per compile. Writes
        # naturally serialise since each write_deck_file depends on prior
        # reads; model has no reason to write the same path twice in
        # parallel.
        model_settings=ModelSettings(parallel_tool_calls=True),
    )


async def run_deck_create(
    *,
    kb_dir: Path,
    deck_name: str,
    intent: str,
    model: str,
    critique: bool,
) -> Path:
    """Compile a single deck from the KB's wiki.

    Returns the deck directory. Raises ``RuntimeError`` if the agent
    finishes without writing ``index.html``, or if the SDK hits its
    turn cap.
    """
    wiki_root = str(kb_dir / "wiki")
    deck_root = deck_dir(kb_dir, deck_name)

    agent = build_deck_create_agent(
        wiki_root=wiki_root,
        deck_root=str(deck_root),
        deck_name=deck_name,
        intent=intent,
        model=model,
        critique=critique,
    )

    seed = (
        f"Generate the deck '{deck_name}'. Follow the system prompt's "
        f"working method. Read the wiki, then write index.html."
    )

    # Lazy import — same reason as skill creator: keep top of module
    # independent of agents SDK's exception layout.
    from agents.exceptions import MaxTurnsExceeded

    turn_cap = MAX_TURNS_WITH_CRITIQUE if critique else MAX_TURNS

    try:
        await Runner.run(agent, seed, max_turns=turn_cap)
    except MaxTurnsExceeded as exc:
        raise RuntimeError(
            f"Deck generation hit the {turn_cap}-step cap before finishing. "
            f"The wiki may be too large for a single deck, or the intent may "
            f"be too broad. Try splitting into multiple decks with narrower "
            f"intents, or pass a smaller wiki subset."
        ) from exc

    if not (deck_root / "index.html").exists():
        raise RuntimeError(
            f"Deck generation finished but agent did not write index.html "
            f"at {deck_root}. The deck is incomplete; check the wiki has "
            f"content related to your intent."
        )

    return deck_root
