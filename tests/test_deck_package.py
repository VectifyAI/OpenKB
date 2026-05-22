"""Sanity test for deck package path helpers — mirrors test_skill_factory expectations."""
from __future__ import annotations

from pathlib import Path

from openkb.deck import deck_dir, deck_workspace_dir, decks_root


def test_decks_root(tmp_path: Path):
    assert decks_root(tmp_path) == tmp_path / "output" / "decks"


def test_deck_dir(tmp_path: Path):
    assert deck_dir(tmp_path, "transformers") == tmp_path / "output" / "decks" / "transformers"


def test_deck_workspace_dir(tmp_path: Path):
    assert deck_workspace_dir(tmp_path, "transformers") == tmp_path / "output" / "decks" / "transformers-workspace"
