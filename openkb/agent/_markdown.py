"""Markdown rendering in Claude Code's terminal style.

Mirrors claude-code's utils/markdown.ts: parse with markdown-it, then map
each token to Rich primitives. No colors for plain text / bold / italic --
just terminal styling. Headings are left-aligned.
"""

from __future__ import annotations

from typing import Any

from markdown_it import MarkdownIt
from markdown_it.tree import SyntaxTreeNode
from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.text import Text


INLINE_CODE_STYLE = "blue"
BLOCKQUOTE_BAR = "\u258e"


_MD = MarkdownIt("commonmark").enable("table")


def render(content: str) -> RenderableType:
    tokens = _MD.parse(content)
    tree = SyntaxTreeNode(tokens)

    blocks: list[RenderableType] = []
    for child in tree.children:
        rendered = _render_block(child)
        if rendered is not None:
            blocks.append(rendered)

    if not blocks:
        return Text("")
    parts: list[RenderableType] = [blocks[0]]
    for block in blocks[1:]:
        parts.append(Text(""))
        parts.append(block)
    return Group(*parts)


def _render_block(node: Any) -> RenderableType | None:
    t = node.type
    if t == "heading":
        depth = int(node.tag[1:])
        text = _render_inline_container(node)
        if depth == 1:
            text.stylize("bold italic underline")
        else:
            text.stylize("bold")
        return text
    if t == "paragraph":
        return _render_inline_container(node)
    if t == "fence":
        info_parts = (node.info or "").strip().split()
        lang = info_parts[0] if info_parts else ""
        return Syntax(
            node.content.rstrip("\n"),
            lang or "text",
            theme="monokai",
            background_color="default",
            word_wrap=True,
        )
    if t == "code_block":
        return Syntax(
            node.content.rstrip("\n"),
            "text",
            theme="monokai",
            background_color="default",
            word_wrap=True,
        )
    if t == "hr":
        return Text("---")
    if t in ("bullet_list", "ordered_list"):
        return _render_list(node, ordered=(t == "ordered_list"), depth=0)
    if t == "blockquote":
        return _render_blockquote(node)
    if t == "table":
        return _render_table(node)
    if t == "html_block":
        return Text(node.content.rstrip("\n"))
    return None


def _render_inline_container(node: Any) -> Text:
    if not node.children:
        return Text("")
    inline = node.children[0]
    out = Text()
    for child in inline.children or []:
        _append_inline(child, out)
    return out


def _append_inline(node: Any, out: Text) -> None:
    t = node.type
    if t == "text":
        out.append(node.content)
    elif t == "softbreak":
        out.append("\n")
    elif t == "hardbreak":
        out.append("\n")
    elif t == "strong":
        piece = Text()
        for child in node.children or []:
            _append_inline(child, piece)
        piece.stylize("bold")
        out.append_text(piece)
    elif t == "em":
        piece = Text()
        for child in node.children or []:
            _append_inline(child, piece)
        piece.stylize("italic")
        out.append_text(piece)
    elif t == "code_inline":
        out.append(node.content, style=INLINE_CODE_STYLE)
    elif t == "link":
        href = node.attrGet("href") or ""
        piece = Text()
        for child in node.children or []:
            _append_inline(child, piece)
        if href.startswith("mailto:"):
            email = href[len("mailto:") :]
            plain = piece.plain
            if plain and plain != email and plain != href:
                piece.stylize(f"link {href}")
                out.append_text(piece)
            else:
                out.append(email, style=f"link {href}")
            return
        if href:
            plain = piece.plain
            if plain and plain != href:
                piece.stylize(f"link {href}")
                out.append_text(piece)
            else:
                out.append(href, style=f"link {href}")
        else:
            out.append_text(piece)
    elif t == "image":
        href = node.attrGet("src") or ""
        out.append(href)
    elif t in ("html_inline", "html_block"):
        out.append(node.content)
    else:
        content = getattr(node, "content", "")
        if content:
            out.append(content)


def _append_with_cont_indent(target: Text, source: Text, cont_indent: str) -> None:
    lines = source.split("\n", allow_blank=True)
    for i, line in enumerate(lines):
        if i > 0:
            target.append("\n" + cont_indent)
        target.append_text(line)


def _render_code_as_text(node: Any) -> Text:
    return Text(node.content.rstrip("\n"), style="dim")


