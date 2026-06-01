from __future__ import annotations

import io
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Any

from openkb.parsers.base import ParseResult, Parser

logger = logging.getLogger(__name__)

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
        names = zf.namelist()
        md_names = sorted(n for n in names if n.lower().endswith(".md"))
        if md_names:
            chosen = next((n for n in md_names if Path(n).name == "full.md"), md_names[0])
            markdown = zf.read(chosen).decode("utf-8", errors="replace")
        for name in names:
            if name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                base = Path(name).name
                if base in images:
                    logger.warning(
                        "MinerU result has multiple images named %r in different "
                        "folders; keeping the last. Earlier one may be lost.", base
                    )
                images[base] = zf.read(name)
    return ParseResult(markdown=markdown, images=images)


def _mineru_body(resp):
    """Return the 'data' dict from a MinerU v4 JSON response, raising on API errors."""
    body = resp.json()
    code = body.get("code")
    if code not in (0, None):
        raise RuntimeError(f"MinerU API error (code={code}): {body.get('msg')}")
    return body.get("data") or {}


class MineruParser(Parser):
    """MinerU via HTTP — self-hosted server or hosted cloud API."""

    name = "mineru"

    def __init__(self, opts: dict[str, Any] | None = None):
        self.opts = opts or {}
        self.mode = self.opts.get("mode", "cloud")
        self.base_url = self.opts.get("base_url")
        pi = self.opts.get("poll_interval", 3)
        self.poll_interval = pi if isinstance(pi, (int, float)) and pi > 0 else 3
        t = self.opts.get("timeout", 600)
        self.timeout = t if isinstance(t, (int, float)) and t > 0 else 600

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
        with httpx.Client(timeout=min(self.timeout, 120)) as client:
            r = client.post(
                f"{_CLOUD_BASE}/file-urls/batch",
                headers=headers,
                json={"files": [{"name": src.name, "is_ocr": True}]},
            )
            r.raise_for_status()
            data = _mineru_body(r)
            batch_id = data.get("batch_id")
            file_urls = data.get("file_urls") or []
            if not batch_id or not file_urls:
                raise RuntimeError(f"MinerU returned no upload URL: {data}")
            upload_url = file_urls[0]
            client.put(upload_url, content=src.read_bytes()).raise_for_status()
            deadline = time.monotonic() + self.timeout
            zip_url = None
            while time.monotonic() < deadline:
                pr = client.get(
                    f"{_CLOUD_BASE}/extract-results/batch/{batch_id}", headers=headers
                )
                pr.raise_for_status()
                data = _mineru_body(pr)
                results = data.get("extract_result") or []
                if not results:
                    time.sleep(self.poll_interval)
                    continue
                state = results[0].get("state")
                if state == "done":
                    zip_url = results[0].get("full_zip_url")
                    if not zip_url:
                        raise RuntimeError(
                            f"MinerU reported done but no full_zip_url: {results[0]}"
                        )
                    break
                if state == "failed":
                    raise RuntimeError(f"MinerU extraction failed: {results[0]}")
                time.sleep(self.poll_interval)
            if zip_url is None:
                raise RuntimeError("MinerU extraction timed out.")
            zr = client.get(zip_url)
            zr.raise_for_status()
            return _result_from_zip(zr.content)
