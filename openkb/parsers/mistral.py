from __future__ import annotations

import base64
import logging
import os
import re
from pathlib import Path
from typing import Any

from openkb.parsers.base import ParseResult, Parser

logger = logging.getLogger(__name__)

_SUPPORTED = {".pdf"}
_DATA_URI_RE = re.compile(r"^data:[^;]+;base64,", re.IGNORECASE)


class MistralParser(Parser):
    """Mistral OCR (Document AI). Synchronous; markdown + base64 images."""

    name = "mistral"

    def __init__(self, opts: dict[str, Any] | None = None):
        self.opts = opts or {}
        self.model = self.opts.get("model", "mistral-ocr-latest")

    def supports(self, suffix: str) -> bool:
        return suffix.lower() in _SUPPORTED

    def parse(self, src: Path) -> ParseResult:
        api_key = os.environ.get("MISTRAL_API_KEY")
        if not api_key:
            raise RuntimeError(
                "Mistral parser requires the MISTRAL_API_KEY environment variable."
            )
        try:
            from mistralai import Mistral
        except ImportError as exc:
            raise RuntimeError(
                "Mistral parser requires the 'mistralai' package. "
                "Install with: pip install openkb[mistral]"
            ) from exc

        client = Mistral(api_key=api_key)
        uploaded = client.files.upload(
            file={"file_name": src.name, "content": src.read_bytes()}, purpose="ocr"
        )
        signed = client.files.get_signed_url(file_id=uploaded.id)
        resp = client.ocr.process(
            model=self.model,
            document={"type": "document_url", "document_url": signed.url},
            include_image_base64=True,
        )

        parts: list[str] = []
        images: dict[str, bytes] = {}
        for page in resp.pages:
            parts.append(page.markdown or "")
            for img in getattr(page, "images", None) or []:
                raw = img.image_base64 or ""
                raw = _DATA_URI_RE.sub("", raw)
                try:
                    images[img.id] = base64.b64decode(raw, validate=True)
                except Exception:
                    logger.warning("Skipping undecodable Mistral image: %s", getattr(img, "id", "?"))
                    continue
        return ParseResult(markdown="\n\n".join(parts), images=images)