def _render_list(node: Any, ordered: bool, depth: int) -> Text:
    result = Text()
    items = list(node.children)
    start = 1
    if ordered:
        try:
            start = int(node.attrGet("start") or 1)
        except (TypeError, ValueError):
            start = 1

    for i, item in enumerate(items):
        indent = "  " * depth
        cont = indent + "  "
        if ordered:
            prefix = f"{_list_number(depth, start + i)}. "
        else:
            prefix = "- "
        result.append(indent + prefix)
        first = True
        for child in item.children or []:
            if child.type == "paragraph":
                if not first:
                    result.append("\n" + cont)
                _append_with_cont_indent(result, _render_inline_container(child), cont)
                first = False
            elif child.type in ("bullet_list", "ordered_list"):
                result.append("\n")
                result.append_text(
                    _render_list(
                        child,
                        ordered=(child.type == "ordered_list"),
                        depth=depth + 1,
                    )
                )
            elif child.type in ("fence", "code_block"):
                if not first:
                    result.append("\n" + cont)
                _append_with_cont_indent(result, _render_code_as_text(child), cont)
                first = False
            else:
                rendered = _render_block(child)
                if rendered is None:
                    continue
                if not first:
                    result.append("\n" + cont)
                if isinstance(rendered, Text):
                    _append_with_cont_indent(result, rendered, cont)
                first = False
        if i < len(items) - 1:
            result.append("\n")
    return result


def _list_number(depth: int, n: int) -> str:
    if depth == 0:
        return str(n)
    if depth == 1:
        return _to_letters(n)
    if depth == 2:
        return _to_roman(n)
    return str(n)


def _to_letters(n: int) -> str:
    result = ""
    while n > 0:
        n -= 1
        result = chr(ord("a") + (n % 26)) + result
        n //= 26
    return result or "a"


_ROMAN = [
    (1000, "m"),
    (900, "cm"),
    (500, "d"),
    (400, "cd"),
    (100, "c"),
    (90, "xc"),
    (50, "l"),
    (40, "xl"),
    (10, "x"),
    (9, "ix"),
    (5, "v"),
    (4, "iv"),
    (1, "i"),
]


def _to_roman(n: int) -> str:
    out = ""
    for value, numeral in _ROMAN:
        while n >= value:
            out += numeral
            n -= value
    return out


def _render_blockquote(node: Any) -> Text:
    inner_blocks: list[Text] = []
    for child in node.children or []:
        if child.type in ("fence", "code_block"):
            inner_blocks.append(_render_code_as_text(child))
            continue
        rendered = _render_block(child)
        if isinstance(rendered, Text):
            inner_blocks.append(rendered)

    combined = Text()
    for i, block in enumerate(inner_blocks):
        if i > 0:
            combined.append("\n\n")
        combined.append_text(block)
    combined.stylize("italic")

    lines = combined.split("\n", allow_blank=True)
    out = Text()
    for i, line in enumerate(lines):
        if i > 0:
            out.append("\n")
        if line.plain.strip():
            out.append(f"{BLOCKQUOTE_BAR} ", style="dim")
            out.append_text(line)
        else:
            out.append_text(line)
    return out


def _render_table(node: Any) -> Text:
    header_row: list[Text] = []
    rows: list[list[Text]] = []
    aligns: list[str | None] = []

    thead = next((c for c in node.children if c.type == "thead"), None)
    tbody = next((c for c in node.children if c.type == "tbody"), None)

    if thead and thead.children:
        tr = thead.children[0]
        for th in tr.children or []:
            header_row.append(_render_inline_container(th))
            aligns.append(th.attrGet("style"))
    if tbody:
        for tr in tbody.children or []:
            row: list[Text] = []
            for td in tr.children or []:
                row.append(_render_inline_container(td))
            rows.append(row)

    if not header_row:
        return Text("")

    widths = [max(3, cell.cell_len) for cell in header_row]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], cell.cell_len)

    out = Text()
    out.append("| ")
    for i, cell in enumerate(header_row):
        out.append_text(_pad(cell, widths[i], aligns[i] if i < len(aligns) else None))
        out.append(" | ")
    out = _rstrip_trailing_space(out)
    out.append("\n|")
    for w in widths:
        out.append("-" * (w + 2))
        out.append("|")
    for row in rows:
        out.append("\n| ")
        for i, cell in enumerate(row):
            width = widths[i] if i < len(widths) else cell.cell_len
            align = aligns[i] if i < len(aligns) else None
            out.append_text(_pad(cell, width, align))
            out.append(" | ")
        out = _rstrip_trailing_space(out)
    return out


def _pad(cell: Text, width: int, align: str | None) -> Text:
    padding = max(0, width - cell.cell_len)
    if not padding:
        return cell
    if align and "center" in align:
        left = padding // 2
        right = padding - left
        out = Text(" " * left)
        out.append_text(cell)
        out.append(" " * right)
        return out
    if align and "right" in align:
        out = Text(" " * padding)
        out.append_text(cell)
        return out
    out = Text()
    out.append_text(cell)
    out.append(" " * padding)
    return out


def _rstrip_trailing_space(text: Text) -> Text:
    plain = text.plain
    stripped = plain.rstrip(" ")
    trim = len(plain) - len(stripped)
    if trim:
        return text[: len(plain) - trim]
    return text
