"""Generator primitive — shared abstraction for all `<kb>/output/<type>/` artifacts.

v0.2 supports ``target_type="skill"`` and ``target_type="deck"``. Future
targets (``"podcast"``, ``"report"``, ``"video"``) plug in here under the
same conventions:

* output-path convention: ``<kb>/output/<type>/<name>/``
* post-compile validation: target-specific validator dispatched here
* post-run hooks: skill regenerates marketplace.json; deck does not (it's
  not a Claude Code plugin)

Each target plugs in its own ``run`` coroutine. v0.2 dispatches to
``openkb.skill.creator.run_skill_create`` or
``openkb.deck.creator.run_deck_create``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Union

from openkb.deck import deck_dir
from openkb.deck.creator import run_deck_create
from openkb.deck.critic import cleanup_pre_critique, restore_pre_critique
from openkb.deck.validator import (
    ValidationResult as DeckValidationResult,
    validate_deck,
)
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
    """A v0.2 generator instance.

    Args:
        target_type: ``"skill"`` or ``"deck"``.
        name: kebab-case slug; becomes the output directory name.
        intent: natural-language description of the desired artifact.
        kb_dir: KB root.
        model: LiteLLM model name (from KB config).
        critique: (deck only) opt-in second-pass review via SDK handoff.
            Ignored for ``target_type="skill"``.
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
    ) -> None:
        if target_type not in ("skill", "deck"):
            raise ValueError(
                f"Unknown target_type {target_type!r}. v0.2 supports 'skill' and 'deck'."
            )
        self.target_type: TargetType = target_type
        self.name = name
        self.intent = intent
        self.kb_dir = kb_dir
        self.model = model
        self.critique = critique
        self.output_dir = (
            deck_dir(kb_dir, name) if target_type == "deck" else skill_dir(kb_dir, name)
        )
        self.validation: AnyValidationResult | None = None

    async def run(self) -> Path:
        """Execute the generator. Returns the path to the produced artifact.

        Side-effects, in order: compile → validate → (skill only) publish
        manifest. ``self.validation`` holds the result so callers can
        surface issues without re-running the validator.

        Deck path: on validation error after a critique run, restore the
        pre-critique snapshot so the user never loses the clean main draft.
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
        await run_deck_create(
            kb_dir=self.kb_dir,
            deck_name=self.name,
            intent=self.intent,
            model=self.model,
            critique=self.critique,
        )
        self.validation = validate_deck(self.output_dir)

        if self.critique:
            if self.validation.errors:
                # Critique pass produced invalid HTML. Restore pre-critique
                # snapshot and re-validate so callers see the clean state.
                restore_pre_critique(self.output_dir)
                self.validation = validate_deck(self.output_dir)
                self.validation.warnings.append(
                    "critique pass failed; restored pre-critique draft."
                )
            else:
                # Critique succeeded — remove the snapshot so it doesn't
                # accumulate across runs.
                cleanup_pre_critique(self.output_dir)

        return self.output_dir
