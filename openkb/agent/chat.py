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
from openkb.agent.query import MAX_TURNS, build_chat_agent
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
    '  /skill new <name> "<intent>"   Compile a skill from the wiki\n'
    '  /deck new [--critique] [--skill <name>] <name> "<intent>"   Generate an HTML deck from the wiki\n'
    "  /critique <path-to-html>   Run html-critic skill on a file (CSS bugs, layout, self-containment)\n"
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
    # prompt_toolkit's print_formatted_text constructs a Win32Output on
    # Windows that requires a real console handle, raising
    # NoConsoleScreenBufferError when stdout is a pipe, file, or captured
    # subprocess stream. Fall back to plain text when the output isn't a
    # usable console.
    if not _use_color(force_off=False):
        for _, text in fragments:
            sys.stdout.write(text)
        sys.stdout.flush()
        return
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
    ("/skill",  "Compile a skill (try `/skill new <name> \"intent\"`)"),
    ("/deck",   "Generate a deck (try `/deck new <name> \"intent\"`)"),
    ("/critique", "Run html-critic skill on a file (e.g. `/critique output/decks/foo/index.html`)"),
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
            # Strip leading quote so PathCompleter resolves the real path
            quote_char = ""
            if path_text and path_text[0] in ("'", '"'):
                quote_char = path_text[0]
                path_text = path_text[1:]
            path_doc = Document(path_text, len(path_text))
            for c in self._path_completer.get_completions(path_doc, complete_event):
                # Hide dotfiles unless the user explicitly typed a dot
                basename = c.text.lstrip("/")
                if basename.startswith(".") and not path_text.rpartition("/")[2].startswith("."):
                    continue
                # Append closing quote for files; skip for directories so
                # the user can keep navigating into subdirectories.
                if quote_char and not c.text.endswith("/"):
                    comp_text = c.text + quote_char
                else:
                    comp_text = c.text
                yield Completion(
                    comp_text,
                    start_position=c.start_position,
                    display=c.display,
                    display_meta=c.display_meta,
                )
            return

        # Complete slash commands with descriptions
        if text.startswith("/"):
            for cmd, desc in _SLASH_COMMANDS:
                if cmd.startswith(text.lower()):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)


def _make_prompt_session(session: ChatSession, style: Style, use_color: bool, kb_dir: Path) -> PromptSession:
    from prompt_toolkit.filters import has_completions
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings

    kb = KeyBindings()

    @kb.add("tab", filter=has_completions)
    def _accept_completion(event: Any) -> None:
        """Tab accepts the current completion (like zsh), not cycle."""
        buf = event.current_buffer
        state = buf.complete_state
        if not state:
            return
        # Only one candidate or already selected — accept immediately
        if state.current_completion:
            buf.apply_completion(state.current_completion)
        elif len(state.completions) == 1:
            buf.apply_completion(state.completions[0])
        else:
            # Multiple candidates, nothing selected — highlight first
            buf.go_to_completion(0)

    @kb.add("tab", filter=~has_completions)
    def _trigger_completion(event: Any) -> None:
        """Tab triggers completion when menu is not open."""
        buf = event.current_buffer
        buf.start_completion()

    history_path = kb_dir / ".openkb" / "chat_history"
    return PromptSession(
        message=FormattedText([("class:prompt", ">>> ")]),
        style=style,
        completer=_ChatCompleter(),
        complete_style=CompleteStyle.MULTI_COLUMN,
        complete_while_typing=False,
        key_bindings=kb,
        history=FileHistory(str(history_path)),
        bottom_toolbar=(lambda: _bottom_toolbar(session)) if use_color else None,
    )


def _make_rich_console() -> Any:
    from rich.console import Console

    return Console()


def _make_markdown(text: str) -> Any:
    from openkb.agent._markdown import render

    return render(text)


