"""Tests for the bundle ingest add path."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import patch

from click.testing import CliRunner

from openkb.cli import cli
from openkb.converter import _registry_path
from openkb.ingest.add import add_bundle_target, resolve_ingest_pipeline
from openkb.ingest.context import IngestContext
from openkb.ingest.enrichers import LinkDiscoveryEnricher
from openkb.ingest.importers.feishu import looks_like_feishu_url
from openkb.ingest.models import DocumentBundle, IngestInput, ProvenanceRecord, TextBlock
from openkb.state import HashRegistry


def _setup_kb(tmp_path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki" / "sources" / "images").mkdir(parents=True)
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "index.md").write_text("# Index\n", encoding="utf-8")
    (tmp_path / "wiki" / "log.md").write_text("# Operations Log\n\n", encoding="utf-8")
    openkb_dir = tmp_path / ".openkb"
    openkb_dir.mkdir()
    (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n", encoding="utf-8")
    (openkb_dir / "hashes.json").write_text(json.dumps({}), encoding="utf-8")
    return tmp_path


def _write_config(kb_dir, body: str) -> None:
    (kb_dir / ".openkb" / "config.yaml").write_text(body, encoding="utf-8")


def _write_url_markdown(raw_dir, name: str, body: str):
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_text(body, encoding="utf-8")
    return path


def _completed_process(stdout: str, *, returncode: int = 0, stderr: str = ""):
    return type(
        "Completed",
        (),
        {"stdout": stdout, "stderr": stderr, "returncode": returncode},
    )()


class _FakeEntryPoint:
    def __init__(self, group: str, name: str, loaded):
        self.group = group
        self.name = name
        self._loaded = loaded

    def load(self):
        return self._loaded


class _FakeEntryPoints(list):
    def select(self, *, group: str):
        return [entry_point for entry_point in self if entry_point.group == group]


def test_resolve_ingest_pipeline_defaults_to_legacy():
    assert resolve_ingest_pipeline({}) == "legacy"
    assert resolve_ingest_pipeline({"ingest": {"pipeline": "bundle"}}) == "bundle"
    assert resolve_ingest_pipeline({"ingest": {"pipeline": "bundle"}}, "legacy") == "legacy"


def test_external_importer_and_normalizer_entry_points_can_add_custom_source(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  importers:\n"
        "    enabled:\n      - mocksource\n  normalizers:\n    enabled:\n"
        "      - mocknormalizer\n",
    )

    class MockImporter:
        name = "mocksource"

        def can_handle(self, target, context):
            del context
            return target == "mock://doc"

        def import_source(self, target, context):
            path = context.staging_dir / "raw" / "mock.mock"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("raw mock", encoding="utf-8")
            return IngestInput(
                target=target,
                path=path,
                source_uri=target,
                media_type="application/x-mock",
                metadata={"display_name": "mock.mock"},
            )

    class MockNormalizer:
        name = "mocknormalizer"

        def supports(self, input_, context):
            del context
            return input_.media_type == "application/x-mock"

        def normalize(self, input_, context):
            del context
            source_uri = input_.source_uri or input_.target
            return DocumentBundle(
                id=source_uri,
                title="Mock",
                source_uri=source_uri,
                blocks=[TextBlock("# Mock\n\nPlugin body.")],
                metadata={
                    "display_name": "mock.mock",
                    "source_path": input_.path.as_posix(),
                    "source_identity": source_uri,
                },
                provenance=[ProvenanceRecord(source_uri=source_uri)],
            )

    async def compile_noop(*args, **kwargs):
        return None

    entry_points = _FakeEntryPoints(
        [
            _FakeEntryPoint("openkb.ingest.importers", "mocksource", MockImporter),
            _FakeEntryPoint("openkb.ingest.normalizers", "mocknormalizer", MockNormalizer),
        ]
    )
    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.ingest.plugins.entry_points", return_value=entry_points),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target("mock://doc", kb_dir)

    assert outcome == "added"
    assert (kb_dir / "wiki" / "sources" / "mock.md").read_text(encoding="utf-8") == (
        "# Mock\n\nPlugin body.\n"
    )
    ((_, meta),) = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries().items()
    assert meta["path"] == "mock://doc"


def test_external_enricher_entry_point_can_modify_bundle(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  enrichers:\n"
        "    enabled:\n      - append_text\n",
    )
    doc = tmp_path / "notes.md"
    doc.write_text("# Notes\n", encoding="utf-8")

    class AppendTextEnricher:
        name = "append_text"

        def applies_to(self, bundle, context):
            del bundle, context
            return True

        def enrich(self, bundle, context):
            del context
            return replace(bundle, blocks=[*bundle.blocks, TextBlock("Plugin enrichment.")])

    async def compile_noop(*args, **kwargs):
        return None

    entry_points = _FakeEntryPoints(
        [_FakeEntryPoint("openkb.ingest.enrichers", "append_text", AppendTextEnricher)]
    )
    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.ingest.plugins.entry_points", return_value=entry_points),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(doc, kb_dir)

    assert outcome == "added"
    rendered = (kb_dir / "wiki" / "sources" / "notes.md").read_text(encoding="utf-8")
    assert rendered == "# Notes\n\nPlugin enrichment.\n"


def test_cli_bundle_pipeline_dispatches_single_file(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    doc = tmp_path / "notes.md"
    doc.write_text("# Notes\n", encoding="utf-8")

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=kb_dir),
        patch("openkb.ingest.add.add_bundle_target", return_value="added") as mock_add,
        patch("openkb.cli.add_single_file") as legacy_add,
    ):
        result = runner.invoke(cli, ["add", str(doc), "--ingest-pipeline", "bundle"])

    assert result.exit_code == 0, result.output
    mock_add.assert_called_once()
    assert mock_add.call_args.args == (doc, kb_dir)
    assert mock_add.call_args.kwargs["legacy_fallback"] is legacy_add
    legacy_add.assert_not_called()


def test_cli_config_bundle_pipeline_dispatches_directory(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    (kb_dir / ".openkb" / "config.yaml").write_text(
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n", encoding="utf-8")
    (docs / "b.md").write_text("# B\n", encoding="utf-8")

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=kb_dir),
        patch("openkb.ingest.add.add_bundle_target", return_value="added") as mock_add,
    ):
        result = runner.invoke(cli, ["add", str(docs)])

    assert result.exit_code == 0, result.output
    assert [call.args[0].name for call in mock_add.call_args_list] == ["a.md", "b.md"]
    assert all(
        call.kwargs["legacy_fallback"].__name__ == "add_single_file"
        for call in mock_add.call_args_list
    )


def test_cli_bundle_pipeline_dispatches_url_to_bundle(tmp_path):
    kb_dir = _setup_kb(tmp_path)

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=kb_dir),
        patch("openkb.ingest.add.add_bundle_target", return_value="added") as mock_add,
        patch("openkb.cli.add_single_file") as legacy_add,
        patch("openkb.url_ingest.fetch_url_to_raw") as legacy_fetch,
    ):
        result = runner.invoke(
            cli,
            ["add", "https://example.com/article", "--ingest-pipeline", "bundle"],
        )

    assert result.exit_code == 0, result.output
    mock_add.assert_called_once_with(
        "https://example.com/article",
        kb_dir,
        legacy_fallback=legacy_add,
    )
    legacy_fetch.assert_not_called()
    legacy_add.assert_not_called()


def test_feishu_importer_recognizes_supported_doc_urls():
    assert looks_like_feishu_url("https://acme.feishu.cn/wiki/D3l4w0Y")
    assert looks_like_feishu_url("https://acme.feishu.cn/docx/AbCdEf")
    assert looks_like_feishu_url("https://acme.larksuite.com/docs/doccn123")
    assert not looks_like_feishu_url("https://acme.feishu.cn/sheets/abc")
    assert not looks_like_feishu_url("https://example.com/wiki/D3l4w0Y")


def test_cli_bundle_pipeline_accepts_image_file(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=kb_dir),
        patch("openkb.ingest.add.add_bundle_target", return_value="added") as mock_add,
    ):
        result = runner.invoke(cli, ["add", str(image), "--ingest-pipeline", "bundle"])

    assert result.exit_code == 0, result.output
    mock_add.assert_called_once()
    assert mock_add.call_args.args == (image, kb_dir)


def test_cli_legacy_pipeline_still_rejects_image_file(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=kb_dir):
        result = runner.invoke(cli, ["add", str(image)])

    assert result.exit_code == 0, result.output
    assert "Unsupported file type: .png" in result.output


def test_bundle_markdown_add_writes_compatible_artifacts_and_registry(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    doc = tmp_path / "notes.md"
    doc.write_text("# Notes\n\nBody.\n", encoding="utf-8")
    compile_calls = []

    async def compile_noop(*args, **kwargs):
        compile_calls.append((args, kwargs))

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(doc, kb_dir)

    assert outcome == "added"
    assert len(compile_calls) == 1
    assert (kb_dir / "raw" / "notes.md").read_text(encoding="utf-8") == "# Notes\n\nBody.\n"
    assert (kb_dir / "wiki" / "sources" / "notes.md").read_text(encoding="utf-8") == (
        "# Notes\n\nBody.\n"
    )
    bundle_sidecar = kb_dir / ".openkb" / "bundles" / "notes.json"
    bundle_json = json.loads(bundle_sidecar.read_text(encoding="utf-8"))
    assert bundle_json["schema_version"] == 1
    assert bundle_json["source_uri"] == _registry_path(doc, kb_dir)
    assert bundle_json["blocks"][0]["type"] == "text"
    assert bundle_json["provenance"][0]["source_uri"] == _registry_path(doc, kb_dir)

    entries = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries()
    assert len(entries) == 1
    ((_, meta),) = entries.items()

    assert meta == {
        "name": "notes.md",
        "doc_name": "notes",
        "type": "md",
        "path": _registry_path(doc, kb_dir),
        "raw_path": "raw/notes.md",
        "source_path": "wiki/sources/notes.md",
        "bundle_path": ".openkb/bundles/notes.json",
    }


def test_bundle_markdown_add_rolls_back_on_compile_failure(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    doc = tmp_path / "notes.md"
    doc.write_text("# Notes\n", encoding="utf-8")

    async def fail_compile(*args, **kwargs):
        raise RuntimeError("LLM 503")

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.agent.compiler.compile_short_doc", new=fail_compile),
        patch("openkb.cli.time.sleep"),
    ):
        outcome = add_bundle_target(doc, kb_dir)

    assert outcome == "failed"
    assert not (kb_dir / "raw" / "notes.md").exists()
    assert not (kb_dir / "wiki" / "sources" / "notes.md").exists()
    assert HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries() == {}


def test_bundle_text_add_writes_markdown_source(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    doc = tmp_path / "notes.txt"
    doc.write_text("Plain notes\n", encoding="utf-8")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(doc, kb_dir)

    assert outcome == "added"
    assert (kb_dir / "raw" / "notes.txt").read_text(encoding="utf-8") == "Plain notes\n"
    assert (kb_dir / "wiki" / "sources" / "notes.md").read_text(encoding="utf-8") == (
        "Plain notes\n"
    )
    ((_, meta),) = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries().items()
    assert meta["type"] == "txt"
    assert meta["raw_path"] == "raw/notes.txt"
    assert meta["source_path"] == "wiki/sources/notes.md"


def test_bundle_short_pdf_uses_existing_pdf_renderer(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    doc = tmp_path / "paper.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.ingest.normalizers.pdf.get_pdf_page_count", return_value=3),
        patch(
            "openkb.ingest.render.convert_pdf_with_images",
            return_value="# Paper\n\nConverted.",
        ) as convert_pdf,
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(doc, kb_dir)

    assert outcome == "added"
    convert_pdf.assert_called_once()
    assert (kb_dir / "wiki" / "sources" / "paper.md").read_text(encoding="utf-8") == (
        "# Paper\n\nConverted."
    )
    ((_, meta),) = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries().items()
    assert meta["type"] == "pdf"


def test_bundle_markitdown_path_uses_existing_base64_extractor(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    doc = tmp_path / "report.docx"
    doc.write_bytes(b"fake docx")
    mock_result = type("Result", (), {"text_content": "![](data:image/png;base64,abc123)"})()

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.ingest.render.MarkItDown") as markitdown,
        patch("openkb.ingest.render.extract_base64_images", return_value="converted markdown"),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        markitdown.return_value.convert.return_value = mock_result
        outcome = add_bundle_target(doc, kb_dir)

    assert outcome == "added"
    markitdown.return_value.convert.assert_called_once_with(str(doc.resolve()), keep_data_uris=True)
    assert (kb_dir / "wiki" / "sources" / "report.md").read_text(encoding="utf-8") == (
        "converted markdown"
    )
    ((_, meta),) = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries().items()
    assert meta["type"] == "docx"


def test_bundle_url_add_writes_staged_artifacts_and_registry(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  importers:\n"
        "    enabled:\n      - url\n",
    )

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch(
            "openkb.ingest.importers.url.fetch_url_to_dir",
            side_effect=lambda _url, raw_dir: _write_url_markdown(
                raw_dir,
                "Remote-Article.md",
                "# Remote Article\n\nBody.\n",
            ),
        ) as fetch,
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target("https://example.com/article", kb_dir)

    assert outcome == "added"
    fetch.assert_called_once()
    assert (kb_dir / "raw" / "Remote-Article.md").read_text(encoding="utf-8") == (
        "# Remote Article\n\nBody.\n"
    )
    assert (kb_dir / "wiki" / "sources" / "Remote-Article.md").read_text(
        encoding="utf-8"
    ) == "# Remote Article\n\nBody.\n"
    ((_, meta),) = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries().items()
    assert meta["path"] == "https://example.com/article"
    assert meta["raw_path"] == "raw/Remote-Article.md"
    assert meta["source_path"] == "wiki/sources/Remote-Article.md"


def test_bundle_feishu_add_uses_lark_cli_and_preserves_source_url(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  importers:\n"
        "    enabled:\n      - feishu\n",
    )
    url = "https://acme.feishu.cn/wiki/D3l4w0Y"
    stdout = json.dumps({"data": {"title": "Feishu PRD", "content": "# Feishu PRD\n\nBody."}})

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch(
            "openkb.ingest.importers.feishu.subprocess.run",
            return_value=_completed_process(stdout),
        ) as run,
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(url, kb_dir)

    assert outcome == "added"
    command = run.call_args.args[0]
    assert command[:3] == ["lark-cli", "docs", "+fetch"]
    assert command[command.index("--doc") + 1] == url
    assert (kb_dir / "raw" / "Feishu-PRD.md").read_text(encoding="utf-8") == (
        "# Feishu PRD\n\nBody.\n"
    )
    assert (kb_dir / "wiki" / "sources" / "Feishu-PRD.md").read_text(
        encoding="utf-8"
    ) == "# Feishu PRD\n\nBody.\n"
    bundle_json = json.loads(
        (kb_dir / ".openkb" / "bundles" / "Feishu-PRD.json").read_text(encoding="utf-8")
    )
    assert bundle_json["title"] == "Feishu PRD"
    assert bundle_json["source_uri"] == url
    assert bundle_json["metadata"]["source_metadata"]["source_system"] == "feishu"
    assert (
        bundle_json["metadata"]["source_metadata"]["permission_boundary"]
        == "current lark-cli identity"
    )
    ((_, meta),) = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries().items()
    assert meta["path"] == url
    assert meta["raw_path"] == "raw/Feishu-PRD.md"
    assert meta["source_path"] == "wiki/sources/Feishu-PRD.md"


def test_bundle_image_add_writes_image_asset_markdown_and_registry(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    image = tmp_path / "diagram.png"
    image_bytes = b"\x89PNG\r\n\x1a\nfake image"
    image.write_bytes(image_bytes)

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(image, kb_dir)

    assert outcome == "added"
    assert (kb_dir / "raw" / "diagram.png").read_bytes() == image_bytes
    rendered = (kb_dir / "wiki" / "sources" / "diagram.md").read_text(encoding="utf-8")
    assert rendered == "# diagram\n\n![source image](sources/images/diagram/diagram.png)\n"
    assert (kb_dir / "wiki" / "sources" / "images" / "diagram" / "diagram.png").read_bytes() == (
        image_bytes
    )
    ((_, meta),) = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries().items()
    assert meta["type"] == "png"
    assert meta["raw_path"] == "raw/diagram.png"
    assert meta["source_path"] == "wiki/sources/diagram.md"


def test_bundle_image_vision_enricher_writes_derived_sections(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n"
        "  image_model: gpt-4o-vision\n  enrichers:\n    enabled:\n"
        "      - image_vision\n",
    )
    image = tmp_path / "diagram.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nfake image")
    vision_payload = json.dumps(
        {
            "visual_description": "A system architecture diagram with three boxes.",
            "ocr_text": "API Gateway",
            "keywords": ["architecture", "gateway"],
            "uncertainty": "Small labels may be incomplete.",
        }
    )

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch(
            "litellm.completion",
            return_value={"choices": [{"message": {"content": vision_payload}}]},
        ) as completion,
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(image, kb_dir)

    assert outcome == "added"
    assert completion.call_args.kwargs["model"] == "gpt-4o-vision"
    message_content = completion.call_args.kwargs["messages"][0]["content"]
    assert message_content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    rendered = (kb_dir / "wiki" / "sources" / "diagram.md").read_text(encoding="utf-8")
    assert "## 视觉描述" in rendered
    assert "模型派生信息，可能不完整或不准确。" in rendered
    assert "A system architecture diagram with three boxes." in rendered
    assert "## 可见文字" in rendered
    assert "API Gateway" in rendered


def test_cli_bundle_long_pdf_falls_back_to_legacy(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    doc = tmp_path / "long.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")

    runner = CliRunner()
    with (
        patch("openkb.cli._find_kb_dir", return_value=kb_dir),
        patch("openkb.ingest.normalizers.pdf.get_pdf_page_count", return_value=200),
        patch("openkb.cli.add_single_file", return_value="added") as legacy_add,
    ):
        result = runner.invoke(cli, ["add", str(doc), "--ingest-pipeline", "bundle"])

    assert result.exit_code == 0, result.output
    assert "Falling back to legacy long-document ingest" in result.output
    legacy_add.assert_called_once_with(doc.resolve(), kb_dir)


def test_link_discovery_enricher_finds_markdown_and_bare_links(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    context = IngestContext(kb_dir=kb_dir, config={}, staging_dir=kb_dir / ".openkb" / "staging")
    bundle = DocumentBundle(
        id="root",
        title="Root",
        source_uri="root.md",
        blocks=[
            TextBlock(
                text=("See [Child](child.md), ![figure](image.png), and https://example.com/page.")
            )
        ],
    )

    enriched = LinkDiscoveryEnricher().enrich(bundle, context)

    assert [(link.uri, link.text) for link in enriched.links] == [
        ("child.md", "Child"),
        ("https://example.com/page", None),
    ]


def test_bundle_recursive_imports_local_markdown_links(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  link_discovery: true\n"
        "  max_depth: 1\n  max_documents: 5\n",
    )
    root = tmp_path / "root.md"
    child = tmp_path / "child.md"
    root.write_text("# Root\n\nSee [Child](child.md).\n", encoding="utf-8")
    child.write_text("# Child\n\nBody.\n", encoding="utf-8")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(root, kb_dir)

    assert outcome == "added"
    assert (kb_dir / "wiki" / "sources" / "root.md").exists()
    assert (kb_dir / "wiki" / "sources" / "child.md").exists()
    child_bundle = json.loads(
        (kb_dir / ".openkb" / "bundles" / "child.json").read_text(encoding="utf-8")
    )
    assert child_bundle["provenance"][0]["relationship"] == "child"
    assert child_bundle["provenance"][0]["parent_uri"] == _registry_path(root, kb_dir)
    entries = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries()
    assert {meta["doc_name"] for meta in entries.values()} == {"root", "child"}


def test_bundle_recursion_respects_max_depth_zero(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  link_discovery: true\n"
        "  max_depth: 0\n  max_documents: 5\n",
    )
    root = tmp_path / "root.md"
    child = tmp_path / "child.md"
    root.write_text("# Root\n\nSee [Child](child.md).\n", encoding="utf-8")
    child.write_text("# Child\n\nBody.\n", encoding="utf-8")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(root, kb_dir)

    assert outcome == "added"
    assert (kb_dir / "wiki" / "sources" / "root.md").exists()
    assert not (kb_dir / "wiki" / "sources" / "child.md").exists()


def test_bundle_recursion_respects_max_documents(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  link_discovery: true\n"
        "  max_depth: 1\n  max_documents: 1\n",
    )
    root = tmp_path / "root.md"
    child = tmp_path / "child.md"
    root.write_text("# Root\n\nSee [Child](child.md).\n", encoding="utf-8")
    child.write_text("# Child\n\nBody.\n", encoding="utf-8")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(root, kb_dir)

    assert outcome == "added"
    assert (kb_dir / "wiki" / "sources" / "root.md").exists()
    assert not (kb_dir / "wiki" / "sources" / "child.md").exists()


def test_bundle_recursion_deduplicates_cycles(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  link_discovery: true\n"
        "  max_depth: 5\n  max_documents: 5\n",
    )
    root = tmp_path / "root.md"
    child = tmp_path / "child.md"
    root.write_text("# Root\n\nSee [Child](child.md).\n", encoding="utf-8")
    child.write_text("# Child\n\nBack to [Root](root.md).\n", encoding="utf-8")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(root, kb_dir)

    assert outcome == "added"
    entries = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries()
    assert {meta["doc_name"] for meta in entries.values()} == {"root", "child"}


def test_bundle_recursion_skips_url_when_url_importer_disabled(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  link_discovery: true\n"
        "  max_depth: 1\n  max_documents: 5\n  allow_domains:\n    - example.com\n",
    )
    root = tmp_path / "root.md"
    root.write_text("# Root\n\nSee [Remote](https://example.com/child.md).\n", encoding="utf-8")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(root, kb_dir)

    assert outcome == "added"
    entries = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries()
    assert [meta["doc_name"] for meta in entries.values()] == ["root"]


def test_bundle_recursion_imports_allowed_url_links(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  link_discovery: true\n"
        "  max_depth: 1\n  max_documents: 5\n  allow_domains:\n    - example.com\n"
        "  importers:\n    enabled:\n      - file\n      - url\n",
    )
    root = tmp_path / "root.md"
    root.write_text("# Root\n\nSee [Remote](https://example.com/child?q=1#section).\n")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch(
            "openkb.ingest.importers.url.fetch_url_to_dir",
            side_effect=lambda url, raw_dir: _write_url_markdown(
                raw_dir,
                "child.md",
                f"# Child\n\nFetched from {url}.\n",
            ),
        ) as fetch,
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(root, kb_dir)

    assert outcome == "added"
    fetch.assert_called_once()
    assert fetch.call_args.args[0] == "https://example.com/child?q=1"
    entries = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries()
    assert {meta["doc_name"] for meta in entries.values()} == {"root", "child"}
    child_meta = next(meta for meta in entries.values() if meta["doc_name"] == "child")
    assert child_meta["path"] == "https://example.com/child?q=1"


def test_bundle_recursion_imports_allowed_feishu_links_without_url_importer(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  link_discovery: true\n"
        "  max_depth: 1\n  max_documents: 5\n  allow_domains:\n    - feishu.cn\n"
        "  importers:\n    enabled:\n      - file\n      - feishu\n",
    )
    root = tmp_path / "root.md"
    feishu_url = "https://acme.feishu.cn/docx/AbCdEf#heading"
    root.write_text(f"# Root\n\nSee [Feishu]({feishu_url}).\n")
    stdout = json.dumps({"data": {"title": "Child Doc", "content": "# Child Doc\n\nBody."}})

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch(
            "openkb.ingest.importers.feishu.subprocess.run",
            return_value=_completed_process(stdout),
        ) as run,
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(root, kb_dir)

    assert outcome == "added"
    command = run.call_args.args[0]
    assert command[command.index("--doc") + 1] == "https://acme.feishu.cn/docx/AbCdEf"
    entries = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries()
    assert {meta["doc_name"] for meta in entries.values()} == {"root", "Child-Doc"}
    child_meta = next(meta for meta in entries.values() if meta["doc_name"] == "Child-Doc")
    assert child_meta["path"] == "https://acme.feishu.cn/docx/AbCdEf"


def test_bundle_recursion_blocks_feishu_link_outside_allow_domains(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  link_discovery: true\n"
        "  max_depth: 1\n  max_documents: 5\n  allow_domains:\n    - example.com\n"
        "  importers:\n    enabled:\n      - file\n      - feishu\n",
    )
    root = tmp_path / "root.md"
    root.write_text("# Root\n\nSee [Feishu](https://acme.feishu.cn/wiki/D3l4w0Y).\n")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.ingest.importers.feishu.subprocess.run") as run,
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(root, kb_dir)

    assert outcome == "added"
    run.assert_not_called()
    entries = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries()
    assert [meta["doc_name"] for meta in entries.values()] == ["root"]


def test_bundle_recursion_blocks_url_outside_allow_domains(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  link_discovery: true\n"
        "  max_depth: 1\n  max_documents: 5\n  allow_domains:\n    - example.com\n"
        "  importers:\n    enabled:\n      - file\n      - url\n",
    )
    root = tmp_path / "root.md"
    root.write_text("# Root\n\nSee [Remote](https://blocked.example.net/child).\n")

    async def compile_noop(*args, **kwargs):
        return None

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.ingest.importers.url.fetch_url_to_dir") as fetch,
        patch("openkb.agent.compiler.compile_short_doc", new=compile_noop),
    ):
        outcome = add_bundle_target(root, kb_dir)

    assert outcome == "added"
    fetch.assert_not_called()
    entries = HashRegistry(kb_dir / ".openkb" / "hashes.json").all_entries()
    assert [meta["doc_name"] for meta in entries.values()] == ["root"]


def test_bundle_url_long_pdf_fallback_keeps_staged_file_during_fallback(tmp_path):
    kb_dir = _setup_kb(tmp_path)
    _write_config(
        kb_dir,
        "model: gpt-4o-mini\ningest:\n  pipeline: bundle\n  importers:\n"
        "    enabled:\n      - url\n",
    )
    fallback_seen = []

    def fake_fetch(_url, raw_dir):
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / "long.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        return path

    def legacy_fallback(path, kb_dir_arg):
        fallback_seen.append((path.exists(), path, kb_dir_arg))
        return "added"

    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.ingest.importers.url.fetch_url_to_dir", side_effect=fake_fetch),
        patch("openkb.ingest.normalizers.pdf.get_pdf_page_count", return_value=200),
    ):
        outcome = add_bundle_target(
            "https://example.com/long.pdf",
            kb_dir,
            legacy_fallback=legacy_fallback,
        )

    assert outcome == "added"
    assert fallback_seen
    exists_during_fallback, fallback_path, fallback_kb_dir = fallback_seen[0]
    assert exists_during_fallback is True
    assert fallback_path.name == "long.pdf"
    assert fallback_kb_dir == kb_dir
