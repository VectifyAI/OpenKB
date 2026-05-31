from __future__ import annotations

import io
import os
import time
import zipfile
from pathlib import Path
from typing import Any

from openkb.parsers.base import ParseResult, Parser

_SUPPORTED = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".html", ".htm"}
_CLOUD_BASE = "https://mineru.net/api/v4"


def _httpx():
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError(
            "MinerU parser requires 'httpx'. Install with: pip install openkb[mineru]"
        ) from exc
    return httpx


def _result_from_zip(zip_bytes: bytes) -> ParseResult:
    """Extract the markdown file + images from a MinerU result zip."""
    images: dict[str, bytes] = {}
    markdown = ""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        md_names = [n for n in zf.namelist() if n.lower().endswith(".md")]
        if md_names:
            chosen = next((n for n in md_names if n.endswith("full.md")), md_names[0])
            markdown = zf.read(chosen).decode("utf-8", errors="replace")
        for name in zf.namelist():
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                images[Path(name).name] = zf.read(name)
    # Markdown references images as 'images/<file>'; localize_images matches on
    # the bare filename, so rewrite 'images/fig.png' -> 'fig.png'.
    for fname in images:
        markdown = markdown.replace(f"images/{fname}", fname)
    return ParseResult(markdown=markdown, images=images)


class MineruParser(Parser):
    """MinerU via HTTP — self-hosted server or hosted cloud API."""

    name = "mineru"

    def __init__(self, opts: dict[str, Any] | None = None):
        self.opts = opts or {}
        self.mode = self.opts.get("mode", "cloud")
        self.base_url = self.opts.get("base_url")
        self.poll_interval = self.opts.get("poll_interval", 3)
        self.timeout = self.opts.get("timeout", 600)

    def supports(self, suffix: str) -> bool:
        return suffix.lower() in _SUPPORTED

    def parse(self, src: Path) -> ParseResult:
        if self.mode == "self_hosted":
            return self._parse_self_hosted(src)
        return self._parse_cloud(src)

    def _parse_self_hosted(self, src: Path) -> ParseResult:
        if not self.base_url:
            raise RuntimeError(
                "MinerU self_hosted mode requires 'base_url' in parsers.mineru config."
            )
        httpx = _httpx()
        url = self.base_url.rstrip("/") + "/file_parse"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                url,
                files={"file": (src.name, src.read_bytes())},
                data={"return_format": "zip"},
            )
            resp.raise_for_status()
            return _result_from_zip(resp.content)

    def _parse_cloud(self, src: Path) -> ParseResult:
        api_key = os.environ.get("MINERU_API_KEY")
        if not api_key:
            raise RuntimeError(
                "MinerU cloud mode requires the MINERU_API_KEY environment variable."
            )
        httpx = _httpx()
        headers = {"Authorization": f"Bearer {api_key}"}
        with httpx.Client(timeout=self.timeout) as client:
            r = client.post(
                f"{_CLOUD_BASE}/file-urls/batch",
                headers=headers,
                json={"files": [{"name": src.name, "is_ocr": True}]},
            )
            r.raise_for_status()
            data = r.json()["data"]
            batch_id = data["batch_id"]
            upload_url = data["file_urls"][0]
            client.put(upload_url, content=src.read_bytes()).raise_for_status()
            elapsed = 0
            zip_url = None
            while elapsed < self.timeout:
                pr = client.get(
                    f"{_CLOUD_BASE}/extract-results/batch/{batch_id}", headers=headers
                )
                pr.raise_for_status()
                results = pr.json()["data"]["extract_result"]
                state = results[0].get("state")
                if state == "done":
                    zip_url = results[0]["full_zip_url"]
                    break
                if state == "failed":
                    raise RuntimeError(f"MinerU extraction failed: {results[0]}")
                time.sleep(self.poll_interval)
                elapsed += self.poll_interval
            if zip_url is None:
                raise RuntimeError("MinerU extraction timed out.")
            zr = client.get(zip_url)
            zr.raise_for_status()
            return _result_from_zip(zr.content)
