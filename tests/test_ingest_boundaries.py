"""Boundary coverage for the bundle ingest internals."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import click
import pytest

from openkb.ingest.add import (
    _add_one_bundle_target_locked,
    _allowed_recursive_targets,
    _BundleAddState,
    _domain_allowed,
    _summarize_outcomes,
    _with_parent_provenance,
    resolve_ingest_pipeline,
)
from openkb.ingest.config import resolve_ingest_options
from openkb.ingest.context import IngestContext
from openkb.ingest.enrichers.image_vision import (
    ImageVisionEnricher,
    _image_model,
    _parse_vision_response,
    _response_content,
)
from openkb.ingest.enrichers.link_discovery import LinkDiscoveryEnricher
from openkb.ingest.importers.feishu import (
    FeishuImporter,
    _fallback_title,
    _fetch_markdown_with_lark_cli,
    _find_first_string,
    _int_config,
    _parse_lark_cli_output,
    looks_like_feishu_url,
)
from openkb.ingest.importers.file import FileImporter
from openkb.ingest.importers.file import _media_type_for as file_media_type_for
from openkb.ingest.importers.url import UrlImporter
from openkb.ingest.importers.url import _media_type_for as url_media_type_for
from openkb.ingest.models import (
    Asset,
    DiscoveredLink,
    DocumentBundle,
    EmbedBlock,
    ImageBlock,
    IngestInput,
    ProvenanceRecord,
    TableBlock,
    TextBlock,
)
from openkb.ingest.normalizers.image import ImageNormalizer, _media_type_for_suffix
from openkb.ingest.normalizers.markdown import MarkdownNormalizer
from openkb.ingest.normalizers.markitdown import MarkItDownNormalizer
from openkb.ingest.normalizers.pdf import PdfNormalizer
from openkb.ingest.normalizers.text import PlainTextNormalizer
from openkb.ingest.plugins import load_ingest_entry_points
from openkb.ingest.registry import BundleNormalizerRegistry, SourceImporterRegistry
from openkb.ingest.render import _bundle_source_path, _render_image_markdown
from openkb.ingest.serialization import bundle_to_dict


def _context(tmp_path: Path, config: dict | None = None) -> IngestContext:
    return IngestContext(kb_dir=tmp_path, config=config or {}, staging_dir=tmp_path / "stage")


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return type(
        "Completed",
        (),
        {"stdout": stdout, "stderr": stderr, "returncode": returncode},
    )()


def test_resolve_ingest_pipeline_rejects_invalid_value():
    with pytest.raises(click.BadParameter):
        resolve_ingest_pipeline({"ingest": {"pipeline": "bogus"}})


def test_resolve_ingest_options_handles_invalid_shapes():
    options = resolve_ingest_options(
        {
            "ingest": {
                "max_depth": True,
                "max_documents": "0",
                "allow_domains": "example.com",
                "importers": {"enabled": ["file", 7, "file", "url"]},
                "normalizers": {"enabled": ["plugin"]},
                "enrichers": {"enabled": ["link_discovery"]},
            }
        }
    )

    assert options.max_depth == 0
    assert options.max_documents == 1
    assert options.allow_domains == ()
    assert options.enabled_importers == ("file", "url")
    assert options.enabled_normalizers == ("plugin",)
    assert options.link_discovery is True


def test_source_and_normalizer_registries_error_when_unhandled(tmp_path):
    context = _context(tmp_path)
    with pytest.raises(ValueError, match="No bundle source importer"):
        SourceImporterRegistry([]).resolve("missing", context)
    with pytest.raises(ValueError, match="No bundle normalizer"):
        BundleNormalizerRegistry([]).normalize(IngestInput(target="x"), context)


def test_add_one_skips_duplicate_target(tmp_path):
    state = _BundleAddState(seen_targets={str(tmp_path / "doc.md")})
    outcome, bundle = _add_one_bundle_target_locked(
        str(tmp_path / "doc.md"),
        tmp_path,
        "model",
        resolve_ingest_options({}),
        state,
    )
    assert outcome == "skipped"
    assert bundle is None


def test_add_one_errors_when_importer_returns_no_path(tmp_path):
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "hashes.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".openkb" / "config.yaml").write_text(
        "ingest:\n  importers:\n    enabled:\n      - virtual\n",
        encoding="utf-8",
    )

    class VirtualImporter:
        name = "virtual"

        def can_handle(self, target, context):
            del target, context
            return True

        def import_source(self, target, context):
            del context
            return IngestInput(target=target, source_uri=target)

    entry_points = _FakeEntryPoints(
        [_FakeEntryPoint("openkb.ingest.importers", "virtual", VirtualImporter)]
    )
    with (
        patch("openkb.ingest.add._setup_ingest_llm"),
        patch("openkb.ingest.plugins.entry_points", return_value=entry_points),
    ):
        outcome, bundle = _add_one_bundle_target_locked(
            "virtual://doc",
            tmp_path,
            "model",
            resolve_ingest_options({"ingest": {"importers": {"enabled": ["virtual"]}}}),
            _BundleAddState(seen_targets=set()),
        )

    assert outcome == "failed"
    assert bundle is None


def test_add_one_skips_known_hash(tmp_path):
    kb = tmp_path
    (kb / ".openkb").mkdir()
    (kb / ".openkb" / "hashes.json").write_text("{}", encoding="utf-8")
    doc = kb / "known.md"
    doc.write_text("# Known\n", encoding="utf-8")

    from openkb.state import HashRegistry

    registry = HashRegistry(kb / ".openkb" / "hashes.json")
    registry.add(HashRegistry.hash_file(doc), {"name": "known.md"})

    outcome, bundle = _add_one_bundle_target_locked(
        str(doc),
        kb,
        "model",
        resolve_ingest_options({}),
        _BundleAddState(seen_targets=set()),
    )
    assert outcome == "skipped"
    assert bundle is None


def test_with_parent_provenance_adds_record_when_missing():
    bundle = DocumentBundle(id="child", title=None, source_uri="child", blocks=[])
    out = _with_parent_provenance(bundle, "parent")
    assert out.provenance[0].relationship == "child"
    assert out.provenance[0].parent_uri == "parent"


def test_recursive_target_boundary_cases(tmp_path):
    bundle = DocumentBundle(
        id="root",
        title=None,
        source_uri="root",
        blocks=[],
        metadata={"source_path": str(tmp_path / "root.md")},
        links=[
            DiscoveredLink("mailto:a@example.com"),
            DiscoveredLink("missing.md"),
            DiscoveredLink("https://example.com/page#frag"),
        ],
    )
    options = resolve_ingest_options(
        {
            "ingest": {
                "allow_domains": ["example.com"],
                "importers": {"enabled": ["file", "url"]},
            }
        }
    )
    assert _allowed_recursive_targets(bundle, options) == ["https://example.com/page"]

    no_file_options = resolve_ingest_options({"ingest": {"importers": {"enabled": []}}})
    assert (
        _allowed_recursive_targets(
            DocumentBundle(
                id="root",
                title=None,
                source_uri="root",
                blocks=[],
                links=[DiscoveredLink("")],
            ),
            no_file_options,
        )
        == []
    )
    assert _domain_allowed("", ()) is False
    assert _summarize_outcomes(["skipped", "failed"]) == "failed"


def test_file_importer_directory_error_and_media_types(tmp_path):
    context = _context(tmp_path)
    assert FileImporter().can_handle(str(tmp_path), context)
    with pytest.raises(ValueError, match="requires a file"):
        FileImporter().import_source(str(tmp_path), context)

    expected = {
        "a.markdown": "text/markdown",
        "a.txt": "text/plain",
        "a.pdf": "application/pdf",
        "a.htm": "text/html",
        "a.csv": "text/csv",
        "a.png": "image/png",
        "a.jpeg": "image/jpeg",
        "a.gif": "image/gif",
        "a.webp": "image/webp",
        "a.bmp": "image/bmp",
        "a.bin": "application/octet-stream",
    }
    for name, media_type in expected.items():
        assert file_media_type_for(Path(name)) == media_type


def test_url_importer_failure_and_media_types(tmp_path):
    context = _context(tmp_path)
    assert UrlImporter().can_handle("https://example.com", context)
    with patch("openkb.ingest.importers.url.fetch_url_to_dir", return_value=None):
        with pytest.raises(ValueError, match="URL fetch failed"):
            UrlImporter().import_source("https://example.com", context)

    assert url_media_type_for(Path("x.markdown")) == "text/markdown"
    assert url_media_type_for(Path("x.pdf")) == "application/pdf"
    assert url_media_type_for(Path("x.htm")) == "text/html"
    assert url_media_type_for(Path("x.bin")) == "application/octet-stream"


def test_image_normalizer_boundaries(tmp_path):
    context = _context(tmp_path)
    normalizer = ImageNormalizer()
    assert not normalizer.supports(IngestInput(target="x", path=None), context)
    with pytest.raises(ValueError, match="requires a local file path"):
        normalizer.normalize(IngestInput(target="x"), context)

    for suffix, media_type in {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tif": "application/octet-stream",
    }.items():
        assert _media_type_for_suffix(suffix) == media_type


def test_other_normalizer_error_branches(tmp_path):
    context = _context(tmp_path)
    for normalizer in [
        MarkdownNormalizer(),
        PlainTextNormalizer(),
        PdfNormalizer(),
        MarkItDownNormalizer(),
    ]:
        with pytest.raises(ValueError, match="requires a local file path"):
            normalizer.normalize(IngestInput(target="x"), context)


def test_link_discovery_noop_and_trailing_punctuation(tmp_path):
    context = _context(tmp_path)
    bundle = DocumentBundle(
        id="b",
        title=None,
        source_uri="b",
        blocks=[ImageBlock(asset_id="missing")],
    )
    assert not LinkDiscoveryEnricher().applies_to(bundle, context)

    enriched = LinkDiscoveryEnricher().enrich(
        DocumentBundle(
            id="b",
            title=None,
            source_uri="b",
            blocks=[TableBlock("See https://example.com/a).")],
        ),
        context,
    )
    assert enriched.links[0].uri == "https://example.com/a"


def test_feishu_cli_error_boundaries(tmp_path):
    context = _context(tmp_path)
    assert not looks_like_feishu_url("not-a-url")

    with patch("openkb.ingest.importers.feishu.subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(ValueError, match="requires lark-cli"):
            _fetch_markdown_with_lark_cli("https://x.feishu.cn/wiki/a", context)

    with patch(
        "openkb.ingest.importers.feishu.subprocess.run",
        side_effect=subprocess.TimeoutExpired("lark-cli", 1),
    ):
        with pytest.raises(ValueError, match="timed out"):
            _fetch_markdown_with_lark_cli("https://x.feishu.cn/wiki/a", context)

    with patch(
        "openkb.ingest.importers.feishu.subprocess.run",
        return_value=_completed(stderr="denied", returncode=2),
    ):
        with pytest.raises(ValueError, match="denied"):
            _fetch_markdown_with_lark_cli("https://x.feishu.cn/wiki/a", context)

    with patch(
        "openkb.ingest.importers.feishu.subprocess.run",
        return_value=_completed(stdout='{"data": {"title": "Empty"}}'),
    ):
        with pytest.raises(ValueError, match="empty markdown"):
            _fetch_markdown_with_lark_cli("https://x.feishu.cn/wiki/a", context)


def test_feishu_parse_helpers_and_config(tmp_path):
    assert _parse_lark_cli_output("plain markdown") == ("plain markdown", None)
    nested = {"data": [{"node": {"body": "# Body", "name": "Nested"}}]}
    assert _parse_lark_cli_output(json.dumps(nested)) == ("# Body", "Nested")
    assert _find_first_string([{"x": ""}, {"title": "T"}], ("title",)) == "T"
    assert _int_config(True, default=5) == 5
    assert _int_config("bad", default=5) == 5
    assert _int_config("0", default=5) == 1
    assert _fallback_title("https://x.feishu.cn/") == "x.feishu.cn"

    context = _context(tmp_path, {"ingest": {"feishu": {"timeout": "2", "cli": "custom"}}})
    with patch(
        "openkb.ingest.importers.feishu.subprocess.run",
        return_value=_completed(stdout="# Body"),
    ) as run:
        assert _fetch_markdown_with_lark_cli("https://x.feishu.cn/wiki/a", context) == (
            "# Body",
            None,
        )
    assert run.call_args.kwargs["timeout"] == 2
    assert run.call_args.args[0][0] == "custom"


def test_feishu_importer_unique_fallback_name(tmp_path):
    context = _context(tmp_path)
    existing = context.staging_dir / "raw" / "abc.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("old", encoding="utf-8")
    with patch(
        "openkb.ingest.importers.feishu._fetch_markdown_with_lark_cli",
        return_value=("# Body", None),
    ):
        input_ = FeishuImporter().import_source("https://x.feishu.cn/wiki/abc", context)
    assert input_.path is not None
    assert input_.path.name == "abc_2.md"


def test_image_vision_parse_and_noop_boundaries(tmp_path):
    asset = tmp_path / "image.png"
    asset.write_bytes(b"img")
    bundle = DocumentBundle(
        id="b",
        title=None,
        source_uri="b",
        blocks=[TextBlock("text"), ImageBlock(asset_id="missing")],
        assets=[
            Asset(
                id="source",
                path=asset,
                media_type="image/png",
                sha256="hash",
            )
        ],
    )
    context = _context(tmp_path, {})
    enricher = ImageVisionEnricher()
    assert not enricher.applies_to(bundle, context)
    assert enricher.enrich(bundle, context) is bundle
    assert _image_model(context) == "gpt-4o-mini"

    parsed = _parse_vision_response("not json")
    assert parsed.visual_description == "not json"
    assert _parse_vision_response("[1]").visual_description == "[1]"
    fenced = _parse_vision_response(
        '```json\n{"visual_description": "desc", "ocr_text": 7, "keywords": ["a", 1]}\n```'
    )
    assert fenced.visual_description == "desc"
    assert fenced.ocr_text is None
    assert fenced.keywords == ["a"]
    assert _response_content({"content": "top"}) == "top"


def test_plugins_loader_boundaries():
    assert load_ingest_entry_points("group", ()) == []

    class Component:
        name = "component"

    class Factory:
        def __call__(self):
            return Component()

    entry_points = _FakeEntryPoints(
        [
            _FakeEntryPoint("group", "skip", Component),
            _FakeEntryPoint("group", "excluded", Component),
            _FakeEntryPoint("group", "factory", Factory()),
            _FakeEntryPoint("group", "bad", object()),
            _FakeEntryPoint("group", "boom", RuntimeError("boom"), raises=True),
        ]
    )
    with patch("openkb.ingest.plugins.entry_points", return_value=entry_points):
        loaded = load_ingest_entry_points("group", ("factory", "excluded"), exclude={"excluded"})
        assert [component.name for component in loaded] == ["component"]
        with pytest.raises(ValueError, match="did not produce"):
            load_ingest_entry_points("group", ("bad",))
        with pytest.raises(ValueError, match="boom"):
            load_ingest_entry_points("group", ("boom",))

    class LegacyEntryPoints(dict):
        pass

    with patch("openkb.ingest.plugins.entry_points", return_value=LegacyEntryPoints(group=[])):
        assert load_ingest_entry_points("group", ("x",)) == []


def test_render_and_serialization_boundaries(tmp_path):
    source = tmp_path / "image.png"
    source.write_bytes(b"img")
    images_dir = tmp_path / "images"
    bundle = DocumentBundle(
        id="b",
        title="Title",
        source_uri="b",
        blocks=[
            ImageBlock(
                asset_id="source",
                visual_description="desc",
                ocr_text="text",
            )
        ],
    )
    rendered = _render_image_markdown(bundle, source, "doc", images_dir)
    assert "## 视觉描述" in rendered
    assert "## 可见文字" in rendered

    with pytest.raises(ValueError, match="source_path"):
        _bundle_source_path(DocumentBundle(id="x", title=None, source_uri="x", blocks=[]))

    sidecar = bundle_to_dict(
        DocumentBundle(
            id="x",
            title=None,
            source_uri="x",
            blocks=[
                ImageBlock(asset_id="a"),
                TableBlock("| a |"),
                EmbedBlock("https://example.com", title="Example"),
            ],
            assets=[Asset("a", source, "image/png", "sha", metadata={"p": source})],
            links=[DiscoveredLink("u", metadata={"items": (source, object())})],
            metadata={1: object()},
            provenance=[ProvenanceRecord("x")],
        )
    )
    assert [block["type"] for block in sidecar["blocks"]] == ["image", "table", "embed"]
    assert sidecar["assets"][0]["metadata"]["p"].endswith("image.png")
    assert sidecar["links"][0]["metadata"]["items"][0].endswith("image.png")


@dataclass
class _FakeEntryPoint:
    group: str
    name: str
    loaded: object
    raises: bool = False

    def load(self):
        if self.raises:
            raise self.loaded
        return self.loaded


class _FakeEntryPoints(list):
    def select(self, *, group: str):
        return [entry_point for entry_point in self if entry_point.group == group]
