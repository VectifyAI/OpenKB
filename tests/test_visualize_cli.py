from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from openkb.cli import cli


def _kb(tmp_path: Path) -> Path:
    for sub in ("summaries", "concepts", "entities"):
        (tmp_path / "wiki" / sub).mkdir(parents=True)
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n", encoding="utf-8")
    (tmp_path / "wiki" / "concepts" / "a.md").write_text(
        '---\ntype: "Concept"\ndescription: "d"\n---\n\nlinks [[concepts/b]]\n', encoding="utf-8"
    )
    (tmp_path / "wiki" / "concepts" / "b.md").write_text(
        '---\ntype: "Concept"\ndescription: "d2"\n---\n\n# B\n', encoding="utf-8"
    )
    return tmp_path


def test_visualize_writes_html_and_opens_by_default(tmp_path):
    kb = _kb(tmp_path)
    with patch("openkb.cli._find_kb_dir", return_value=kb), patch("webbrowser.open") as wb:
        result = CliRunner().invoke(cli, ["visualize"])
    assert result.exit_code == 0, result.output
    out = kb / "output" / "visualize" / "graph.html"
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "<canvas" in html and '"concepts/a"' in html
    wb.assert_called_once()  # auto-opens the browser by default


def test_visualize_no_open_suppresses_browser(tmp_path):
    kb = _kb(tmp_path)
    with patch("openkb.cli._find_kb_dir", return_value=kb), patch("webbrowser.open") as wb:
        result = CliRunner().invoke(cli, ["visualize", "--no-open"])
    assert result.exit_code == 0, result.output
    assert (kb / "output" / "visualize" / "graph.html").exists()
    wb.assert_not_called()  # --no-open keeps it headless-friendly


def test_visualize_empty_wiki(tmp_path):
    for sub in ("summaries", "concepts", "entities"):
        (tmp_path / "wiki" / sub).mkdir(parents=True)
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n", encoding="utf-8")
    with patch("openkb.cli._find_kb_dir", return_value=tmp_path), patch("webbrowser.open") as wb:
        result = CliRunner().invoke(cli, ["visualize"])
    assert result.exit_code == 0
    assert "No wiki pages" in result.output
    assert not (tmp_path / "output" / "visualize" / "graph.html").exists()
    wb.assert_not_called()  # nothing to show → no browser
