from rich.console import Group
from rich.text import Text

from openkb.agent._markdown import render


def _group_text(renderable: Group) -> list[str]:
    return [part.plain for part in renderable.renderables if isinstance(part, Text)]


def test_render_preserves_inline_html():
    rendered = render("hello<br>world")

    assert isinstance(rendered, Group)
    assert _group_text(rendered) == ["hello<br>world"]


def test_render_preserves_inline_html_tags():
    rendered = render("H<sub>2</sub>O and x<sup>2</sup>")

    assert isinstance(rendered, Group)
    assert _group_text(rendered) == ["H<sub>2</sub>O and x<sup>2</sup>"]


def test_render_preserves_html_block():
    rendered = render("<details>\n<summary>More</summary>\nHidden text\n</details>")

    assert isinstance(rendered, Group)
    assert _group_text(rendered) == [
        "<details>\n<summary>More</summary>\nHidden text\n</details>",
    ]


def test_render_keeps_html_block_between_paragraphs():
    rendered = render("before\n\n<div>hello</div>\n\nafter")

    assert isinstance(rendered, Group)
    assert _group_text(rendered) == ["before", "", "<div>hello</div>", "", "after"]
