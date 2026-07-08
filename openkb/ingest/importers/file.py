"""Local filesystem source importer."""

from __future__ import annotations

from pathlib import Path

from openkb.converter import _registry_path
from openkb.ingest.context import IngestContext
from openkb.ingest.models import IngestInput


class FileImporter:
    name = "file"

    def can_handle(self, target: str, context: IngestContext) -> bool:
        del context
        return Path(target).expanduser().exists()

    def import_source(self, target: str, context: IngestContext) -> IngestInput:
        path = Path(target).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Bundle file importer requires a file: {target}")
        return IngestInput(
            target=target,
            path=path,
            source_uri=_registry_path(path, context.kb_dir),
            media_type=_media_type_for(path),
            metadata={"display_name": path.name},
        )


def _media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    if suffix == ".txt":
        return "text/plain"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in {".html", ".htm"}:
        return "text/html"
    if suffix == ".csv":
        return "text/csv"
    if suffix == ".png":
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".gif":
        return "image/gif"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".bmp":
        return "image/bmp"
    return "application/octet-stream"
