"""Data models for the bundle ingest pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class IngestInput:
    """Raw source payload produced by a source importer."""

    target: str
    path: Path | None = None
    source_uri: str | None = None
    media_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextBlock:
    text: str
    page: int | None = None
    anchor: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ImageBlock:
    asset_id: str
    caption: str | None = None
    ocr_text: str | None = None
    visual_description: str | None = None
    page: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TableBlock:
    markdown: str
    page: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbedBlock:
    uri: str
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


Block = TextBlock | ImageBlock | TableBlock | EmbedBlock


@dataclass(frozen=True)
class Asset:
    id: str
    path: Path
    media_type: str
    sha256: str
    source_uri: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveredLink:
    uri: str
    text: str | None = None
    source_block_id: str | None = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProvenanceRecord:
    source_uri: str
    relationship: str = "root"
    parent_uri: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentBundle:
    id: str
    title: str | None
    source_uri: str
    blocks: list[Block]
    assets: list[Asset] = field(default_factory=list)
    links: list[DiscoveredLink] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: list[ProvenanceRecord] = field(default_factory=list)


@dataclass(frozen=True)
class RenderedBundle:
    """Staged artifacts compatible with OpenKB's existing add commit path."""

    doc_name: str
    display_name: str
    source_identity: str
    content_hash: str
    raw_path: Path | None
    source_path: Path | None
    doc_type: str
    bundle_path: Path | None = None
    is_long_doc: bool = False
    kind: Literal["short", "long"] = "short"
