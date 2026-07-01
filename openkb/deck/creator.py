"""Thin CLI/Generator wrapper around any deck-producing skill.

The actual deck generation lives in skill packages — by default
``skills/openkb-deck-neon/SKILL.md``, but the caller may pass
``skill_name`` to route to a different one (e.g.
``deck-guizang-editorial`` from open-design). Skill execution runs
through :func:`openkb.agent.skill_runner.run_skill`, which also handles
output-path templating and post-run validation based on the skill's
``od:`` frontmatter.

This module exists only to:

* expose the deck CLI's ``critique`` flag as a second skill call
  (``openkb-html-critic``) chained after the producer skill,
* surface a clean ``Path``-returning interface for callers that don't
  care about skill plumbing.
"""

from __future__ import annotations

from pathlib import Path

from openkb.agent.skill_runner import (
    MAX_TURNS,
    MAX_TURNS_WITH_CRITIQUE,
    SkillNotFoundError,
    SkillRunResult,
    run_skill,
)
from openkb.deck import deck_dir

DEFAULT_DECK_SKILL = "openkb-deck-neon"
"""Skill name routed to when the CLI / chat doesn't pass ``--skill``."""

CRITIC_SKILL = "openkb-html-critic"
"""Skill chained after the producer when ``--critique`` is set."""

CRITIC_MAX_TURNS = 40
"""Critic is read-and-patch, not authoring; converges fast."""


async def run_deck_create(
    *,
    kb_dir: Path,
    deck_name: str,
    intent: str,
    model: str,
    critique: bool,
    skill_name: str = DEFAULT_DECK_SKILL,
) -> SkillRunResult:
    """Compile a single deck from the KB's wiki via the chosen skill.

    Args:
        skill_name: Which deck skill to run. Defaults to the built-in
            ``openkb-deck-neon``. Pass ``"deck-guizang-editorial"``
            etc. to route to a third-party skill installed under
            ``~/.openkb/skills/``.

    Returns the :class:`SkillRunResult` from the producer skill (carries
    ``output_path`` and ``validation`` populated by ``run_skill`` per
    the skill's frontmatter contract).

    Raises ``RuntimeError`` if the skill is missing, hits the turn cap,
    or doesn't write its declared output path.

    When ``critique=True`` the html-critic skill runs as a second pass
    on the produced file. Missing critic skill is a soft failure (the
    deck still ships, just unpatched).
    """
    # Ensure the conventional deck dir exists. Skills that use
    # output_path_template = "output/decks/{slug}/index.html" need this;
    # skills that pick their own location won't be hindered.
    deck_root = deck_dir(kb_dir, deck_name)
    deck_root.mkdir(parents=True, exist_ok=True)

    try:
        result = await run_skill(
            skill_name=skill_name,
            intent=intent,
            kb_dir=kb_dir,
            model=model,
            slug=deck_name,
            max_turns=MAX_TURNS_WITH_CRITIQUE if critique else MAX_TURNS,
        )
    except SkillNotFoundError as exc:
        raise RuntimeError(
            f"Deck skill {skill_name!r} is not installed. "
            f"Drop a SKILL.md into ~/.openkb/skills/{skill_name}/ (or "
            f"<kb>/skills/{skill_name}/) and re-run."
        ) from exc

    if critique:
        # The producer's output_path tells the critic which file to patch.
        # If the producer didn't template a path, fall back to the
        # conventional location.
        #
        # ``result.output_path`` is already ``.resolve()``-d by run_skill;
        # ``kb_dir`` may still hold an un-resolved form (e.g. ``/tmp/...``
        # on macOS where ``/tmp`` symlinks to ``/private/tmp``). Resolve
        # the KB root too so ``relative_to`` doesn't trip on the symlink.
        target_path = (
            result.output_path.relative_to(kb_dir.resolve())
            if result.output_path is not None
            else Path(f"output/decks/{deck_name}/index.html")
        )
        critic_intent = (
            f"Critique and patch the HTML deck at: {target_path}\n"
            f"Original user brief (for context, do NOT change content):\n{intent}"
        )
        try:
            await run_skill(
                skill_name=CRITIC_SKILL,
                intent=critic_intent,
                kb_dir=kb_dir,
                model=model,
                max_turns=CRITIC_MAX_TURNS,
            )
        except SkillNotFoundError:
            # Critic missing is non-fatal — the unpatched deck still ships.
            pass

    return result
