"""Serialization helpers for persisted ingest bundle sidecars."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openkb.ingest.models import (
    Asset,
    DiscoveredLink,
    DocumentBundle,
    EmbedBlock,
    ImageBlock,
    ProvenanceRecord,
    TableBlock,
    TextBlock,
)
from openkb.locks import atomic_write_json

_SCHEMA_VERSION = 1


def write_bundle_json(bundle: DocumentBundle, path: Path) -> None:
    atomic_write_json(path, bundle_to_dict(bundle))


def bundle_to_dict(bundle: DocumentBundle) -> dict[str, Any]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "id": bundle.id,
        "title": bundle.title,
        "source_uri": bundle.source_uri,
        "blocks": [_block_to_dict(block) for block in bundle.blocks],
        "assets": [_asset_to_dict(asset) for asset in bundle.assets],
        "links": [_link_to_dict(link) for link in bundle.links],
        "metadata": _jsonable(bundle.metadata),
        "provenance": [_provenance_to_dict(record) for record in bundle.provenance],
    }


def _block_to_dict(block) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {
            "type": "text",
            "text": block.text,
            "page": block.page,
            "anchor": block.anchor,
            "metadata": _jsonable(block.metadata),
        }
    if isinstance(block, ImageBlock):
        return {
            "type": "image",
            "asset_id": block.asset_id,
            "caption": block.caption,
            "ocr_text": block.ocr_text,
            "visual_description": block.visual_description,
            "page": block.page,
            "metadata": _jsonable(block.metadata),
        }
    if isinstance(block, TableBlock):
        return {
            "type": "table",
            "markdown": block.markdown,
            "page": block.page,
            "metadata": _jsonable(block.metadata),
        }
    if isinstance(block, EmbedBlock):
        return {
            "type": "embed",
            "uri": block.uri,
            "title": block.title,
            "metadata": _jsonable(block.metadata),
        }
    raise TypeError(f"Unsupported bundle block: {type(block).__name__}")


def _asset_to_dict(asset: Asset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "path": asset.path.as_posix(),
        "media_type": asset.media_type,
        "sha256": asset.sha256,
        "source_uri": asset.source_uri,
        "metadata": _jsonable(asset.metadata),
    }


def _link_to_dict(link: DiscoveredLink) -> dict[str, Any]:
    return {
        "uri": link.uri,
        "text": link.text,
        "source_block_id": link.source_block_id,
        "confidence": link.confidence,
        "metadata": _jsonable(link.metadata),
    }


def _provenance_to_dict(record: ProvenanceRecord) -> dict[str, Any]:
    return {
        "source_uri": record.source_uri,
        "relationship": record.relationship,
        "parent_uri": record.parent_uri,
        "metadata": _jsonable(record.metadata),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
