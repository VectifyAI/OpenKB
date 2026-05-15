import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from openkb.cli import cli
from openkb.schema import AGENTS_MD


def test_init_creates_structure(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0

        from pathlib import Path
        cwd = Path(".")

        # Directories
        assert (cwd / "raw").is_dir()
        assert (cwd / "wiki" / "sources" / "images").is_dir()
        assert (cwd / "wiki" / "summaries").is_dir()
        assert (cwd / "wiki" / "concepts").is_dir()
        assert (cwd / ".openkb").is_dir()

        # Files
        assert (cwd / "wiki" / "AGENTS.md").is_file()
        assert (cwd / "wiki" / "log.md").is_file()
        assert (cwd / "wiki" / "index.md").is_file()
        assert (cwd / ".openkb" / "config.yaml").is_file()
        assert (cwd / ".openkb" / "hashes.json").is_file()

        # hashes.json is empty object
        hashes = json.loads((cwd / ".openkb" / "hashes.json").read_text())
        assert hashes == {}

        # index.md header
        index_content = (cwd / "wiki" / "index.md").read_text()
        assert index_content == "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n"


def test_init_schema_content(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0

        from pathlib import Path
        agents_content = Path("wiki/AGENTS.md").read_text()
        assert agents_content == AGENTS_MD


def test_init_already_exists(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        # First run should succeed
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0

        # Second run should print already initialized message
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "already initialized" in result.output


class TestQueryStreamGate:
    """Regression tests for issue #34.

    `openkb query` should auto-disable streaming when stdout isn't a TTY
    (pipes, redirects, captured subprocess streams, MCP stdio transport),
    so non-interactive callers get the clean final answer instead of an
    interleave of tool-call telemetry and answer tokens.
    """

    @staticmethod
    def _capture_run_query(captured):
        async def fake(*_args, **kwargs):
            captured.update(kwargs)
            return "the answer"
        return fake

    def test_query_disables_stream_when_stdout_is_not_tty(self, kb_dir):
        captured: dict = {}
        with patch("openkb.cli._stream_to_tty", return_value=False), \
             patch("openkb.agent.query.run_query", side_effect=self._capture_run_query(captured)), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.cli.append_log"):
            result = CliRunner().invoke(
                cli, ["--kb-dir", str(kb_dir), "query", "what is X?"]
            )

        assert result.exit_code == 0, result.output
        assert captured["stream"] is False
        # Non-stream branch must still print the answer
        assert "the answer" in result.output

    def test_query_enables_stream_when_stdout_is_tty(self, kb_dir):
        captured: dict = {}
        with patch("openkb.cli._stream_to_tty", return_value=True), \
             patch("openkb.agent.query.run_query", side_effect=self._capture_run_query(captured)), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.cli.append_log"):
            result = CliRunner().invoke(
                cli, ["--kb-dir", str(kb_dir), "query", "what is X?"]
            )

        assert result.exit_code == 0, result.output
        assert captured["stream"] is True
        # Stream branch should NOT echo the answer again — run_query already
        # wrote tokens to stdout as they arrived.
        assert "the answer" not in result.output


class TestQuerySaveGhostStrip:
    """`openkb query --save` writes the LLM answer to wiki/explorations/.
    The agent's instructions encourage [[wikilinks]], but its view of which
    pages exist can drift from disk. Ghost wikilinks in the saved file
    would then surface as broken links the next time `openkb lint` runs.
    The save path strips them before writing.
    """

    def test_save_strips_ghost_wikilinks(self, kb_dir):
        # A real concept page exists on disk → valid wikilink target.
        (kb_dir / "wiki" / "concepts" / "attention.md").write_text(
            "# Attention\n", encoding="utf-8",
        )

        # The agent's answer includes one valid + two ghost wikilinks.
        answer = (
            "Transformers rely on [[concepts/attention]] over the input. "
            "They differ from [[concepts/rnn]] which processes sequentially, "
            "and use [[concepts/multi-head-attention]] as a key building block."
        )

        async def fake_run_query(*_args, **_kwargs):
            return answer

        with patch("openkb.cli._stream_to_tty", return_value=False), \
             patch("openkb.agent.query.run_query", side_effect=fake_run_query), \
             patch("openkb.cli._setup_llm_key"), \
             patch("openkb.cli.append_log"):
            result = CliRunner().invoke(
                cli, ["--kb-dir", str(kb_dir), "query", "transformers?", "--save"]
            )

        assert result.exit_code == 0, result.output
        explore_files = list((kb_dir / "wiki" / "explorations").glob("*.md"))
        assert len(explore_files) == 1
        saved = explore_files[0].read_text()
        # Valid link preserved
        assert "[[concepts/attention]]" in saved
        # Ghost links stripped to plain text
        assert "[[concepts/rnn]]" not in saved
        assert "rnn" in saved
        assert "[[concepts/multi-head-attention]]" not in saved
        assert "multi head attention" in saved
