"""Generator primitive — shared abstraction for all `<kb>/output/<type>/` artifacts.

v0.1 supports ``target_type="skill"`` only. Future targets (``"ppt"``,
``"podcast"``, ``"report"``, ``"video"``) will register here and reuse the
same:

* output-path convention: ``<kb>/output/<type>/<name>/``
* post-compile validation: structural check via :mod:`openkb.skill.validator`
* post-run hook: marketplace.json regeneration (so artifacts ride the same
  distribution mechanic as skills)

Each target plugs in its own `run` coroutine. v0.1's only entry calls into
``openkb.skill.creator.run_skill_create``.

Validation runs inside ``run`` so every entry point — CLI, chat slash
command, or future programmatic caller — gets the same quality gate.
Callers consume :attr:`Generator.validation` after ``run()`` returns to
format the issues for their output sink.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from openkb.skill import skill_dir
from openkb.skill.creator import run_skill_create
from openkb.skill.marketplace import regenerate_marketplace
from openkb.skill.validator import ValidationResult, validate_skill


TargetType = Literal["skill"]  # extend as new targets land


class Generator:
    """A v0.1 generator instance.

    Args:
        target_type: One of the supported targets. v0.1: ``"skill"``.
        name: kebab-case slug; becomes the output directory name.
        intent: natural-language description of the desired artifact.
        kb_dir: KB root.
        model: LiteLLM model name (from KB config).
    """

    def __init__(
        self,
        *,
        target_type: TargetType,
        name: str,
        intent: str,
        kb_dir: Path,
        model: str,
    ) -> None:
        if target_type != "skill":
            raise ValueError(
                f"Unknown target_type {target_type!r}. v0.1 supports only 'skill'."
            )
        self.target_type = target_type
        self.name = name
        self.intent = intent
        self.kb_dir = kb_dir
        self.model = model
        self.output_dir = skill_dir(kb_dir, name)
        self.validation: ValidationResult | None = None

    async def run(self) -> Path:
        """Execute the generator. Returns the path to the produced artifact.

        Side-effects, in order: compile → validate → publish manifest.
        ``self.validation`` holds the :class:`ValidationResult` so callers
        can surface issues without re-running the validator.
        """
        await run_skill_create(
            kb_dir=self.kb_dir,
            skill_name=self.name,
            intent=self.intent,
            model=self.model,
        )
        self.validation = validate_skill(self.output_dir)
        regenerate_marketplace(self.kb_dir)
        return self.output_dir
