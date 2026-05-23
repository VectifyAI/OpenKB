"""Thin CLI/Generator wrapper around the deck-editorial skill.

The actual deck generation lives in
``skills/openkb-deck-editorial/SKILL.md`` and runs via
:func:`openkb.agent.skill_runner.run_skill`. This module exists only to:

* enforce the deck output path convention (``output/decks/<name>/index.html``),
* check that the skill actually wrote the expected file,
* (when ``critique=True``) chain the ``openkb-html-critic`` skill on the
  produced HTML.

Pre-skill-system implementation lived here too (build_deck_create_agent,
build_deck_critic_agent, snapshot/restore helpers). All deleted — see
git history before commit 08e95c3 if you need to reference it.
"""
from __future__ import annotations

from pathlib import Path

from openkb.agent.skill_runner import (
    MAX_TURNS,
    MAX_TURNS_WITH_CRITIQUE,
    SkillNotFoundError,
    run_skill,
)
from openkb.deck import deck_dir


CRITIC_MAX_TURNS = 40
"""Critic skill is read-and-patch, not authoring; it converges fast."""


async def run_deck_create(
    *,
    kb_dir: Path,
    deck_name: str,
    intent: str,
    model: str,
    critique: bool,
) -> Path:
    """Compile a single deck from the KB's wiki via the deck-editorial skill.

    Returns the deck directory. Raises ``RuntimeError`` if the skill is
    missing, hits the turn cap, or doesn't write ``index.html``.

    When ``critique=True`` the html-critic skill runs as a second pass on
    the produced file (CSS specificity bugs, missing nav, failure of
    self-containment). The critic patches in place; missing critic skill
    is a soft failure (the deck still ships, just unpatched).
    """
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
                max_turns=CRITIC_MAX_TURNS,
            )
        except SkillNotFoundError:
            # Critic skill missing is non-fatal — the unpatched deck
            # is still on disk and usable.
            pass

    return deck_root
