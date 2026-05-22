"""The deck-critic agent.

Invoked via OpenAI Agents SDK handoff from the deck-creator (see
``openkb.deck.creator``). Inherits the creator's full conversation
history — tool calls, wiki reads, reasoning trace, the written HTML —
which is why critic does not need to re-read the wiki to revise.

Critic-only tools:
  * read_deck_file — main's write_deck_file result string is "Written:
    index.html", not the body, so critic needs an explicit re-read.

Safety:
  * ``snapshot_pre_critique`` copies ``index.html`` →
    ``index.pre-critique.html`` the instant control transfers to critic
    (called from the SDK lifecycle hook attached in
    ``build_deck_create_agent`` Task 7).
  * ``restore_pre_critique`` puts the snapshot back when critic raises,
    hits MaxTurnsExceeded, or produces output that fails validation.
  * ``cleanup_pre_critique`` removes the snapshot once critique has
    succeeded and validation passes — keeps the deck directory clean
    across runs.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from agents import Agent, ToolOutputImage, ToolOutputText, function_tool
from agents.model_settings import ModelSettings

from openkb.deck.tools import (
    read_deck_file as _read_deck_file_impl,
    write_deck_file as _write_deck_file_impl,
)
from openkb.prompts import load_prompt
from openkb.skill.tools import (
    get_skill_page_content as _get_page_content_impl,
    list_wiki_dir as _list_wiki_dir_impl,
    read_skill_image as _read_image_impl,
    read_wiki_file_for_skill as _read_wiki_file_impl,
)

PRE_CRITIQUE_SUFFIX = "pre-critique"  # → "index.pre-critique.html"


def snapshot_pre_critique(deck_root: Path) -> Path:
    """Copy index.html → index.pre-critique.html. Returns the snapshot path.

    Raises FileNotFoundError if index.html is missing — that means the
    creator never wrote it, and there is nothing for critic to revise.
    """
    src = deck_root / "index.html"
    if not src.is_file():
        raise FileNotFoundError(
            f"snapshot_pre_critique: {src} missing — creator did not produce a draft."
        )
    dst = deck_root / f"index.{PRE_CRITIQUE_SUFFIX}.html"
    shutil.copy2(src, dst)
    return dst


def restore_pre_critique(deck_root: Path) -> None:
    """Restore index.pre-critique.html → index.html. No-op if snapshot absent."""
    src = deck_root / f"index.{PRE_CRITIQUE_SUFFIX}.html"
    if not src.is_file():
        return  # idempotent — nothing to restore
    dst = deck_root / "index.html"
    shutil.copy2(src, dst)


def cleanup_pre_critique(deck_root: Path) -> None:
    """Delete index.pre-critique.html if present. No-op if absent.

    Task 7's lifecycle wiring calls this once the critique pass has
    completed and validation has passed — at that point the snapshot is
    no longer needed, and leaving it on disk would accumulate stale
    files across runs.
    """
    snapshot = deck_root / f"index.{PRE_CRITIQUE_SUFFIX}.html"
    if snapshot.is_file():
        snapshot.unlink()


def build_deck_critic_agent(
    *,
    wiki_root: str,
    deck_root: str,
    intent: str,
    model: str,
) -> Agent:
    """Build the openai-agents Agent for revising a draft deck.

    Args:
        wiki_root: ``<kb>/wiki`` absolute path.
        deck_root: ``<kb>/output/decks/<name>`` absolute path.
        intent: the original user intent the creator received.
        model: LiteLLM-formatted model name.
    """
    instructions = load_prompt("deck_critique").format(intent=intent)

    @function_tool
    def list_wiki_dir(directory: str) -> str:
        """List .md files in a wiki subdirectory."""
        return _list_wiki_dir_impl(directory, wiki_root)

    @function_tool
    def read_wiki_file(path: str) -> str:
        """Read a wiki markdown file by path relative to wiki/."""
        return _read_wiki_file_impl(path, wiki_root)

    @function_tool
    def get_page_content(doc_name: str, pages: str) -> str:
        """Get text content of specific pages from a PageIndex document."""
        return _get_page_content_impl(doc_name, pages, wiki_root)

    @function_tool
    def get_image(image_path: str) -> ToolOutputImage | ToolOutputText:
        """View an image referenced in the wiki."""
        result = _read_image_impl(image_path, wiki_root)
        if result["type"] == "image":
            return ToolOutputImage(image_url=result["image_url"])
        return ToolOutputText(text=result["text"])

    @function_tool
    async def query_wiki(question: str) -> str:
        """Semantic search over the wiki — narrow follow-ups only."""
        from openkb.agent.query import run_query
        kb_dir = Path(wiki_root).parent
        return await run_query(question, kb_dir, model, stream=False)

    @function_tool
    def take_snapshot() -> str:
        """Save the current index.html as index.pre-critique.html.

        Call this as your FIRST action when starting the critique, before
        reading or modifying anything. It preserves the creator's draft
        so it can be restored if your revisions fail validation.
        """
        snapshot_pre_critique(Path(deck_root))
        return "Snapshot taken: index.pre-critique.html"

    @function_tool
    def read_deck_file(path: str) -> str:
        """Read a file from the deck directory (e.g. 'index.html')."""
        return _read_deck_file_impl(path, deck_root)

    @function_tool
    def write_deck_file(path: str, content: str) -> str:
        """Write a file under the deck directory."""
        return _write_deck_file_impl(path, content, deck_root)

    @function_tool
    def done(summary: str) -> str:
        """Signal the critique is complete. Call exactly once when finished."""
        return f"Critique marked done: {summary}"

    return Agent(
        name="deck-critic",
        instructions=instructions,
        tools=[
            list_wiki_dir,
            read_wiki_file,
            get_page_content,
            get_image,
            query_wiki,
            take_snapshot,
            read_deck_file,
            write_deck_file,
            done,
        ],
        model=f"litellm/{model}",
        # Critic edits sequentially: read draft → patch. Parallel tool
        # calls would let it issue overlapping writes to the same file,
        # which is unsafe.
        model_settings=ModelSettings(parallel_tool_calls=False),
    )
