"""Interactive multi-turn chat REPL for the OpenKB knowledge base.

Builds on the single-shot Q&A agent in ``openkb.agent.query`` and keeps
conversation state in ``ChatSession``. Uses prompt_toolkit for the input
line (history, editing, bottom toolbar) and streams responses directly to
stdout to preserve the existing ``query`` visual.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.shortcuts import CompleteStyle, print_formatted_text
from prompt_toolkit.styles import Style

from openkb.agent.chat_session import ChatSession
from openkb.agent.query import MAX_TURNS, build_query_agent
from openkb.log import append_log


_STYLE_DICT: dict[str, str] = {
    "prompt":           "bold #5fa0e0",
    "bottom-toolbar":   "noreverse nobold #8a8a8a bg:default",
    "toolbar":          "noreverse nobold #8a8a8a bg:default",
    "toolbar.session":  "noreverse #8a8a8a bg:default bold",
    "header":           "#8a8a8a",
    "header.title":     "bold #5fa0e0",
    "tool":             "#a8a8a8",
    "tool.name":        "#a8a8a8 bold",
    "slash.ok":         "ansigreen",
    "slash.help":       "#8a8a8a",
    "error":            "ansired bold",
    "resume.turn":      "#5fa0e0",
    "resume.user":      "bold",
    "resume.assistant": "#8a8a8a",
    # Completion menu — lightweight, no heavy background
    "completion-menu":                    "bg:default #8a8a8a",
    "completion-menu.completion":         "bg:default #d0d0d0",
    "completion-menu.completion.current": "bg:#3a3a3a #ffffff bold",
    "completion-menu.meta.completion":         "bg:default #6a6a6a",
    "completion-menu.meta.completion.current": "bg:#3a3a3a #8a8a8a",
}

_HELP_TEXT = (
    "Commands:\n"
    "  /exit          Exit (Ctrl-D also works)\n"
    "  /clear         Start a fresh session (current one is kept on disk)\n"
    "  /save [name]   Export transcript to wiki/explorations/\n"
    "  /status        Show knowledge base status\n"
    "  /list          List all documents in the knowledge base\n"
    "  /lint          Lint the knowledge base\n"
    "  /add <path>    Add a document or directory to the knowledge base\n"
    "  /help          Show this"
)

_SIGINT_EXIT_WINDOW = 2.0


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
    from openkb import __version__
    return __version__


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


_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/exit",   "Exit (Ctrl-D also works)"),
    ("/quit",   "Exit (alias)"),
    ("/help",   "Show available commands"),
    ("/clear",  "Start a fresh session"),
    ("/save",   "Export transcript to wiki/explorations/"),
    ("/status", "Show knowledge base status"),
    ("/list",   "List all documents"),
    ("/lint",   "Lint the knowledge base"),
    ("/add",    "Add a document or directory"),
]


class _ChatCompleter(Completer):
    """Complete slash commands and file paths after /add."""

    def __init__(self) -> None:
        self._path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, complete_event: Any) -> Any:
        text = document.text_before_cursor

        # After "/add ", complete file paths (skip dotfiles)
        if text.lstrip().lower().startswith("/add "):
            path_text = text.lstrip()[5:]
            # Strip leading quote if user started one
            if path_text and path_text[0] in ("'", '"'):
                path_text = path_text[1:]
            path_doc = Document(path_text, len(path_text))
            for c in self._path_completer.get_completions(path_doc, complete_event):
                # Hide dotfiles unless the user explicitly typed a dot
                basename = c.text.lstrip("/")
                if basename.startswith(".") and not path_text.rpartition("/")[2].startswith("."):
                    continue
                yield c
            return

        # Complete slash commands with descriptions
        if text.startswith("/"):
            for cmd, desc in _SLASH_COMMANDS:
                if cmd.startswith(text.lower()):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)


def _make_prompt_session(session: ChatSession, style: Style, use_color: bool, kb_dir: Path) -> PromptSession:
    from prompt_toolkit.history import FileHistory

    history_path = kb_dir / ".openkb" / "chat_history"
    return PromptSession(
        message=FormattedText([("class:prompt", ">>> ")]),
        style=style,
        completer=_ChatCompleter(),
        complete_style=CompleteStyle.MULTI_COLUMN,
        history=FileHistory(str(history_path)),
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
    need_blank_before_text = False
    try:
        async for event in result.stream_events():
            if isinstance(event, RawResponsesStreamEvent):
                if isinstance(event.data, ResponseTextDeltaEvent):
                    text = event.data.delta
                    if text:
                        if need_blank_before_text:
                            sys.stdout.write("\n")
                            need_blank_before_text = False
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
                    need_blank_before_text = True
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


async def _run_add(arg: str, kb_dir: Path, style: Style) -> None:
    """Add a document or directory to the knowledge base from the chat REPL."""
    from openkb.cli import add_single_file, SUPPORTED_EXTENSIONS

    target = Path(arg).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target = target.resolve()

    if not target.exists():
        _fmt(style, ("class:error", f"Path does not exist: {arg}\n"))
        return

    if target.is_dir():
        files = [
            f for f in sorted(target.rglob("*"))
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        if not files:
            _fmt(style, ("class:error", f"No supported files found in {arg}.\n"))
            return
        total = len(files)
        _fmt(style, ("class:slash.help", f"Found {total} supported file(s) in {arg}.\n"))
        for i, f in enumerate(files, 1):
            _fmt(style, ("class:slash.help", f"\n[{i}/{total}] "))
            await asyncio.to_thread(add_single_file, f, kb_dir)
    else:
        if target.suffix.lower() not in SUPPORTED_EXTENSIONS:
            _fmt(style, ("class:error", f"Unsupported file type: {target.suffix}\n"))
            return
        await asyncio.to_thread(add_single_file, target, kb_dir)


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
    # Strip surrounding quotes (user may type /add '/path/to file')
    if len(arg) >= 2 and arg[0] == arg[-1] and arg[0] in ("'", '"'):
        arg = arg[1:-1]

    if head in ("/exit", "/quit"):
        _fmt(style, ("class:header", "Bye. Thanks for using OpenKB.\n\n"))
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

    if head == "/status":
        from openkb.cli import print_status
        print_status(kb_dir)
        return None

    if head == "/list":
        from openkb.cli import print_list
        print_list(kb_dir)
        return None

    if head == "/lint":
        from openkb.cli import run_lint
        await run_lint(kb_dir)
        return None

    if head == "/add":
        if not arg:
            _fmt(style, ("class:error", "Usage: /add <path>\n"))
            return None
        await _run_add(arg, kb_dir, style)
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

    prompt_session = _make_prompt_session(session, style, use_color, kb_dir)

    last_sigint = 0.0

    while True:
        try:
            user_input = await prompt_session.prompt_async()
            last_sigint = 0.0
        except KeyboardInterrupt:
            now = time.monotonic()
            if last_sigint and (now - last_sigint) < _SIGINT_EXIT_WINDOW:
                _fmt(style, ("class:header", "\nBye. Thanks for using OpenKB.\n\n"))
                return
            last_sigint = now
            _fmt(style, ("class:header", "\n(Press Ctrl-C again to exit)\n"))
            continue
        except EOFError:
            _fmt(style, ("class:header", "Bye. Thanks for using OpenKB.\n\n"))
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
                prompt_session = _make_prompt_session(session, style, use_color, kb_dir)
            continue

        append_log(kb_dir / "wiki", "query", user_input)
        try:
            await _run_turn(agent, session, user_input, style)
        except KeyboardInterrupt:
            _fmt(style, ("class:error", "\n[aborted]\n"))
        except Exception as exc:
            _fmt(style, ("class:error", f"[ERROR] {exc}\n"))
