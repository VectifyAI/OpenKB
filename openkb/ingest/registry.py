"""Small in-process registries for built-in ingest components."""

from __future__ import annotations

from typing import Protocol

from openkb.ingest.context import IngestContext
from openkb.ingest.models import DocumentBundle, IngestInput


class SourceImporter(Protocol):
    name: str

    def can_handle(self, target: str, context: IngestContext) -> bool: ...
    def import_source(self, target: str, context: IngestContext) -> IngestInput: ...


class BundleNormalizer(Protocol):
    name: str

    def supports(self, input_: IngestInput, context: IngestContext) -> bool: ...
    def normalize(self, input_: IngestInput, context: IngestContext) -> DocumentBundle: ...


class BundleEnricher(Protocol):
    name: str

    def applies_to(self, bundle: DocumentBundle, context: IngestContext) -> bool: ...
    def enrich(self, bundle: DocumentBundle, context: IngestContext) -> DocumentBundle: ...


class SourceImporterRegistry:
    def __init__(self, importers: list[SourceImporter]) -> None:
        self._importers = importers

    def resolve(self, target: str, context: IngestContext) -> SourceImporter:
        for importer in self._importers:
            if importer.can_handle(target, context):
                return importer
        raise ValueError(f"No bundle source importer can handle: {target}")


class BundleNormalizerRegistry:
    def __init__(self, normalizers: list[BundleNormalizer]) -> None:
        self._normalizers = normalizers

    def normalize(self, input_: IngestInput, context: IngestContext) -> DocumentBundle:
        for normalizer in self._normalizers:
            if normalizer.supports(input_, context):
                return normalizer.normalize(input_, context)
        source = input_.path or input_.source_uri or input_.target
        raise ValueError(f"No bundle normalizer can handle: {source}")
