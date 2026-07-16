"""Deterministic link discovery for bundle ingest."""

from __future__ import annotations

import re
from dataclasses import replace

from openkb.ingest.context import IngestContext
from openkb.ingest.models import DiscoveredLink, DocumentBundle, TableBlock, TextBlock

_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
_BARE_URL_RE = re.compile(r"https?://[^\s<>)\]]+")


class LinkDiscoveryEnricher:
    name = "link_discovery"

    def applies_to(self, bundle: DocumentBundle, context: IngestContext) -> bool:
        del context
        return any(isinstance(block, (TextBlock, TableBlock)) for block in bundle.blocks)

    def enrich(self, bundle: DocumentBundle, context: IngestContext) -> DocumentBundle:
        del context
        discovered: list[DiscoveredLink] = list(bundle.links)
        seen = {link.uri for link in discovered}
        for block_index, text in enumerate(_text_parts(bundle)):
            block_id = f"block-{block_index}"
            for label, uri in _MARKDOWN_LINK_RE.findall(text):
                cleaned = _clean_link_uri(uri)
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                discovered.append(
                    DiscoveredLink(
                        uri=cleaned,
                        text=label.strip() or None,
                        source_block_id=block_id,
                        metadata={"source": "markdown"},
                    )
                )
            for match in _BARE_URL_RE.finditer(text):
                cleaned = _clean_link_uri(match.group(0))
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                discovered.append(
                    DiscoveredLink(
                        uri=cleaned,
                        source_block_id=block_id,
                        metadata={"source": "bare_url"},
                    )
                )
        return replace(bundle, links=discovered)


def _text_parts(bundle: DocumentBundle) -> list[str]:
    parts: list[str] = []
    for block in bundle.blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, TableBlock):
            parts.append(block.markdown)
    return parts


def _clean_link_uri(uri: str) -> str:
    cleaned = uri.strip().strip("<>").strip().rstrip(".,;:")
    if not cleaned or cleaned.startswith("#"):
        return ""
    return cleaned
