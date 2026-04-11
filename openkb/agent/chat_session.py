"""Chat session persistence for `openkb chat`.

Each session lives in ``<kb>/.openkb/chats/<id>.json`` and stores the full
agent-SDK history (from ``RunResult.to_input_list()``) alongside the user
messages and full assistant replies kept as plain strings for display and
export.
"""
from __future__ import annotations

import json
import os
import random
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id() -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=3))
    return f"{ts}-{rand}"


def chats_dir(kb_dir: Path) -> Path:
    return kb_dir / ".openkb" / "chats"


def _title_from(msg: str, limit: int = 60) -> str:
    msg = " ".join(msg.strip().split())
    if len(msg) <= limit:
        return msg
    return msg[: limit - 1] + "\u2026"


@dataclass
class ChatSession:
    id: str
    created_at: str
    updated_at: str
    model: str
    language: str
    title: str
    turn_count: int
    history: list[dict[str, Any]]
    user_turns: list[str]
    assistant_texts: list[str]
    path: Path

    @classmethod
    def new(cls, kb_dir: Path, model: str, language: str) -> "ChatSession":
        now = _utcnow_iso()
        sid = _gen_id()
        return cls(
            id=sid,
            created_at=now,
            updated_at=now,
            model=model,
            language=language,
            title="",
            turn_count=0,
            history=[],
            user_turns=[],
            assistant_texts=[],
            path=chats_dir(kb_dir) / f"{sid}.json",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "language": self.language,
            "title": self.title,
            "turn_count": self.turn_count,
            "history": self.history,
            "user_turns": self.user_turns,
            "assistant_texts": self.assistant_texts,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def record_turn(
        self,
        user_message: str,
        assistant_text: str,
        new_history: list[dict[str, Any]],
    ) -> None:
        self.history = new_history
        self.user_turns.append(user_message)
        self.assistant_texts.append(assistant_text)
        self.turn_count = len(self.user_turns)
        if not self.title:
            self.title = _title_from(user_message)
        self.updated_at = _utcnow_iso()
        self.save()


def load_session(kb_dir: Path, session_id: str) -> ChatSession:
    path = chats_dir(kb_dir) / f"{session_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return ChatSession(
        id=data["id"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        model=data["model"],
        language=data.get("language", "en"),
        title=data.get("title", ""),
        turn_count=data.get("turn_count", 0),
        history=data.get("history", []),
        user_turns=data.get("user_turns", []),
        assistant_texts=data.get("assistant_texts", []),
        path=path,
    )


def list_sessions(kb_dir: Path) -> list[dict[str, Any]]:
    """Return session metadata dicts, newest first (by id, which is timestamp-prefixed)."""
    d = chats_dir(kb_dir)
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.json"), key=lambda x: x.name, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(
            {
                "id": data.get("id", p.stem),
                "title": data.get("title", ""),
                "turn_count": data.get("turn_count", 0),
                "updated_at": data.get("updated_at", ""),
                "model": data.get("model", ""),
            }
        )
    return out


def resolve_session_id(kb_dir: Path, query: str) -> str | None:
    """Resolve a query to a full session id.

    ``query`` may be:
    - ``"__latest__"`` — returns the most recently updated session id.
    - A full session id — returned as-is if it exists.
    - A unique prefix of a session id — expanded to the full id.

    Returns ``None`` if no session matches. Raises ``ValueError`` when a
    prefix is ambiguous.
    """
    sessions = list_sessions(kb_dir)
    if not sessions:
        return None
    if query == "__latest__":
        return sessions[0]["id"]
    for s in sessions:
        if s["id"] == query:
            return s["id"]
    matches = [s["id"] for s in sessions if s["id"].startswith(query)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous session prefix '{query}' matches: {', '.join(matches)}"
        )
    return None


def delete_session(kb_dir: Path, session_id: str) -> bool:
    path = chats_dir(kb_dir) / f"{session_id}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def relative_time(iso_str: str) -> str:
    """Render an ISO-8601 timestamp as a short relative string."""
    try:
        t = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return iso_str or ""
    now = datetime.now(timezone.utc)
    seconds = int((now - t).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 86400 * 7:
        return f"{seconds // 86400}d ago"
    return t.strftime("%Y-%m-%d")
