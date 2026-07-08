"""MarkItDown-backed normalizer for Office, HTML, and CSV-like files."""

from __future__ import annotations

from openkb.ingest.context import IngestContext
from openkb.ingest.models import DocumentBundle, IngestInput, ProvenanceRecord

_MARKITDOWN_EXTENSIONS = {
    ".docx",
    ".pptx",
    ".xlsx",
    ".xls",
    ".html",
    ".htm",
    ".csv",
}


class MarkItDownNormalizer:
    name = "markitdown"

    def supports(self, input_: IngestInput, context: IngestContext) -> bool:
        del context
        return input_.path is not None and input_.path.suffix.lower() in _MARKITDOWN_EXTENSIONS

    def normalize(self, input_: IngestInput, context: IngestContext) -> DocumentBundle:
        del context
        if input_.path is None:
            raise ValueError("MarkItDown normalizer requires a local file path.")
        source_uri = input_.source_uri or input_.path.as_posix()
        return DocumentBundle(
            id=source_uri,
            title=input_.path.stem,
            source_uri=source_uri,
            blocks=[],
            metadata={
                "display_name": input_.metadata.get("display_name", input_.path.name),
                "source_path": input_.path.as_posix(),
                "source_suffix": input_.path.suffix.lower(),
                "source_identity": source_uri,
                "render_strategy": "markitdown",
            },
            provenance=[ProvenanceRecord(source_uri=source_uri)],
        )