async def _run_turn(
    agent: Any, session: ChatSession, user_input: str, style: Style,
    *, use_color: bool = True, raw: bool = False,
) -> None:
    """Run one agent turn with streaming output and persist the new history."""
    from agents import (
        RawResponsesStreamEvent,
        RunItemStreamEvent,
        Runner,
    )
    from openai.types.responses import ResponseTextDeltaEvent

    new_input = session.history + [{"role": "user", "content": user_input}]

    result = Runner.run_streamed(agent, new_input, max_turns=MAX_TURNS)

    print()
    collected: list[str] = []
    segment: list[str] = []
    last_was_text = False
    need_blank_before_text = False

    if use_color and not raw:
        from rich.live import Live

        console = _make_rich_console()
    else:
        console = None  # type: ignore[assignment]

    def _start_live() -> Any:
        if console is None:
            return None
        lv = Live(console=console, vertical_overflow="visible")
        lv.start()
        return lv

    live = _start_live()

    try:
        async for event in result.stream_events():
            if isinstance(event, RawResponsesStreamEvent):
                if isinstance(event.data, ResponseTextDeltaEvent):
                    text = event.data.delta
                    if text:
                        if need_blank_before_text:
                            if console is not None:
                                print()
                                segment = []
                                live = _start_live()
                            else:
                                sys.stdout.write("\n")
                            need_blank_before_text = False
                        collected.append(text)
                        segment.append(text)
                        last_was_text = True
                        if live:
                            if "\n" in text:
                                joined = "".join(segment)
                                visible = joined[: joined.rfind("\n") + 1]
                                if visible:
                                    live.update(_make_markdown(visible))
                        else:
                            sys.stdout.write(text)
                            sys.stdout.flush()
            elif isinstance(event, RunItemStreamEvent):
                item = event.item
                if item.type == "tool_call_item":
                    if last_was_text:
                        if live:
                            if segment:
                                live.update(_make_markdown("".join(segment)))
                            live.stop()
                            live = None
                        else:
                            sys.stdout.write("\n")
                            sys.stdout.flush()
                        last_was_text = False
                    raw_item = item.raw_item
                    name = getattr(raw_item, "name", "?")
                    args = getattr(raw_item, "arguments", "") or ""
                    if live:
                        live.stop()
                        live = None
                    _fmt(style, ("class:tool", _format_tool_line(name, args) + "\n"))
                    need_blank_before_text = True
    finally:
        if live:
            if segment:
                live.update(_make_markdown("".join(segment)))
            live.stop()
        print()

    answer = "".join(collected).strip()
    if not answer:
        answer = (result.final_output or "").strip()
    session.record_turn(user_input, answer, result.to_input_list())


def _save_transcript(kb_dir: Path, session: ChatSession, name: str | None) -> Path:
    from openkb.lint import (
        build_norm_index,
        list_existing_wiki_targets,
        strip_ghost_wikilinks,
    )

    explore_dir = kb_dir / "wiki" / "explorations"
    explore_dir.mkdir(parents=True, exist_ok=True)

    base = name or session.title or (session.user_turns[0] if session.user_turns else session.id)
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:60] or session.id
    date = session.created_at[:10].replace("-", "")
    path = explore_dir / f"{slug}-{date}.md"

    # Strip ghost wikilinks from assistant responses (the agent's
    # instructions encourage [[wikilinks]] but it can reference pages
    # that don't exist on disk). User turns are written verbatim — they
    # represent intentional user input, not LLM hallucination.
    # Build the normalized index once and reuse for every turn — the
    # whitelist is the same across the whole session.
    known = list_existing_wiki_targets(kb_dir / "wiki")
    norm_index = build_norm_index(known)

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
        if a:
            cleaned_a, _ = strip_ghost_wikilinks(a, known, norm_index=norm_index)
            lines.append(cleaned_a)
        else:
            lines.append("_(no response recorded)_")
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


async def _handle_slash_skill(arg: str, kb_dir: Path, style: Style) -> None:
    """Dispatch ``/skill new <name> "<intent>"`` and any future skill subcommands."""
    import shlex

    try:
        parts = shlex.split(arg) if arg else []
    except ValueError as exc:
        _fmt(style, ("class:error", f"[ERROR] Could not parse: {exc}\n"))
        return
    if not parts:
        _fmt(style, ("class:error", "Usage: /skill new <name> \"<intent>\"\n"))
        return

    sub = parts[0].lower()
    if sub != "new":
        _fmt(style, ("class:error", f"Unknown skill subcommand: {sub}. Try /skill new.\n"))
        return

    if len(parts) < 3:
        _fmt(style, ("class:error", "Usage: /skill new <name> \"<intent>\"\n"))
        return

    name = parts[1]
    intent = " ".join(parts[2:])

    # Use the same safety gates as the CLI (name validation, wiki dir,
    # wiki content). Chat doesn't have a -y flag, so existing skills
    # block with a clear instruction to delete first.
    from openkb.cli import _preflight_skill_new
    err = _preflight_skill_new(kb_dir, name)
    if err:
        _fmt(style, ("class:error", f"[ERROR] {err}\n"))
        return

    from openkb.skill import skill_dir
    target = skill_dir(kb_dir, name)
    if target.exists():
        _fmt(style, ("class:error",
            f"[ERROR] output/skills/{name}/ already exists. Remove it first "
            f"with `rm -rf output/skills/{name}` and re-run.\n"))
        return

    # Load model from KB config
    from openkb.config import load_config, DEFAULT_CONFIG
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    model = config.get("model", DEFAULT_CONFIG["model"])

    from openkb.skill.generator import Generator
    _fmt(style, ("class:slash.help", f"Compiling skill '{name}'...\n"))
    gen = Generator(
        target_type="skill",
        name=name,
        intent=intent,
        kb_dir=kb_dir,
        model=model,
    )
    try:
        await gen.run()
    except RuntimeError as exc:
        _fmt(style, ("class:error", f"[ERROR] {exc}\n"))
        return

    # Surface validation issues from Generator.run. Unlike the CLI
    # (which exits 1 on validation errors), chat is interactive — print
    # issues inline and continue so the user can inspect and iterate.
    result = gen.validation
    if result is not None and (result.errors or result.warnings):
        _fmt(style, ("class:error", "[WARN] Validation found issues:\n"))
        for err in result.errors:
            _fmt(style, ("class:error", f"  ERROR:   {err}\n"))
        for warn in result.warnings:
            _fmt(style, ("class:error", f"  WARN:    {warn}\n"))
        _fmt(style, ("class:slash.help",
            f"Run `openkb skill validate {name}` to re-check, or "
            f"`openkb skill rollback {name}` to revert.\n"))

    _fmt(style, ("class:slash.ok", f"Saved: output/skills/{name}/\n"))
    _fmt(style, ("class:slash.help",
        f"Iterate: ask follow-up questions in this chat and the agent can "
        f"edit files under output/skills/{name}/ directly.\n"))


