"""Image extraction and copy utilities for the OpenKB converter pipeline."""
from __future__ import annotations

import base64
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Matches: ![alt](data:image/ext;base64,DATA)
_BASE64_RE = re.compile(r'!\[([^\]]*)\]\(data:image/([^;]+);base64,([^)]+)\)')

# Matches: ![alt](relative/path) — excludes http(s):// and data: URIs
_RELATIVE_RE = re.compile(r'!\[([^\]]*)\]\((?!https?://|data:)([^)]+)\)')


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
