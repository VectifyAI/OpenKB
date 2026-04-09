"""File-system watcher for the OpenKB raw/ directory.

Watches for new or modified files and debounces rapid bursts of events
before calling the user's callback with a sorted list of affected paths.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


class DebouncedHandler(FileSystemEventHandler):
    """Debounced file-system event handler.

    Collects file creation/modification events and waits *debounce_seconds*
    after the last event before calling *callback* with all pending paths.
    Directories and dotfiles (hidden files) are ignored.

    Args:
        callback: Called with a sorted list of path strings when the debounce
            timer fires.
        debounce_seconds: How long to wait after the last event before
            flushing. Defaults to 2.0 seconds.
    """

    def __init__(self, callback: Callable[[list[str]], None], debounce_seconds: float = 2.0) -> None:
        super().__init__()
        self._callback = callback
        self._debounce_seconds = debounce_seconds
        self._pending: set[str] = set()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def _schedule_flush(self) -> None:
        """Cancel any existing timer and start a fresh debounce timer."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce_seconds, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        """Call the callback with all collected pending paths, then clear."""
        with self._lock:
            paths = sorted(self._pending)
            self._pending.clear()
            self._timer = None
        if paths:
            self._callback(paths)

    def _handle_event(self, event) -> None:
        """Add the event's source path to pending if it's a supported file."""
        if event.is_directory:
            return
        path = Path(event.src_path)
        # Ignore hidden/dotfiles
        if path.name.startswith("."):
            return
        with self._lock:
            self._pending.add(str(path))
        self._schedule_flush()

    def on_created(self, event) -> None:
        """Handle file creation events."""
        self._handle_event(event)

    def on_modified(self, event) -> None:
        """Handle file modification events."""
        self._handle_event(event)


def watch_directory(
    raw_dir: Path,
    callback: Callable[[list[str]], None],
    debounce: float = 2.0,
) -> None:
    """Start watching *raw_dir* and block until Ctrl+C.

    Args:
        raw_dir: Directory to watch for file changes.
        callback: Called with sorted list of new/modified file paths.
        debounce: Debounce delay in seconds. Defaults to 2.0.
    """
    handler = DebouncedHandler(callback, debounce_seconds=debounce)
    observer = Observer()
    observer.schedule(handler, str(raw_dir), recursive=True)
    observer.start()
    try:
        while observer.is_alive():
            observer.join(timeout=1.0)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