async def _handle_slash_deck(arg: str, kb_dir: Path, style: Style) -> None:
    """Dispatch ``/deck new [--critique] <name> "<intent>"``.

    Mirrors :func:`_handle_slash_skill`: validates the name, runs the
    same wiki preflight gate, refuses to overwrite an existing deck
    (chat has no ``-y`` flag), then invokes ``Generator(target_type="deck")``.
    """
    import shlex

    try:
        parts = shlex.split(arg) if arg else []
    except ValueError as exc:
        _fmt(style, ("class:error", f"[ERROR] Could not parse: {exc}\n"))
        return
    if not parts:
        _fmt(style, ("class:error",
            "Usage: /deck new [--critique] <name> \"<intent>\"\n"))
        return

    sub = parts[0].lower()
    if sub != "new":
        _fmt(style, ("class:error", f"Unknown deck subcommand: {sub}. Try /deck new.\n"))
        return

    # Parse optional --critique flag and --skill <name> option. Both can
    # appear anywhere among the remaining tokens.
    rest = parts[1:]
    critique = False
    skill_name: str | None = None
    filtered: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok == "--critique":
            critique = True
        elif tok == "--skill" and i + 1 < len(rest):
            skill_name = rest[i + 1]
            i += 1
        elif tok.startswith("--skill="):
            skill_name = tok.split("=", 1)[1]
        else:
            filtered.append(tok)
        i += 1

    if len(filtered) < 2:
        _fmt(style, ("class:error",
            "Usage: /deck new [--critique] [--skill <skill>] <name> \"<intent>\"\n"))
        return

    name = filtered[0]
    intent = " ".join(filtered[1:])

    # Reuse the shared safety gates from the CLI (name validation,
    # wiki dir, wiki content). Chat has no -y flag, so existing decks
    # block with a clear instruction to delete first.
    from openkb.cli import _preflight_skill_new
    err = _preflight_skill_new(kb_dir, name)
    if err:
        # Reword "Skill name" → "Deck name" so error matches the command.
        err = err.replace("Skill name", "Deck name")
        _fmt(style, ("class:error", f"[ERROR] {err}\n"))
        return

    from openkb.deck import deck_dir
    target = deck_dir(kb_dir, name)
    if target.exists():
        _fmt(style, ("class:error",
            f"[ERROR] output/decks/{name}/ already exists. Remove it first "
            f"with `rm -rf output/decks/{name}` and re-run.\n"))
        return

    # Load model from KB config
    from openkb.config import load_config, DEFAULT_CONFIG
    config = load_config(kb_dir / ".openkb" / "config.yaml")
    model = config.get("model", DEFAULT_CONFIG["model"])

    from openkb.skill.generator import Generator
    skill_label = skill_name if skill_name else "openkb-deck-editorial (default)"
    _fmt(
        style,
        ("class:slash.help", f"Generating deck '{name}' via skill {skill_label}...\n"),
    )
    gen = Generator(
        target_type="deck",
        name=name,
        intent=intent,
        kb_dir=kb_dir,
        model=model,
        critique=critique,
        skill_name=skill_name,
    )
    try:
        await gen.run()
    except RuntimeError as exc:
        _fmt(style, ("class:error", f"[ERROR] {exc}\n"))
        return

    # Surface validation issues from Generator.run. Unlike the CLI
    # (which exits 1 on validation errors), chat is interactive — print
    # issues inline and continue so the user can inspect and iterate.
    result = gen.validation
    if result is not None and (result.errors or result.warnings):
        _fmt(style, ("class:error", "[WARN] Validation found issues:\n"))
        for err in result.errors:
            _fmt(style, ("class:error", f"  ERROR:   {err}\n"))
        for warn in result.warnings:
            _fmt(style, ("class:error", f"  WARN:    {warn}\n"))

    _fmt(style, ("class:slash.ok", f"Saved: output/decks/{name}/index.html\n"))
    _fmt(style, ("class:slash.help",
        f"Iterate: ask follow-up questions in this chat and the agent can "
        f"edit files under output/decks/{name}/ directly.\n"))


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
    elif arg and arg[0] in ("'", '"'):
        arg = arg[1:]

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

    if head == "/skill":
        await _handle_slash_skill(arg, kb_dir, style)
        return None

    if head == "/deck":
        await _handle_slash_deck(arg, kb_dir, style)
        return None

    if head == "/critique":
        await _handle_slash_critique(arg, kb_dir, style)
        return None

    _fmt(
        style,
        ("class:error", f"Unknown command: {head}. Try /help.\n"),
    )
    return None


