from __future__ import annotations

from openkb.ingest.models import DocumentBundle, IngestInput, ProvenanceRecord, TextBlock


class ExampleTextImporter:
    name = "example_text"

    def can_handle(self, target: str, context) -> bool:
        del context
        return target.startswith("example-text:")

    def import_source(self, target: str, context) -> IngestInput:
        path = context.staging_dir / "raw" / "example.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(target.removeprefix("example-text:").strip() + "\n", encoding="utf-8")
        return IngestInput(
            target=target,
            path=path,
            source_uri=target,
            media_type="text/x-example",
            metadata={"display_name": "example.txt"},
        )


class ExampleTextNormalizer:
    name = "example_text"

    def supports(self, input_: IngestInput, context) -> bool:
        del context
        return input_.media_type == "text/x-example"

    def normalize(self, input_: IngestInput, context) -> DocumentBundle:
        del context
        if input_.path is None:
            raise ValueError("Example normalizer requires a local path.")
        source_uri = input_.source_uri or input_.target
        text = input_.path.read_text(encoding="utf-8")
        return DocumentBundle(
            id=source_uri,
            title="Example Text",
            source_uri=source_uri,
            blocks=[TextBlock(text)],
            metadata={
                "display_name": input_.metadata.get("display_name", input_.path.name),
                "source_path": input_.path.as_posix(),
                "source_identity": source_uri,
            },
            provenance=[ProvenanceRecord(source_uri=source_uri)],
        )
