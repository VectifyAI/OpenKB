"""Bundle ingest orchestration and add integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

import click

from openkb.add_coordinator import AddMutationPlan, _cleanup_staging_dirs, run_add_mutation
from openkb.config import DEFAULT_CONFIG, load_config
from openkb.converter import _registry_path
from openkb.ingest.config import IngestOptions, resolve_ingest_options
from openkb.ingest.context import IngestContext
from openkb.ingest.enrichers import ImageVisionEnricher, LinkDiscoveryEnricher
from openkb.ingest.exceptions import LongDocumentFallback
from openkb.ingest.importers import FeishuImporter, FileImporter, UrlImporter
from openkb.ingest.importers.feishu import looks_like_feishu_url
from openkb.ingest.models import DiscoveredLink, DocumentBundle, ProvenanceRecord, RenderedBundle
from openkb.ingest.normalizers import (
    ImageNormalizer,
    MarkdownNormalizer,
    MarkItDownNormalizer,
    PdfNormalizer,
    PlainTextNormalizer,
)
from openkb.ingest.plugins import (
    ENRICHER_ENTRY_POINT_GROUP,
    IMPORTER_ENTRY_POINT_GROUP,
    NORMALIZER_ENTRY_POINT_GROUP,
    load_ingest_entry_points,
)
from openkb.ingest.registry import (
    BundleEnricher,
    BundleNormalizer,
    BundleNormalizerRegistry,
    SourceImporter,
    SourceImporterRegistry,
)
from openkb.ingest.render import render_bundle_to_staging
from openkb.locks import kb_ingest_lock
from openkb.log import append_log
from openkb.mutation import publish_staged_tree
from openkb.state import HashRegistry

logger = logging.getLogger(__name__)

IngestPipeline = Literal["legacy", "bundle"]
AddOutcome = Literal["added", "skipped", "failed"]
LegacyFallback = Callable[[Path, Path], AddOutcome]
_BUILTIN_IMPORTERS = {"file", "feishu", "url"}
_BUILTIN_NORMALIZERS = {"markdown", "text", "pdf-local", "markitdown", "image"}
_BUILTIN_ENRICHERS = {"link_discovery", "image_vision"}


@dataclass
class _BundleAddState:
    seen_targets: set[str]
    processed_documents: int = 0


def resolve_ingest_pipeline(config: dict, override: str | None = None) -> IngestPipeline:
    """Resolve the requested ingest pipeline with legacy as the compatibility default."""
    raw = override
    if raw is None:
        ingest = config.get("ingest")
        if isinstance(ingest, dict):
            raw = ingest.get("pipeline")
    if raw in (None, "", "legacy"):
        return "legacy"
    if raw == "bundle":
        return "bundle"
    raise click.BadParameter(
        "ingest pipeline must be one of: legacy, bundle",
        param_hint="'--ingest-pipeline'",
    )


def add_bundle_target(
    target: str | Path,
    kb_dir: Path,
    *,
    legacy_fallback: LegacyFallback | None = None,
) -> AddOutcome:
    """Import one target through the bundle pipeline under the KB mutation lock."""
    with kb_ingest_lock(kb_dir / ".openkb"):
        return _add_bundle_target_locked(str(target), kb_dir, legacy_fallback=legacy_fallback)


def _add_bundle_target_locked(
    target: str,
    kb_dir: Path,
    *,
    legacy_fallback: LegacyFallback | None = None,
) -> AddOutcome:
    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    _setup_ingest_llm(kb_dir)
    model: str = config.get("model", DEFAULT_CONFIG["model"])
    options = resolve_ingest_options(config)

    state = _BundleAddState(seen_targets=set())
    queue: list[tuple[str, int, str | None]] = [(target, 0, None)]
    outcomes: list[AddOutcome] = []
    while queue:
        current_target, depth, parent_uri = queue.pop(0)
        if state.processed_documents >= options.max_documents:
            click.echo(
                f"  [SKIP] Bundle recursive import reached max_documents={options.max_documents}."
            )
            break
        result, bundle = _add_one_bundle_target_locked(
            current_target,
            kb_dir,
            model,
            options,
            state,
            legacy_fallback=legacy_fallback,
            parent_uri=parent_uri,
        )
        outcomes.append(result)
        if result == "failed":
            return "failed"
        if bundle is None or not options.link_discovery or depth >= options.max_depth:
            continue
        for child in _allowed_recursive_targets(bundle, options):
            key = _target_key(child)
            if key in state.seen_targets:
                continue
            queue.append((str(child), depth + 1, bundle.source_uri))

    return _summarize_outcomes(outcomes)


def _add_one_bundle_target_locked(
    target: str,
    kb_dir: Path,
    model: str,
    options: IngestOptions,
    state: _BundleAddState,
    *,
    legacy_fallback: LegacyFallback | None = None,
    parent_uri: str | None = None,
) -> tuple[AddOutcome, DocumentBundle | None]:
    openkb_dir = kb_dir / ".openkb"

    context: IngestContext | None = None
    target_key = _target_key(target)
    if target_key in state.seen_targets:
        return "skipped", None
    state.seen_targets.add(target_key)
    state.processed_documents += 1

    click.echo(f"Adding: {_display_target(target)}")
    try:
        context = IngestContext.for_target(kb_dir, target)
        importer = SourceImporterRegistry(_source_importers(options)).resolve(target, context)
        input_ = importer.import_source(target, context)
        if input_.path is None:
            raise ValueError("Bundle file ingest requires a local file path.")

        file_hash = HashRegistry.hash_file(input_.path)
        registry = HashRegistry(openkb_dir / "hashes.json")
        if registry.is_known(file_hash):
            stored = registry.get(file_hash) or {}
            name = stored.get("name") or input_.path.name
            click.echo(f"  [SKIP] Already in knowledge base: {name}")
            _cleanup_staging_dirs([context.staging_dir])
            return "skipped", None

        bundle = BundleNormalizerRegistry(_normalizers(options)).normalize(input_, context)
        if parent_uri:
            bundle = _with_parent_provenance(bundle, parent_uri)
        bundle = _enrich_bundle(bundle, context, options)
        rendered = render_bundle_to_staging(bundle, context)
    except LongDocumentFallback as exc:
        if legacy_fallback is None:
            _cleanup_staging_dirs([context.staging_dir if context is not None else None])
            click.echo(f"  [ERROR] Bundle ingest requires legacy fallback: {exc.reason}")
            return "failed", None
        click.echo(f"  Falling back to legacy long-document ingest: {exc.reason}")
        try:
            outcome = legacy_fallback(exc.path, kb_dir)
        finally:
            _cleanup_staging_dirs([context.staging_dir if context is not None else None])
        return outcome, None
    except Exception as exc:
        click.echo(f"  [ERROR] Bundle ingest failed: {exc}")
        logger.debug("Bundle ingest traceback:", exc_info=True)
        _cleanup_staging_dirs([context.staging_dir if context is not None else None])
        return "failed", None

    if rendered.is_long_doc:
        click.echo("  [ERROR] Bundle long-document commit is not implemented yet.")
        _cleanup_staging_dirs([context.staging_dir])
        return "failed", None

    return _commit_rendered_bundle(rendered, kb_dir, model, context.staging_dir), bundle


def _source_importers(options: IngestOptions) -> list[SourceImporter]:
    importers: list[SourceImporter] = []
    if "file" in options.enabled_importers:
        importers.append(FileImporter())
    if "feishu" in options.enabled_importers:
        importers.append(FeishuImporter())
    if "url" in options.enabled_importers:
        importers.append(UrlImporter())
    importers.extend(
        load_ingest_entry_points(
            IMPORTER_ENTRY_POINT_GROUP,
            options.enabled_importers,
            exclude=_BUILTIN_IMPORTERS,
        )
    )
    return importers


def _normalizers(options: IngestOptions) -> list[BundleNormalizer]:
    normalizers: list[BundleNormalizer] = [
        MarkdownNormalizer(),
        PlainTextNormalizer(),
        PdfNormalizer(),
        MarkItDownNormalizer(),
        ImageNormalizer(),
    ]
    normalizers.extend(
        load_ingest_entry_points(
            NORMALIZER_ENTRY_POINT_GROUP,
            options.enabled_normalizers,
            exclude=_BUILTIN_NORMALIZERS,
        )
    )
    return normalizers


def _enrich_bundle(
    bundle: DocumentBundle,
    context: IngestContext,
    options: IngestOptions,
) -> DocumentBundle:
    enrichers: list[BundleEnricher] = []
    if options.link_discovery:
        enrichers.append(LinkDiscoveryEnricher())
    if "image_vision" in options.enabled_enrichers:
        enrichers.append(ImageVisionEnricher())
    enrichers.extend(
        load_ingest_entry_points(
            ENRICHER_ENTRY_POINT_GROUP,
            options.enabled_enrichers,
            exclude=_BUILTIN_ENRICHERS,
        )
    )
    for enricher in enrichers:
        if enricher.applies_to(bundle, context):
            bundle = enricher.enrich(bundle, context)
    return bundle


def _with_parent_provenance(bundle: DocumentBundle, parent_uri: str) -> DocumentBundle:
    if not bundle.provenance:
        return replace(
            bundle,
            provenance=[
                ProvenanceRecord(
                    source_uri=bundle.source_uri,
                    relationship="child",
                    parent_uri=parent_uri,
                    metadata={"parent_uri": parent_uri},
                )
            ],
        )
    first = bundle.provenance[0]
    metadata = {**first.metadata, "parent_uri": parent_uri}
    provenance = [
        replace(first, relationship="child", parent_uri=parent_uri, metadata=metadata),
        *bundle.provenance[1:],
    ]
    return replace(bundle, provenance=provenance)


def _allowed_recursive_targets(bundle: DocumentBundle, options: IngestOptions) -> list[str | Path]:
    targets: list[str | Path] = []
    for link in bundle.links:
        target = _resolve_recursive_target(link, bundle, options)
        if target is not None:
            targets.append(target)
    return targets


def _resolve_recursive_target(
    link: DiscoveredLink,
    bundle: DocumentBundle,
    options: IngestOptions,
) -> str | Path | None:
    parsed = urlparse(link.uri)
    if parsed.scheme in {"http", "https"}:
        if not _network_importer_enabled(link.uri, options):
            return None
        if not _domain_allowed(parsed.netloc, options.allow_domains):
            return None
        return _strip_url_fragment(link.uri)
    if parsed.scheme and parsed.scheme != "file":
        return None
    if "file" not in options.enabled_importers:
        return None

    raw_path = unquote(parsed.path if parsed.scheme == "file" else _strip_fragment(link.uri))
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        source_path = bundle.metadata.get("source_path")
        if not isinstance(source_path, str) or not source_path:
            return None
        path = Path(source_path).parent / path
    resolved = path.resolve()
    return resolved if resolved.is_file() else None


def _strip_fragment(uri: str) -> str:
    return uri.split("#", 1)[0].split("?", 1)[0].strip()


def _strip_url_fragment(uri: str) -> str:
    return uri.split("#", 1)[0].strip()


def _network_importer_enabled(uri: str, options: IngestOptions) -> bool:
    if looks_like_feishu_url(uri) and "feishu" in options.enabled_importers:
        return True
    return "url" in options.enabled_importers


def _domain_allowed(netloc: str, allow_domains: tuple[str, ...]) -> bool:
    if not allow_domains:
        return False
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower()
    for domain in allow_domains:
        cleaned = domain.lower().strip()
        if host == cleaned or host.endswith(f".{cleaned}"):
            return True
    return False


def _target_key(target: str | Path) -> str:
    path = Path(str(target)).expanduser()
    if path.exists():
        return path.resolve().as_posix()
    return str(target)


def _display_target(target: str) -> str:
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https"}:
        return target
    return Path(target).name


def _summarize_outcomes(outcomes: list[AddOutcome]) -> AddOutcome:
    if any(outcome == "added" for outcome in outcomes):
        return "added"
    if any(outcome == "failed" for outcome in outcomes):
        return "failed"
    return "skipped"


def _commit_rendered_bundle(
    rendered: RenderedBundle,
    kb_dir: Path,
    model: str,
    staging_dir: Path,
) -> AddOutcome:
    final_raw, final_source, final_bundle = _final_artifact_paths(rendered, kb_dir)

    def commit_body(_snapshot) -> None:
        publish_staged_tree(staging_dir, kb_dir)
        if rendered.source_path is None or final_source is None:
            raise RuntimeError(f"Rendered bundle has no source artifact: {rendered.display_name}")

        _compile_short_doc(rendered.doc_name, final_source, kb_dir, model)

        registry = HashRegistry(kb_dir / ".openkb" / "hashes.json")
        meta = {
            "name": rendered.display_name,
            "doc_name": rendered.doc_name,
            "type": rendered.doc_type,
            "path": rendered.source_identity,
        }
        if final_raw is not None:
            meta["raw_path"] = _registry_path(final_raw, kb_dir)
        meta["source_path"] = _registry_path(final_source, kb_dir)
        if final_bundle is not None:
            meta["bundle_path"] = _registry_path(final_bundle, kb_dir)

        registry.remove_by_doc_name(rendered.doc_name)
        for existing_hash, existing_meta in list(registry.all_entries().items()):
            if (
                existing_hash != rendered.content_hash
                and not existing_meta.get("doc_name")
                and existing_meta.get("name") == rendered.display_name
            ):
                registry.remove_by_hash(existing_hash)
        registry.add(rendered.content_hash, meta)

    def append_ingest_log() -> None:
        append_log(kb_dir / "wiki", "ingest", rendered.display_name)

    plan = AddMutationPlan(
        operation="add",
        details={
            "file_hash": rendered.content_hash,
            "name": rendered.display_name,
            "doc_name": rendered.doc_name,
        },
        touched_paths=_snapshot_add_paths(
            kb_dir,
            rendered.doc_name,
            final_raw,
            final_source,
            final_bundle,
        ),
        body=commit_body,
        post_commit_hooks=[append_ingest_log],
        hardlink_dirs={
            kb_dir / "wiki" / "concepts",
            kb_dir / "wiki" / "entities",
        },
        staging_dirs=[staging_dir],
    )
    if not run_add_mutation(kb_dir, plan):
        return "failed"
    click.echo(f"  [OK] {rendered.display_name} added to knowledge base.")
    return "added"


def _setup_ingest_llm(kb_dir: Path) -> None:
    from openkb.cli import _setup_llm_key

    _setup_llm_key(kb_dir)


def _compile_short_doc(doc_name: str, source_path: Path, kb_dir: Path, model: str) -> None:
    from openkb.agent.compiler import compile_short_doc
    from openkb.cli import _run_compile_with_retry

    _run_compile_with_retry(
        lambda: compile_short_doc(doc_name, source_path, kb_dir, model),
        label="Compiling short doc",
    )


def _final_artifact_paths(
    rendered: RenderedBundle,
    kb_dir: Path,
) -> tuple[Path | None, Path | None, Path | None]:
    final_raw = None
    final_source = None
    final_bundle = None
    if rendered.raw_path is not None:
        final_raw = kb_dir / "raw" / rendered.raw_path.name
    if rendered.source_path is not None:
        final_source = kb_dir / "wiki" / "sources" / rendered.source_path.name
    if rendered.bundle_path is not None:
        final_bundle = kb_dir / ".openkb" / "bundles" / rendered.bundle_path.name
    return final_raw, final_source, final_bundle


def _snapshot_add_paths(
    kb_dir: Path,
    doc_name: str,
    final_raw: Path | None,
    final_source: Path | None,
    final_bundle: Path | None,
) -> list[Path]:
    paths = [
        kb_dir / ".openkb" / "hashes.json",
        kb_dir / ".openkb" / "pageindex.db",
        kb_dir / ".openkb" / "pageindex.db-wal",
        kb_dir / ".openkb" / "pageindex.db-shm",
        kb_dir / ".openkb" / "pageindex.db-journal",
        kb_dir / "wiki" / "summaries" / f"{doc_name}.md",
        kb_dir / "wiki" / "sources" / f"{doc_name}.json",
        kb_dir / "wiki" / "sources" / "images" / doc_name,
        kb_dir / "wiki" / "concepts",
        kb_dir / "wiki" / "entities",
        kb_dir / "wiki" / "index.md",
        kb_dir / "wiki" / "log.md",
    ]
    if final_raw is not None:
        paths.append(final_raw)
    if final_source is not None:
        paths.append(final_source)
    if final_bundle is not None:
        paths.append(final_bundle)
    return paths
