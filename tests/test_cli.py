import json
from unittest.mock import AsyncMock, patch

import pytest
import yaml
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
        # SQLite DB 在首次访问时由 get_registry() 惰性创建
        assert not (cwd / ".openkb" / "hashes.json").exists()

        config = yaml.safe_load((cwd / ".openkb" / "config.yaml").read_text())
        assert config["storage_backend"] == "sqlite"

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


def test_run_lint_reads_from_registry_not_json(tmp_path):
    """run_lint should use registry.all_entries() instead of hashes.json directly."""
    import asyncio

    from openkb.cli import run_lint
    from openkb.config import DEFAULT_CONFIG
    from openkb.state import get_registry

    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    wiki_dir = tmp_path / "wiki"
    (wiki_dir / "sources").mkdir(parents=True)
    (wiki_dir / "summaries").mkdir(parents=True)
    (wiki_dir / "concepts").mkdir(parents=True)
    (wiki_dir / "reports").mkdir(parents=True)

    config_path = openkb_dir / "config.yaml"
    config_path.write_text(yaml.safe_dump(DEFAULT_CONFIG, allow_unicode=True), encoding="utf-8")

    registry = get_registry(openkb_dir, backend="sqlite")
    registry.add("hash123", {"name": "doc.pdf"})

    with patch("openkb.cli._setup_llm_key"), \
         patch("openkb.lint.run_structural_lint", return_value="structural ok"), \
         patch(
             "openkb.agent.linter.run_knowledge_lint",
             new_callable=AsyncMock,
             return_value="knowledge ok",
         ):
        result = asyncio.run(run_lint(tmp_path))

    assert result is not None
    assert "knowledge ok" in result.read_text(encoding="utf-8")
