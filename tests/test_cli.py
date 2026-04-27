import json
from unittest.mock import patch

from click.testing import CliRunner

from openkb.config import DEFAULT_CONFIG, load_config
from openkb.cli import cli
from openkb.schema import AGENTS_MD


def invoke_init_with_defaults(runner):
    return runner.invoke(cli, ["init"], input="\n\n\n")


def test_init_creates_structure(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        result = invoke_init_with_defaults(runner)
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

        # Pressing Enter through the prompts accepts the configured defaults.
        config = load_config(cwd / ".openkb" / "config.yaml")
        assert config == DEFAULT_CONFIG

        # index.md header
        index_content = (cwd / "wiki" / "index.md").read_text()
        assert index_content == "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n"


def test_init_schema_content(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        result = invoke_init_with_defaults(runner)
        assert result.exit_code == 0

        from pathlib import Path
        agents_content = Path("wiki/AGENTS.md").read_text()
        assert agents_content == AGENTS_MD


def test_init_already_exists(tmp_path):
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path), \
         patch("openkb.cli.register_kb"):
        # First run should succeed
        result = invoke_init_with_defaults(runner)
        assert result.exit_code == 0

        # Second run should print already initialized message
        result = runner.invoke(cli, ["init"])
        assert result.exit_code == 0
        assert "already initialized" in result.output
