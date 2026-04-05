"""Append-only operation log for the wiki (log.md)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def append_log(wiki_dir: Path, operation: str, description: str) -> None:
    """Append an entry to wiki/log.md.

    Format: ``## [YYYY-MM-DD HH:MM:SS] operation | description``
    """
    log_path = wiki_dir / "log.md"
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"## [{date_str}] {operation} | {description}\n\n"

    if not log_path.exists():
        log_path.write_text("# Operations Log\n\n" + entry, encoding="utf-8")
    else:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(entry)