async def _handle_slash_critique(arg: str, kb_dir: Path, style: Style) -> None:
    """``/critique <path>`` — run the openkb-html-critic skill on a file.

    The skill reads the HTML, fixes CSS specificity bugs / missing nav /
    self-containment violations, and writes the corrected file back. It
    will not touch slide content (numbers, names, quotes).
    """
    path = arg.strip()
    if not path:
        _fmt(
            style,
            ("class:slash.help", "Usage: /critique <path-to-html>\n"),
        )
        return

    target = (kb_dir / path).resolve() if not Path(path).is_absolute() else Path(path)
    if not target.is_file():
        _fmt(style, ("class:error", f"[ERROR] File not found: {path}\n"))
        return

    from openkb.agent.skill_runner import (
        MAX_TURNS_WITH_CRITIQUE,
        SkillNotFoundError,
        run_skill,
    )
    from openkb.config import DEFAULT_CONFIG, load_config

    config = load_config(kb_dir / ".openkb" / "config.yaml")
    model = config.get("model", DEFAULT_CONFIG["model"])

    # Path passed to the skill is relative to kb_dir (the agent's cwd
    # conceptually). The skill's read_file/write_file tools operate
    # under wiki/ and output/ scopes — give it the relative form so
    # write_kb_file resolves correctly.
    try:
        rel = target.relative_to(kb_dir)
        rel_str = str(rel)
    except ValueError:
        # Outside KB — pass absolute, write tool will reject, but read
        # may still work for a critique-only diagnostic.
        rel_str = str(target)

    _fmt(style, ("class:slash.ok", f"Critiquing {rel_str}...\n"))

    try:
        await run_skill(
            skill_name="openkb-html-critic",
            intent=f"Critique and patch the HTML file at: {rel_str}",
            kb_dir=kb_dir,
            model=model,
            max_turns=40,
        )
    except SkillNotFoundError as exc:
        _fmt(style, ("class:error", f"[ERROR] {exc}\n"))
        return
    except RuntimeError as exc:
        _fmt(style, ("class:error", f"[ERROR] {exc}\n"))
        return

    _fmt(style, ("class:slash.ok", f"Critique pass complete: {rel_str}\n"))


async def run_chat(
    kb_dir: Path,
    session: ChatSession,
    *,
    no_color: bool = False,
    raw: bool = False,
) -> None:
    """Run the chat REPL against ``session`` until the user exits."""
    from openkb.config import load_config

    use_color = _use_color(force_off=no_color)
    style = _build_style(use_color)

    config = load_config(kb_dir / ".openkb" / "config.yaml")
    language = session.language or config.get("language", "en")
    agent = build_chat_agent(kb_dir, session.model, language=language)

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
            try:
                action = await _handle_slash(user_input, kb_dir, session, style)
            except KeyboardInterrupt:
                _fmt(style, ("class:error", "\n[aborted]\n"))
                continue
            if action == "exit":
                return
            if action == "new_session":
                session = ChatSession.new(kb_dir, session.model, session.language)
                agent = build_chat_agent(kb_dir, session.model, language=language)
                prompt_session = _make_prompt_session(session, style, use_color, kb_dir)
            continue

        append_log(kb_dir / "wiki", "query", user_input)
        try:
            await _run_turn(agent, session, user_input, style, use_color=use_color, raw=raw)
        except KeyboardInterrupt:
            _fmt(style, ("class:error", "\n[aborted]\n"))
        except Exception as exc:
            _fmt(style, ("class:error", f"[ERROR] {exc}\n"))
