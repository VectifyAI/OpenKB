"""Image extraction and copy utilities for the OpenKB converter pipeline."""
from __future__ import annotations

import base64
import logging
import re
import shutil
from pathlib import Path

import pymupdf

logger = logging.getLogger(__name__)

# Matches: ![alt](data:image/ext;base64,DATA)
_BASE64_RE = re.compile(r'!\[([^\]]*)\]\(data:image/([^;]+);base64,([^)]+)\)')

# Matches: ![alt](relative/path) — excludes http(s):// and data: URIs
_RELATIVE_RE = re.compile(r'!\[([^\]]*)\]\((?!https?://|data:)([^)]+)\)')


def extract_pdf_images(pdf_path: Path, doc_name: str, images_dir: Path) -> dict[int, list[str]]:
    """Extract images from a PDF using pymupdf and save to disk.

    Returns a mapping of page_number (1-based) → list of relative image paths,
    so the caller can insert image references into the converted markdown.
    """
    images_dir.mkdir(parents=True, exist_ok=True)
    page_images: dict[int, list[str]] = {}
    counter = 0

    doc = pymupdf.open(str(pdf_path))
    for page_num in range(len(doc)):
        page = doc[page_num]
        image_list = page.get_images(full=True)
        for img_info in image_list:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                logger.warning("Failed to extract image xref=%d from page %d", xref, page_num + 1)
                continue

            if not base_image or not base_image.get("image"):
                continue

            ext = base_image.get("ext", "png")
            image_bytes = base_image["image"]

            # Skip tiny images (likely icons/bullets, < 2KB)
            if len(image_bytes) < 2048:
                continue

            counter += 1
            filename = f"img_{counter:03d}.{ext}"
            (images_dir / filename).write_bytes(image_bytes)

            rel_path = f"images/{doc_name}/{filename}"
            page_images.setdefault(page_num + 1, []).append(rel_path)

    doc.close()
    return page_images


def extract_base64_images(markdown: str, doc_name: str, images_dir: Path) -> str:
    """Decode base64-embedded images, save to disk, and rewrite markdown links.

    For each ``![alt](data:image/ext;base64,DATA)`` match:
    - Decode base64 bytes → save to ``images_dir/img_NNN.ext``
    - Replace the link with ``![alt](images/{doc_name}/img_NNN.ext)``
    - On decode failure: log a warning and leave the original text unchanged.
    """
    counter = 0
    result = markdown

    for match in _BASE64_RE.finditer(markdown):
        alt, ext, b64_data = match.group(1), match.group(2), match.group(3)
        try:
            image_bytes = base64.b64decode(b64_data, validate=True)
        except Exception:
            logger.warning(
                "Failed to decode base64 image (alt=%r, ext=%r); leaving original.",
                alt,
                ext,
            )
            continue

        counter += 1
        filename = f"img_{counter:03d}.{ext}"
        dest = images_dir / filename
        images_dir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image_bytes)

        new_ref = f"![{alt}](images/{doc_name}/{filename})"
        result = result.replace(match.group(0), new_ref, 1)

    return result


def copy_relative_images(
    markdown: str, source_dir: Path, doc_name: str, images_dir: Path
) -> str:
    """Copy locally-referenced images into the KB images directory and rewrite links.

    For each ``![alt](relative/path)`` match (skipping http/https and data URIs):
    - Resolve path relative to ``source_dir``
    - Copy to ``images_dir/{filename}``
    - Replace link with ``![alt](images/{doc_name}/{filename})``
    - Missing source file: log a warning and leave the original text unchanged.
    """
    result = markdown

    for match in _RELATIVE_RE.finditer(markdown):
        alt, rel_path = match.group(1), match.group(2)
        src = source_dir / rel_path
        if not src.exists():
            logger.warning(
                "Relative image not found: %s; leaving original link.", src
            )
            continue

        filename = src.name
        dest = images_dir / filename
        images_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

        new_ref = f"![{alt}](images/{doc_name}/{filename})"
        result = result.replace(match.group(0), new_ref, 1)

    return result
