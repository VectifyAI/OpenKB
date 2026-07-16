"""Markdown normalizer for local files."""

from __future__ import annotations

from openkb.ingest.context import IngestContext
from openkb.ingest.models import DocumentBundle, IngestInput, ProvenanceRecord, TextBlock


class MarkdownNormalizer:
    name = "markdown"

    def supports(self, input_: IngestInput, context: IngestContext) -> bool:
        del context
        if input_.path is None:
            return False
        return input_.path.suffix.lower() in {".md", ".markdown"}

    def normalize(self, input_: IngestInput, context: IngestContext) -> DocumentBundle:
        del context
        if input_.path is None:
            raise ValueError("Markdown normalizer requires a local file path.")
        source_uri = input_.source_uri or input_.path.as_posix()
        input_metadata = dict(input_.metadata)
        title = input_metadata.get("title")
        if not isinstance(title, str) or not title.strip():
            title = input_.path.stem
        return DocumentBundle(
            id=source_uri,
            title=title.strip(),
            source_uri=source_uri,
            blocks=[
                TextBlock(
                    text=input_.path.read_text(encoding="utf-8"),
                    metadata={"source_dir": input_.path.parent.as_posix()},
                )
            ],
            metadata={
                "display_name": input_.metadata.get("display_name", input_.path.name),
                "source_path": input_.path.as_posix(),
                "source_suffix": input_.path.suffix.lower(),
                "source_identity": source_uri,
                "source_metadata": input_metadata,
            },
            provenance=[ProvenanceRecord(source_uri=source_uri)],
        )
