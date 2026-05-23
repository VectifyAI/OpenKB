"""The deck-create agent.

Builds on the same openai-agents SDK + LiteLLM stack as the skill-create
agent in ``openkb.skill.creator``. Differences:

* System prompt comes from ``openkb/prompts/deck_create.md`` and is
  interpolated with intent, deck_name, and wiki schema.
* Output is one self-contained ``index.html`` (not a SKILL.md + refs/).
* Optional ``critique=True`` mode wires a critic agent as an SDK
  handoff target (see ``openkb.deck.critic``); the main agent transfers
  to it instead of calling ``done()``.
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

    main_tools = [
        list_wiki_dir,
        read_wiki_file,
        get_page_content,
        get_image,
        query_wiki,
        write_deck_file,
        done,
    ]

    if not critique:
        return Agent(
            name="deck-creator",
            instructions=instructions,
            tools=main_tools,
            model=f"litellm/{model}",
            # Parallel reads (early fan-out: list dir → read N summaries → read N
            # source page-ranges) saves 5-10 round-trips per compile. Writes
            # naturally serialise since each write_deck_file depends on prior
            # reads; model has no reason to write the same path twice in
            # parallel.
            model_settings=ModelSettings(parallel_tool_calls=True),
        )

    # critique=True: wire critic agent as a handoff target. The agents
    # SDK auto-exposes a `transfer_to_<critic_name>` tool when handoffs
    # is non-empty; we don't need to declare it in `main_tools` ourselves.
    from openkb.deck.critic import build_deck_critic_agent

    critic_agent = build_deck_critic_agent(
        wiki_root=wiki_root,
        deck_root=deck_root,
        intent=intent,
        model=model,
    )

    # The critic's prompt step 1 instructs it to call its `take_snapshot`
    # tool as the first action, which creates index.pre-critique.html.
    # We tried an SDK lifecycle hook (on_handoff) for this, but the
    # pinned openai-agents version exposes lifecycle callbacks only via
    # the Agent.hooks dataclass field (AgentHooks subclass), not as a
    # settable attribute. The prompt-driven approach is simpler and
    # works regardless of SDK version.

    handoff_instructions = (
        "\n\n## Critique pass\n\n"
        "A critic agent will review your work. When you have finished "
        "writing index.html and run your self-check, **do not call `done()`** — "
        "instead transfer to the critic via the handoff tool. The critic "
        "will revise the deck and call `done` itself."
    )

    return Agent(
        name="deck-creator",
        instructions=instructions + handoff_instructions,
        tools=main_tools,
        handoffs=[critic_agent],
        model=f"litellm/{model}",
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

    **As of skill-system refactor:** internally loads the
    ``openkb-deck-editorial`` skill via :func:`openkb.agent.skill_runner.run_skill`,
    then (if ``critique``) the ``openkb-html-critic`` skill on the
    produced file. The CLI surface (``openkb deck new``) is unchanged —
    only the internals are now the unified skill pipeline.

    Returns the deck directory. Raises ``RuntimeError`` if the agent
    finishes without writing ``index.html``, or if the run hits the
    turn cap.
    """
    from openkb.agent.skill_runner import (
        MAX_TURNS,
        MAX_TURNS_WITH_CRITIQUE,
        SkillNotFoundError,
        run_skill,
    )

    deck_root = deck_dir(kb_dir, deck_name)
    deck_root.mkdir(parents=True, exist_ok=True)
    target_path = f"output/decks/{deck_name}/index.html"

    builder_intent = (
        f"Deck slug: {deck_name}\n"
        f"Write the deck to: {target_path}\n\n"
        f"User brief:\n{intent}"
    )

    try:
        await run_skill(
            skill_name="openkb-deck-editorial",
            intent=builder_intent,
            kb_dir=kb_dir,
            model=model,
            max_turns=MAX_TURNS_WITH_CRITIQUE if critique else MAX_TURNS,
        )
    except SkillNotFoundError as exc:
        raise RuntimeError(
            f"Required skill 'openkb-deck-editorial' is missing. "
            f"It ships at skills/openkb-deck-editorial/SKILL.md — "
            f"ensure it's present or re-install openkb."
        ) from exc

    if not (deck_root / "index.html").exists():
        raise RuntimeError(
            f"Deck generation finished but the skill did not write "
            f"index.html at {deck_root}. The deck is incomplete; "
            f"check whether the wiki has content related to your intent."
        )

    if critique:
        critic_intent = (
            f"Critique and patch the HTML deck at: {target_path}\n"
            f"Original user brief (for context, do not change content):\n{intent}"
        )
        try:
            await run_skill(
                skill_name="openkb-html-critic",
                intent=critic_intent,
                kb_dir=kb_dir,
                model=model,
                max_turns=40,  # critic is reading + patching, not authoring
            )
        except SkillNotFoundError:
            # Critic skill missing is a soft failure — the deck is still
            # on disk; just no patch pass happened.
            pass

    return deck_root


async def _legacy_run_deck_create_pre_skill_refactor(
    *,
    kb_dir: Path,
    deck_name: str,
    intent: str,
    model: str,
    critique: bool,
) -> Path:
    """Pre-skill-refactor implementation. Kept under a renamed symbol
    so the existing test_deck_creator tests still have a target while
    we update them in a follow-up. New CLI / chat paths use
    :func:`run_deck_create` above, which delegates to the skill runner.
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
