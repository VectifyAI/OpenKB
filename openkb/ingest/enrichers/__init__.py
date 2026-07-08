"""Built-in bundle enrichers."""

from __future__ import annotations

from openkb.ingest.enrichers.image_vision import ImageVisionEnricher
from openkb.ingest.enrichers.link_discovery import LinkDiscoveryEnricher

__all__ = ["ImageVisionEnricher", "LinkDiscoveryEnricher"]
