"""Render document bundles into staged OpenKB-compatible artifacts."""

from __future__ import annotations

import shutil
from pathlib import Path

from markitdown import MarkItDown

from openkb.converter import _registry_path, resolve_doc_name
from openkb.images import convert_pdf_with_images, copy_relative_images, extract_base64_images
from openkb.ingest.context import IngestContext
from openkb.ingest.models import DocumentBundle, ImageBlock, RenderedBundle, TableBlock, TextBlock
from openkb.ingest.serialization import write_bundle_json
from openkb.locks import atomic_write_text
from openkb.state import HashRegistry


def render_bundle_to_staging(bundle: DocumentBundle, context: IngestContext) -> RenderedBundle:
    """Render a short-document bundle into staged raw/source artifacts."""
    source_path = _bundle_source_path(bundle)
    registry = HashRegistry(context.kb_dir / ".openkb" / "hashes.json")
    file_hash = HashRegistry.hash_file(source_path)
    doc_name = resolve_doc_name(
        source_path,
        context.kb_dir,
        registry,
        persist_legacy=False,
    )

    raw_dir = context.staging_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_dest = raw_dir / f"{doc_name}{source_path.suffix.lower()}"
    if source_path.resolve() != raw_dest.resolve():
        shutil.copy2(source_path, raw_dest)

    images_dir = context.staging_dir / "wiki" / "sources" / "images" / doc_name
    strategy = bundle.metadata.get("render_strategy")
    if strategy == "pdf":
        markdown = convert_pdf_with_images(source_path, doc_name, images_dir)
    elif strategy == "markitdown":
        mid = MarkItDown()
        result = mid.convert(str(source_path), keep_data_uris=True)
        markdown = extract_base64_images(result.text_content, doc_name, images_dir)
    elif strategy == "image":
        markdown = _render_image_markdown(bundle, source_path, doc_name, images_dir)
    else:
        markdown = _render_short_markdown(bundle)
        markdown = copy_relative_images(markdown, source_path.parent, doc_name, images_dir)

    sources_dir = context.staging_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    source_dest = sources_dir / f"{doc_name}.md"
    atomic_write_text(source_dest, markdown)

    bundles_dir = context.staging_dir / ".openkb" / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    bundle_dest = bundles_dir / f"{doc_name}.json"
    write_bundle_json(bundle, bundle_dest)

    return RenderedBundle(
        doc_name=doc_name,
        display_name=str(bundle.metadata.get("display_name") or source_path.name),
        source_identity=str(
            bundle.metadata.get("source_identity") or _registry_path(source_path, context.kb_dir)
        ),
        content_hash=file_hash,
        raw_path=raw_dest,
        source_path=source_dest,
        doc_type=source_path.suffix.lower().lstrip(".") or "md",
        bundle_path=bundle_dest,
    )


def _bundle_source_path(bundle: DocumentBundle) -> Path:
    raw = bundle.metadata.get("source_path")
    if not isinstance(raw, str) or not raw:
        raise ValueError("Bundle renderer requires metadata['source_path'].")
    path = Path(raw)
    if not path.is_file():
        raise ValueError(f"Bundle source path does not exist: {path}")
    return path


def _render_short_markdown(bundle: DocumentBundle) -> str:
    parts: list[str] = []
    for block in bundle.blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, TableBlock):
            parts.append(block.markdown)
    return "\n\n".join(part.strip("\n") for part in parts if part).rstrip() + "\n"


def _render_image_markdown(
    bundle: DocumentBundle,
    source_path: Path,
    doc_name: str,
    images_dir: Path,
) -> str:
    images_dir.mkdir(parents=True, exist_ok=True)
    image_dest = images_dir / source_path.name
    if source_path.resolve() != image_dest.resolve():
        shutil.copy2(source_path, image_dest)
    title = bundle.title or source_path.stem
    parts = [
        f"# {title}",
        f"![source image](sources/images/{doc_name}/{image_dest.name})",
    ]
    for block in bundle.blocks:
        if not isinstance(block, ImageBlock):
            continue
        if block.visual_description:
            parts.append(
                "## 视觉描述\n\n"
                "> 模型派生信息，可能不完整或不准确。\n\n"
                f"{block.visual_description.strip()}"
            )
        if block.ocr_text:
            parts.append(f"## 可见文字\n\n{block.ocr_text.strip()}")
    return "\n\n".join(parts).rstrip() + "\n"
