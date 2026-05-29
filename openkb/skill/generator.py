"""Generator primitive — shared abstraction for all `<kb>/output/<type>/` artifacts.

v0.3 supports ``target_type="skill"`` and ``target_type="deck"``. Both
targets route through ``openkb.agent.skill_runner.run_skill`` under the
hood; ``Generator`` is the thin wrapper that owns:

* output-path convention: ``<kb>/output/<type>/<name>/``
* post-run hooks: skill target regenerates ``marketplace.json``; deck
  target has no per-target hook (the producer SKILL.md's frontmatter
  ``od.deck_grammar`` already drove validation inside ``run_skill``,
  and the html-critic skill, when chained, patched the file in place)

The artifact CONTENT for each target is a ``SKILL.md`` under
``skills/`` — the dispatch here is purely the orchestration shell
(arg-routing, output path resolution, post-run hook firing).

A third target type would require editing this module (the ``Literal``
type, the ``target_type`` check in ``__init__``, the ``if/else`` in
``run``). A plug-in registry refactor is in the deferred-followups list
(score 70 in the architectural review); current ``if/else`` is
intentional v0.x scope.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Union

from openkb.deck import deck_dir
from openkb.deck.creator import DEFAULT_DECK_SKILL, run_deck_create
from openkb.deck.validator import ValidationResult as DeckValidationResult
from openkb.skill import skill_dir
from openkb.skill.creator import run_skill_create
from openkb.skill.marketplace import regenerate_marketplace
from openkb.skill.validator import (
    ValidationResult as SkillValidationResult,
    validate_skill,
)


TargetType = Literal["skill", "deck"]
AnyValidationResult = Union[SkillValidationResult, DeckValidationResult]


class Generator:
    """A v0.3 generator instance.

    Args:
        target_type: ``"skill"`` or ``"deck"``.
        name: kebab-case slug; becomes the output directory name.
        intent: natural-language description of the desired artifact.
        kb_dir: KB root.
        model: LiteLLM model name (from KB config).
        critique: (deck only) opt-in second-pass via the
            ``openkb-html-critic`` skill which patches the produced HTML
            in place. Ignored for ``target_type="skill"``.
    """

    def __init__(
        self,
        *,
        target_type: TargetType,
        name: str,
        intent: str,
        kb_dir: Path,
        model: str,
        critique: bool = False,
        skill_name: str | None = None,
    ) -> None:
        """Args:
            skill_name: For ``target_type="deck"``, which deck skill to use.
                Defaults to :data:`openkb.deck.creator.DEFAULT_DECK_SKILL`
                (``"openkb-deck-editorial"``). Ignored for skill target.
        """
        if target_type not in ("skill", "deck"):
            raise ValueError(
                f"Unknown target_type {target_type!r}. v0.3 supports 'skill' and 'deck'."
            )
        self.target_type: TargetType = target_type
        self.name = name
        self.intent = intent
        self.kb_dir = kb_dir
        self.model = model
        self.critique = critique
        self.skill_name = skill_name or DEFAULT_DECK_SKILL
        self.output_dir = (
            deck_dir(kb_dir, name) if target_type == "deck" else skill_dir(kb_dir, name)
        )
        self.validation: AnyValidationResult | None = None

    async def run(self) -> Path:
        """Execute the generator. Returns the path to the produced artifact.

        Side-effects, in order: compile → validate → (skill only) publish
        manifest. ``self.validation`` holds the result so callers can
        surface issues without re-running the validator. For deck target,
        validation runs inside ``run_skill`` via the producing skill's
        frontmatter-declared grammar; we propagate it up.
        """
        if self.target_type == "skill":
            await run_skill_create(
                kb_dir=self.kb_dir,
                skill_name=self.name,
                intent=self.intent,
                model=self.model,
            )
            self.validation = validate_skill(self.output_dir)
            regenerate_marketplace(self.kb_dir)
            return self.output_dir

        # target_type == "deck"
        deck_result = await run_deck_create(
            kb_dir=self.kb_dir,
            deck_name=self.name,
            intent=self.intent,
            model=self.model,
            critique=self.critique,
            skill_name=self.skill_name,
        )
        # run_deck_create returns a SkillRunResult-like (or Path) — use its
        # validation if present; otherwise fall back to None (skill didn't
        # declare a grammar to validate against).
        self.validation = deck_result.validation
        return self.output_dir
