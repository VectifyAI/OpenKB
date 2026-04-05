"""Tests for openkb.watcher (Task 12)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from openkb.watcher import DebouncedHandler


def _make_file_event(src_path: str, is_directory: bool = False):
    """Create a mock watchdog file event."""
    event = MagicMock()
    event.src_path = src_path
    event.is_directory = is_directory
    return event


class TestDebouncedHandler:
    def test_collects_created_files(self):
        callback = MagicMock()
        handler = DebouncedHandler(callback, debounce_seconds=100)

        handler.on_created(_make_file_event("/raw/doc.pdf"))
        handler.on_created(_make_file_event("/raw/notes.md"))

        # Cancel pending timer; check pending set
        if handler._timer:
            handler._timer.cancel()

        assert "/raw/doc.pdf" in handler._pending
        assert "/raw/notes.md" in handler._pending

    def test_collects_modified_files(self):
        callback = MagicMock()
        handler = DebouncedHandler(callback, debounce_seconds=100)

        handler.on_modified(_make_file_event("/raw/paper.txt"))

        if handler._timer:
            handler._timer.cancel()

        assert "/raw/paper.txt" in handler._pending

    def test_ignores_directories(self):
        callback = MagicMock()
        handler = DebouncedHandler(callback, debounce_seconds=100)

        handler.on_created(_make_file_event("/raw/subdir", is_directory=True))

        if handler._timer:
            handler._timer.cancel()

        assert len(handler._pending) == 0

    def test_ignores_hidden_files(self):
        callback = MagicMock()
        handler = DebouncedHandler(callback, debounce_seconds=100)

        handler.on_created(_make_file_event("/raw/.hidden_file"))
        handler.on_created(_make_file_event("/raw/.DS_Store"))

        if handler._timer:
            handler._timer.cancel()

        assert len(handler._pending) == 0

    def test_flush_calls_callback_with_sorted_paths(self):
        received = []

        def callback(paths):
            received.extend(paths)

        handler = DebouncedHandler(callback, debounce_seconds=100)
        handler._pending = {"/raw/b.pdf", "/raw/a.md", "/raw/c.txt"}

        handler._flush()

        assert received == ["/raw/a.md", "/raw/b.pdf", "/raw/c.txt"]

    def test_flush_clears_pending(self):
        callback = MagicMock()
        handler = DebouncedHandler(callback, debounce_seconds=100)
        handler._pending = {"/raw/doc.pdf"}

        handler._flush()

        assert len(handler._pending) == 0

    def test_flush_does_not_call_callback_when_empty(self):
        callback = MagicMock()
        handler = DebouncedHandler(callback, debounce_seconds=100)

        handler._flush()

        callback.assert_not_called()

    def test_debounce_resets_timer_on_new_event(self):
        callback = MagicMock()
        handler = DebouncedHandler(callback, debounce_seconds=100)

        handler.on_created(_make_file_event("/raw/a.pdf"))
        first_timer = handler._timer

        handler.on_created(_make_file_event("/raw/b.pdf"))
        second_timer = handler._timer

        # Timer should have been replaced
        assert first_timer is not second_timer

        if handler._timer:
            handler._timer.cancel()

    def test_mixed_events_collected(self):
        callback = MagicMock()
        handler = DebouncedHandler(callback, debounce_seconds=100)

        handler.on_created(_make_file_event("/raw/new.pdf"))
        handler.on_modified(_make_file_event("/raw/existing.md"))
        handler.on_created(_make_file_event("/raw/.hidden"))  # should be ignored
        handler.on_created(_make_file_event("/raw/subdir", is_directory=True))  # ignored

        if handler._timer:
            handler._timer.cancel()

        assert handler._pending == {"/raw/new.pdf", "/raw/existing.md"}
