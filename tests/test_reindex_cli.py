from unittest.mock import patch

from click.testing import CliRunner

from openkb.cli import cli


def _kb(tmp_path, topic_tree=True):
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    (kb / "wiki" / "concepts").mkdir(parents=True)
    (kb / "wiki" / "concepts" / "a.md").write_text("# a\n", encoding="utf-8")
    flag = "topic_tree: true\n" if topic_tree else ""
    (kb / ".openkb" / "config.yaml").write_text(
        f"model: gpt-5.4\n{flag}", encoding="utf-8"
    )
    return kb


def test_reindex_runs_when_enabled(tmp_path):
    kb = _kb(tmp_path, topic_tree=True)
    with patch("openkb.cli.tt_bootstrap", return_value=1) as boot, \
            patch("openkb.cli._setup_llm_key"):
        res = CliRunner().invoke(cli, ["--kb-dir", str(kb), "reindex"])
    assert res.exit_code == 0, res.output
    assert boot.called
    assert "1" in res.output


def test_reindex_noop_when_disabled(tmp_path):
    kb = _kb(tmp_path, topic_tree=False)
    with patch("openkb.cli.tt_bootstrap") as boot, patch("openkb.cli._setup_llm_key"):
        res = CliRunner().invoke(cli, ["--kb-dir", str(kb), "reindex"])
    assert res.exit_code == 0, res.output
    assert not boot.called
    assert "topic_tree" in res.output
