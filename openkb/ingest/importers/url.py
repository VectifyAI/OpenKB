"""HTTP(S) source importer for bundle ingest."""

from __future__ import annotations

from pathlib import Path

from openkb.ingest.context import IngestContext
from openkb.ingest.models import IngestInput
from openkb.url_ingest import fetch_url_to_dir, looks_like_url


class UrlImporter:
    name = "url"

    def can_handle(self, target: str, context: IngestContext) -> bool:
        del context
        return looks_like_url(target)

    def import_source(self, target: str, context: IngestContext) -> IngestInput:
        raw_dir = context.staging_dir / "raw"
        path = fetch_url_to_dir(target, raw_dir)
        if path is None:
            raise ValueError(f"URL fetch failed: {target}")
        return IngestInput(
            target=target,
            path=path,
            source_uri=target,
            media_type=_media_type_for(path),
            metadata={"display_name": path.name},
        )


def _media_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return "text/markdown"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in {".html", ".htm"}:
        return "text/html"
    return "application/octet-stream"
