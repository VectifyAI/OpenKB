"""Tests for deck-scoped IO tools. Mirrors tests/test_skill_tools.py for write_skill_file."""
from __future__ import annotations

from pathlib import Path

from openkb.deck.tools import read_deck_file, write_deck_file


def test_write_then_read_roundtrip(tmp_path: Path):
    deck_root = tmp_path / "deck"
    msg = write_deck_file("index.html", "<html><body>hi</body></html>", str(deck_root))
    assert msg.startswith("Written:")
    content = read_deck_file("index.html", str(deck_root))
    assert "<body>hi</body>" in content


def test_write_creates_parent_dirs(tmp_path: Path):
    deck_root = tmp_path / "deck"
    write_deck_file("nested/sub/file.txt", "ok", str(deck_root))
    assert (deck_root / "nested" / "sub" / "file.txt").read_text() == "ok"


def test_write_rejects_absolute_path(tmp_path: Path):
    msg = write_deck_file("/etc/passwd", "pwn", str(tmp_path / "deck"))
    assert "Access denied" in msg
    assert not Path("/etc/passwd_test_marker").exists()  # sanity


def test_write_rejects_parent_traversal(tmp_path: Path):
    deck_root = tmp_path / "deck"
    msg = write_deck_file("../escape.txt", "pwn", str(deck_root))
    assert "Access denied" in msg
    assert not (tmp_path / "escape.txt").exists()


def test_read_rejects_escape(tmp_path: Path):
    deck_root = tmp_path / "deck"
    deck_root.mkdir()
    msg = read_deck_file("../../etc/passwd", str(deck_root))
    assert "Access denied" in msg


def test_read_missing_file(tmp_path: Path):
    deck_root = tmp_path / "deck"
    deck_root.mkdir()
    msg = read_deck_file("index.html", str(deck_root))
    assert "not found" in msg.lower()
