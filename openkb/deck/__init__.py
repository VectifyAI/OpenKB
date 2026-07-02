"""Deck target — HTML slide-deck generator. Second Generator target after skill.

This package mirrors openkb/skill/ structurally. Today it owns:
* path construction — ``deck_dir`` / ``decks_root`` / ``deck_workspace_dir``

A deck is a single self-contained ``index.html`` file at
``<kb>/output/decks/<name>/index.html``. Workspace iteration history lives
at ``<kb>/output/decks/<name>-workspace/iteration-N/``.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "decks_root",
    "deck_dir",
    "deck_workspace_dir",
]


def decks_root(kb_dir: Path) -> Path:
    """``<kb>/output/decks`` — the directory holding every compiled deck."""
    return kb_dir / "output" / "decks"


def deck_dir(kb_dir: Path, deck_name: str) -> Path:
    """``<kb>/output/decks/<name>`` — one compiled deck's home."""
    return decks_root(kb_dir) / deck_name


def deck_workspace_dir(kb_dir: Path, deck_name: str) -> Path:
    """``<kb>/output/decks/<name>-workspace`` — iteration history for a deck."""
    return decks_root(kb_dir) / f"{deck_name}-workspace"
