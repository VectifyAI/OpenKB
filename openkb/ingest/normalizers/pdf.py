"""PDF normalizer for short local PDFs."""

from __future__ import annotations

from openkb.converter import get_pdf_page_count
from openkb.ingest.context import IngestContext
from openkb.ingest.exceptions import LongDocumentFallback
from openkb.ingest.models import DocumentBundle, IngestInput, ProvenanceRecord


class PdfNormalizer:
    name = "pdf-local"

    def supports(self, input_: IngestInput, context: IngestContext) -> bool:
        del context
        return input_.path is not None and input_.path.suffix.lower() == ".pdf"

    def normalize(self, input_: IngestInput, context: IngestContext) -> DocumentBundle:
        if input_.path is None:
            raise ValueError("PDF normalizer requires a local file path.")
        threshold = int(context.config.get("pageindex_threshold", 20))
        page_count = get_pdf_page_count(input_.path)
        if page_count >= threshold:
            raise LongDocumentFallback(
                input_.path,
                f"PDF has {page_count} pages >= pageindex_threshold {threshold}",
            )
        source_uri = input_.source_uri or input_.path.as_posix()
        return DocumentBundle(
            id=source_uri,
            title=input_.path.stem,
            source_uri=source_uri,
            blocks=[],
            metadata={
                "display_name": input_.metadata.get("display_name", input_.path.name),
                "source_path": input_.path.as_posix(),
                "source_suffix": ".pdf",
                "source_identity": source_uri,
                "render_strategy": "pdf",
            },
            provenance=[ProvenanceRecord(source_uri=source_uri)],
        )
