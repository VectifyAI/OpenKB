"""Interactive multi-turn chat REPL for the OpenKB knowledge base.

Builds on the single-shot Q&A agent in ``openkb.agent.query`` and keeps
conversation state in ``ChatSession``. Uses prompt_toolkit for the input
line (history, editing, bottom toolbar) and streams responses directly to
stdout to preserve the existing ``query`` visual.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.styles import Style

from openkb.agent.chat_session import ChatSession
from openkb.agent.query import MAX_TURNS, build_query_agent
from openkb.log import append_log


_STYLE_DICT: dict[str, str] = {
    "prompt":           "bold ansicyan",
    "bottom-toolbar":   "noreverse nobold #8a8a8a bg:default",
    "toolbar":          "noreverse nobold #8a8a8a bg:default",
    "toolbar.session":  "noreverse #8a8a8a bg:default bold",
    "header":           "#8a8a8a",
    "header.title":     "bold #ffffff",
    "tool":             "#8a8a8a",
    "tool.name":        "#8a8a8a bold",
    "slash.ok":         "ansigreen",
    "slash.help":       "#8a8a8a",
    "error":            "ansired bold",
    "resume.turn":      "ansicyan",
    "resume.user":      "bold",
    "resume.assistant": "#8a8a8a",
}

_HELP_TEXT = (
    "Commands:\n"
    "  /exit          Exit (Ctrl-D also works)\n"
    "  /clear         Start a fresh session (current one is kept on disk)\n"
    "  /save [name]   Export transcript to wiki/explorations/\n"
    "  /help          Show this"
)


def _use_color(force_off: bool) -> bool:
    if force_off:
        return False
    if os.environ.get("NO_COLOR", ""):
        return False
    if not sys.stdout.isatty():
        return False
    return True


def _build_style(use_color: bool) -> Style:
    return Style.from_dict(_STYLE_DICT if use_color else {})


def _fmt(style: Style, *fragments: tuple[str, str]) -> None:
    print_formatted_text(FormattedText(list(fragments)), style=style, end="")


def _format_tool_line(name: str, args: str, width: int = 78) -> str:
    args = args or ""
    args = args.replace("\n", " ")
    base = f"  \u00b7 {name}({args})"
    if len(base) > width:
        base = base[: width - 1] + "\u2026"
    return base


def _extract_preview(text: str, limit: int = 150) -> str:
    text = " ".join((text or "").strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "\u2026"


def _openkb_version() -> str:
    try:
        from importlib.metadata import version
        return version("openkb")
    except Exception:
        try:
            from openkb import __version__
            return __version__
        except Exception:
            return ""


def _display_kb_dir(kb_dir: Path) -> str:
    home = str(Path.home())
    s = str(kb_dir)
    if s == home:
        return "~"
    if s.startswith(home + "/"):
        return "~" + s[len(home):]
    return s


def _print_header(session: ChatSession, kb_dir: Path, style: Style) -> None:
    disp_dir = _display_kb_dir(kb_dir)
    version = _openkb_version()
    version_suffix = f" v{version}\n" if version else "\n"
    print()
    _fmt(
        style,
        ("class:header.title", "OpenKB Chat"),
        ("class:header", version_suffix),
    )
    _fmt(
        style,
        (
            "class:header",
            f"{disp_dir} \u00b7 {session.model} \u00b7 session {session.id}\n",
        ),
    )
    _fmt(
        style,
        (
            "class:header",
            "Type /help for commands, Ctrl-D to exit, "
            "Ctrl-C to abort current response.\n",
        ),
    )
    print()


def _print_resume_view(session: ChatSession, style: Style) -> None:
    turns = list(zip(session.user_turns, session.assistant_texts))
    if not turns:
        return
    total = len(turns)
    if total > 5:
        omitted = total - 5
        _fmt(
            style,
            ("class:header", f"... {omitted} earlier turn(s) omitted\n"),
        )
        turns = turns[-5:]
        start = omitted + 1
    else:
        start = 1

    _fmt(
        style,
        ("class:header", f"Resumed session  {total} turn(s)\n"),
    )
    for i, (u, a) in enumerate(turns, start):
        _fmt(
            style,
            ("class:resume.turn", f"[{i}] "),
            ("class:resume.user", f">>> {u}\n"),
        )
        if a:
            preview = _extract_preview(a, 180)
            extra = ""
            if len(a) > len(preview):
                extra = f"  ({len(a)} chars)"
            _fmt(
                style,
                ("class:resume.turn", f"[{i}] "),
                ("class:resume.assistant", f"    {preview}{extra}\n"),
            )
    print()


def _bottom_toolbar(session: ChatSession) -> FormattedText:
    return FormattedText(
        [
            ("class:toolbar", " session "),
            ("class:toolbar.session", session.id),
            (
                "class:toolbar",
                f"  {session.turn_count} turn(s)  {session.model} ",
            ),
        ]
    )


def _make_prompt_session(session: ChatSession, style: Style, use_color: bool) -> PromptSession:
    return PromptSession(
        message=FormattedText([("class:prompt", ">>> ")]),
        style=style,
        bottom_toolbar=(lambda: _bottom_toolbar(session)) if use_color else None,
    )


async def _run_turn(agent: Any, session: ChatSession, user_input: str, style: Style) -> None:
    """Run one agent turn with streaming output and persist the new history."""
    from agents import (
        RawResponsesStreamEvent,
        RunItemStreamEvent,
        Runner,
    )
    from openai.types.responses import ResponseTextDeltaEvent

    new_input = session.history + [{"role": "user", "content": user_input}]

    result = Runner.run_streamed(agent, new_input, max_turns=MAX_TURNS)

    sys.stdout.write("\n")
    sys.stdout.flush()
    collected: list[str] = []
    last_was_text = False
    try:
        async for event in result.stream_events():
            if isinstance(event, RawResponsesStreamEvent):
                if isinstance(event.data, ResponseTextDeltaEvent):
                    text = event.data.delta
                    if text:
                        sys.stdout.write(text)
                        sys.stdout.flush()
                        collected.append(text)
                        last_was_text = True
            elif isinstance(event, RunItemStreamEvent):
                item = event.item
                if item.type == "tool_call_item":
                    if last_was_text:
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        last_was_text = False
                    raw = item.raw_item
                    name = getattr(raw, "name", "?")
                    args = getattr(raw, "arguments", "") or ""
                    _fmt(style, ("class:tool", _format_tool_line(name, args) + "\n"))
    finally:
        sys.stdout.write("\n\n")
        sys.stdout.flush()

    answer = "".join(collected).strip()
    if not answer:
        answer = (result.final_output or "").strip()
    session.record_turn(user_input, answer, result.to_input_list())


def _save_transcript(kb_dir: Path, session: ChatSession, name: str | None) -> Path:
    explore_dir = kb_dir / "wiki" / "explorations"
    explore_dir.mkdir(parents=True, exist_ok=True)

    base = name or session.title or (session.user_turns[0] if session.user_turns else session.id)
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:60] or session.id
    date = session.created_at[:10].replace("-", "")
    path = explore_dir / f"{slug}-{date}.md"

    lines: list[str] = [
        "---",
        f'session: "{session.id}"',
        f'model: "{session.model}"',
        f'created: "{session.created_at}"',
        "---",
        "",
        f"# Chat transcript  {session.title or session.id}",
        "",
    ]
    for i, (u, a) in enumerate(zip(session.user_turns, session.assistant_texts), 1):
        lines.append(f"## [{i}] {u}")
        lines.append("")
        lines.append(a or "_(no response recorded)_")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


async def _handle_slash(
    cmd: str,
    kb_dir: Path,
    session: ChatSession,
    style: Style,
) -> str | None:
    """Return ``"exit"`` to end the REPL, ``"new_session"`` to swap sessions,
    or ``None`` to continue with the current session."""
    parts = cmd.split(maxsplit=1)
    head = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if head in ("/exit", "/quit"):
        _fmt(style, ("class:header", "Bye.\n"))
        return "exit"

    if head == "/help":
        _fmt(style, ("class:slash.help", _HELP_TEXT + "\n"))
        return None

    if head == "/clear":
        old_id = session.id
        _fmt(
            style,
            ("class:slash.ok", f"Started new session (previous: {old_id})\n"),
        )
        return "new_session"

    if head == "/save":
        if not session.user_turns:
            _fmt(style, ("class:error", "Nothing to save yet.\n"))
            return None
        path = _save_transcript(kb_dir, session, arg or None)
        _fmt(style, ("class:slash.ok", f"Saved to {path}\n"))
        return None

    _fmt(
        style,
        ("class:error", f"Unknown command: {head}. Try /help.\n"),
    )
    return None


async def run_chat(
    kb_dir: Path,
    session: ChatSession,
    *,
    no_color: bool = False,
) -> None:
    """Run the chat REPL against ``session`` until the user exits."""
    from openkb.config import load_config

    use_color = _use_color(force_off=no_color)
    style = _build_style(use_color)

    config = load_config(kb_dir / ".openkb" / "config.yaml")
    language = session.language or config.get("language", "en")
    wiki_root = str(kb_dir / "wiki")
    agent = build_query_agent(wiki_root, session.model, language=language)

    _print_header(session, kb_dir, style)
    if session.turn_count > 0:
        _print_resume_view(session, style)

    prompt_session = _make_prompt_session(session, style, use_color)

    while True:
        try:
            user_input = await prompt_session.prompt_async()
        except KeyboardInterrupt:
            _fmt(style, ("class:header", "\nBye.\n"))
            return
        except EOFError:
            _fmt(style, ("class:header", "Bye.\n"))
            return

        user_input = (user_input or "").strip()
        if not user_input:
            continue

        if user_input.startswith("/"):
            action = await _handle_slash(user_input, kb_dir, session, style)
            if action == "exit":
                return
            if action == "new_session":
                session = ChatSession.new(kb_dir, session.model, session.language)
                agent = build_query_agent(wiki_root, session.model, language=language)
                prompt_session = _make_prompt_session(session, style, use_color)
            continue

        append_log(kb_dir / "wiki", "query", user_input)
        try:
            await _run_turn(agent, session, user_input, style)
        except KeyboardInterrupt:
            _fmt(style, ("class:error", "\n[aborted]\n"))
        except Exception as exc:
            _fmt(style, ("class:error", f"[ERROR] {exc}\n"))
