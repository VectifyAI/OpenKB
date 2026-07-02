"""Static prompt templates loaded from disk.

Keeping multi-paragraph LLM system prompts in `.md` files (rather than triple-
quoted Python strings) makes them readable in editors with markdown previews
and easier to diff/review.
"""

from __future__ import annotations

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def load_prompt(name: str) -> str:
    """Return the contents of ``openkb/prompts/<name>.md`` as a string.

    Args:
        name: Filename without the ``.md`` suffix (e.g. ``"skill_create"``).
    """
    path = _PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
