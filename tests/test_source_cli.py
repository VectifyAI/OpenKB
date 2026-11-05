"""CLI and REST coverage for ``openkb source``."""

from __future__ import annotations

from click.testing import CliRunner

from openkb.cli import cli
from openkb.connectors.sync import BindingReport, SyncOutcome


def _kb(tmp_path):
    kb = tmp_path / "kb"
    (kb / ".openkb").mkdir(parents=True)
    (kb / "wiki").mkdir()
    (kb / ".env").write_text(
        "OPENKB_S3_ACCESS_KEY_ID=AKIA_TEST\nOPENKB_S3_SECRET_ACCESS_KEY=secret-test\n",
        encoding="utf-8",
    )
    return kb


def test_source_add_requires_credentials_before_saving(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    (kb / ".env").write_text("", encoding="utf-8")
    monkeypatch.delenv("OPENKB_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("OPENKB_S3_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.chdir(kb)
    result = CliRunner().invoke(
        cli,
        ["source", "add", "s3", "--name", "papers", "--region", "us-east-1", "--bucket", "acme"],
    )
    assert result.exit_code != 0
    assert "credentials are missing" in result.output
    assert (
        "connectors" not in (kb / ".openkb" / "config.yaml").read_text(encoding="utf-8")
        if (kb / ".openkb" / "config.yaml").exists()
        else True
    )


def test_source_add_list_remove(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    monkeypatch.delenv("OPENKB_S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("OPENKB_S3_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.chdir(kb)
    runner = CliRunner()
    added = runner.invoke(
        cli,
        [
            "source",
            "add",
            "s3",
            "--name",
            "papers",
            "--region",
            "us-east-1",
            "--bucket",
            "acme",
            "--prefix",
            "docs/",
            "--non-interactive",
        ],
    )
    assert added.exit_code == 0, added.output
    listed = runner.invoke(cli, ["source", "list"])
    assert "papers" in listed.output
    assert "s3://acme/docs/" in listed.output
    removed = runner.invoke(cli, ["source", "remove", "papers"])
    assert removed.exit_code == 0
    assert "remain in the wiki" in removed.output


def test_source_sync_exit_code(tmp_path, monkeypatch):
    kb = _kb(tmp_path)
    monkeypatch.chdir(kb)
    (kb / ".openkb" / "config.yaml").write_text(
        "connectors:\n- name: papers\n  provider: s3\n  region: us-east-1\n"
        "  bucket: acme\n  prefix: ''\n",
        encoding="utf-8",
    )

    def fake_sync(kb_dir, *, name=None, non_interactive=False, **kwargs):
        assert non_interactive is True
        return SyncOutcome(
            reports=[
                BindingReport(
                    name="papers",
                    provider="s3",
                    ok=False,
                    failed=1,
                    error="one file failed",
                )
            ]
        )

    monkeypatch.setattr("openkb.source_cmd.sync_bindings", fake_sync)
    result = CliRunner().invoke(cli, ["source", "sync", "--non-interactive"])
    assert result.exit_code == 1
    assert "failed 1" in result.output
