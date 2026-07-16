"""Plain text normalizer for local files."""

from __future__ import annotations

from openkb.ingest.context import IngestContext
from openkb.ingest.models import DocumentBundle, IngestInput, ProvenanceRecord, TextBlock


class PlainTextNormalizer:
    name = "text"

    def supports(self, input_: IngestInput, context: IngestContext) -> bool:
        del context
        return input_.path is not None and input_.path.suffix.lower() == ".txt"

    def normalize(self, input_: IngestInput, context: IngestContext) -> DocumentBundle:
        del context
        if input_.path is None:
            raise ValueError("Text normalizer requires a local file path.")
        source_uri = input_.source_uri or input_.path.as_posix()
        return DocumentBundle(
            id=source_uri,
            title=input_.path.stem,
            source_uri=source_uri,
            blocks=[TextBlock(text=input_.path.read_text(encoding="utf-8"))],
            metadata={
                "display_name": input_.metadata.get("display_name", input_.path.name),
                "source_path": input_.path.as_posix(),
                "source_suffix": input_.path.suffix.lower(),
                "source_identity": source_uri,
            },
            provenance=[ProvenanceRecord(source_uri=source_uri)],
        )
