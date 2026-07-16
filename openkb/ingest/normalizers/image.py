"""Image normalizer for local image files."""

from __future__ import annotations

from openkb.ingest.context import IngestContext
from openkb.ingest.models import Asset, DocumentBundle, ImageBlock, IngestInput, ProvenanceRecord
from openkb.state import HashRegistry

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class ImageNormalizer:
    name = "image"

    def supports(self, input_: IngestInput, context: IngestContext) -> bool:
        del context
        return input_.path is not None and input_.path.suffix.lower() in IMAGE_EXTENSIONS

    def normalize(self, input_: IngestInput, context: IngestContext) -> DocumentBundle:
        del context
        if input_.path is None:
            raise ValueError("Image normalizer requires a local file path.")
        source_uri = input_.source_uri or input_.path.as_posix()
        media_type = input_.media_type or _media_type_for_suffix(input_.path.suffix)
        image_hash = HashRegistry.hash_file(input_.path)
        asset = Asset(
            id="source-image",
            path=input_.path,
            media_type=media_type,
            sha256=image_hash,
            source_uri=source_uri,
        )
        return DocumentBundle(
            id=source_uri,
            title=input_.path.stem,
            source_uri=source_uri,
            blocks=[ImageBlock(asset_id=asset.id, caption=input_.path.stem)],
            assets=[asset],
            metadata={
                "display_name": input_.metadata.get("display_name", input_.path.name),
                "source_path": input_.path.as_posix(),
                "source_suffix": input_.path.suffix.lower(),
                "source_identity": source_uri,
                "render_strategy": "image",
            },
            provenance=[ProvenanceRecord(source_uri=source_uri)],
        )


def _media_type_for_suffix(suffix: str) -> str:
    normalized = suffix.lower()
    if normalized in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if normalized == ".png":
        return "image/png"
    if normalized == ".gif":
        return "image/gif"
    if normalized == ".webp":
        return "image/webp"
    if normalized == ".bmp":
        return "image/bmp"
    return "application/octet-stream"
