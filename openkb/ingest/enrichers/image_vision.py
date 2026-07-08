"""Image OCR and visual-description enricher."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, replace
from typing import Any

from openkb.ingest.context import IngestContext
from openkb.ingest.models import Asset, Block, DocumentBundle, ImageBlock


@dataclass(frozen=True)
class ImageVisionResult:
    visual_description: str | None = None
    ocr_text: str | None = None
    keywords: list[str] | None = None
    uncertainty: str | None = None


class ImageVisionEnricher:
    name = "image_vision"

    def applies_to(self, bundle: DocumentBundle, context: IngestContext) -> bool:
        del context
        asset_ids = {asset.id for asset in bundle.assets}
        return any(
            isinstance(block, ImageBlock) and block.asset_id in asset_ids for block in bundle.blocks
        )

    def enrich(self, bundle: DocumentBundle, context: IngestContext) -> DocumentBundle:
        assets = {asset.id: asset for asset in bundle.assets}
        model = _image_model(context)
        blocks: list[Block] = []
        changed = False
        for block in bundle.blocks:
            if not isinstance(block, ImageBlock):
                blocks.append(block)
                continue
            asset = assets.get(block.asset_id)
            if asset is None:
                blocks.append(block)
                continue
            result = _describe_image(asset, model)
            metadata = {
                **block.metadata,
                "visual_description_derived": True,
                "visual_description_model": model,
            }
            if result.keywords:
                metadata["keywords"] = result.keywords
            if result.uncertainty:
                metadata["uncertainty"] = result.uncertainty
            blocks.append(
                replace(
                    block,
                    visual_description=result.visual_description,
                    ocr_text=result.ocr_text,
                    metadata=metadata,
                )
            )
            changed = True
        if not changed:
            return bundle
        return replace(
            bundle,
            blocks=blocks,
            metadata={**bundle.metadata, "image_vision_model": model},
        )


def _describe_image(asset: Asset, model: str) -> ImageVisionResult:
    import litellm

    image_bytes = asset.path.read_bytes()
    encoded = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{asset.media_type};base64,{encoded}"
    response = litellm.completion(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Describe this image for a knowledge base. Return JSON with "
                            "visual_description, ocr_text, keywords, and uncertainty. "
                            "Use null when a field is not visible or not applicable."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
    )
    return _parse_vision_response(_response_content(response))


def _parse_vision_response(content: str) -> ImageVisionResult:
    cleaned = _strip_code_fence(content.strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return ImageVisionResult(visual_description=cleaned or None)
    if not isinstance(payload, dict):
        return ImageVisionResult(visual_description=cleaned or None)
    keywords = payload.get("keywords")
    parsed_keywords = (
        [item for item in keywords if isinstance(item, str)] if isinstance(keywords, list) else None
    )
    return ImageVisionResult(
        visual_description=_optional_string(payload.get("visual_description")),
        ocr_text=_optional_string(payload.get("ocr_text")),
        keywords=parsed_keywords,
        uncertainty=_optional_string(payload.get("uncertainty")),
    )


def _response_content(response: Any) -> str:
    choices = _get(response, "choices")
    if isinstance(choices, list) and choices:
        message = _get(choices[0], "message")
        content = _get(message, "content")
        if isinstance(content, str):
            return content
    content = _get(response, "content")
    return content if isinstance(content, str) else ""


def _get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _strip_code_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return content


def _optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _image_model(context: IngestContext) -> str:
    ingest = context.config.get("ingest")
    if isinstance(ingest, dict):
        image_model = ingest.get("image_model")
        if isinstance(image_model, str) and image_model.strip():
            return image_model.strip()
    model = context.config.get("model")
    return str(model or "gpt-4o-mini")
