"""Feishu/Lark document source importer for bundle ingest."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openkb.ingest.context import IngestContext
from openkb.ingest.models import IngestInput
from openkb.locks import atomic_write_text
from openkb.url_ingest import _sanitize_filename, _unique_path, looks_like_url

_FEISHU_DOMAINS = ("feishu.cn", "larksuite.com")
_FEISHU_DOC_PREFIXES = ("/wiki/", "/docx/", "/docs/")


class FeishuImporter:
    name = "feishu"

    def can_handle(self, target: str, context: IngestContext) -> bool:
        del context
        return looks_like_feishu_url(target)

    def import_source(self, target: str, context: IngestContext) -> IngestInput:
        markdown, title = _fetch_markdown_with_lark_cli(target, context)
        raw_dir = context.staging_dir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        filename = _sanitize_filename(title or _fallback_title(target), ".md")
        path = _unique_path(raw_dir / filename)
        atomic_write_text(path, markdown.rstrip() + "\n")
        return IngestInput(
            target=target,
            path=path,
            source_uri=target,
            media_type="text/markdown",
            metadata={
                "display_name": path.name,
                "title": title,
                "source_system": "feishu",
                "auth_adapter": "lark-cli",
                "permission_boundary": "current lark-cli identity",
            },
        )


def looks_like_feishu_url(target: str) -> bool:
    if not looks_like_url(target):
        return False
    parsed = urlparse(target)
    if not _host_matches(parsed.netloc, _FEISHU_DOMAINS):
        return False
    return any(parsed.path.startswith(prefix) for prefix in _FEISHU_DOC_PREFIXES)


def _fetch_markdown_with_lark_cli(target: str, context: IngestContext) -> tuple[str, str | None]:
    config = _feishu_config(context)
    cli = str(config.get("cli") or "lark-cli")
    timeout = _int_config(config.get("timeout"), default=120)
    command = [
        cli,
        "docs",
        "+fetch",
        "--api-version",
        "v2",
        "--doc",
        target,
        "--doc-format",
        "markdown",
        "--format",
        "json",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ValueError("Feishu importer requires lark-cli on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"Feishu fetch timed out after {timeout}s: {target}") from exc

    if completed.returncode != 0:
        detail = (
            completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        )
        raise ValueError(f"Feishu fetch failed: {detail}")

    markdown, title = _parse_lark_cli_output(completed.stdout)
    if not markdown.strip():
        raise ValueError("Feishu fetch returned empty markdown.")
    return markdown, title


def _parse_lark_cli_output(stdout: str) -> tuple[str, str | None]:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout, None
    markdown = _find_first_string(payload, ("markdown", "content", "text", "body")) or ""
    title = _find_first_string(payload, ("title", "name"))
    return markdown, title


def _find_first_string(value: Any, keys: tuple[str, ...]) -> str | None:
    if isinstance(value, dict):
        for key in keys:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        for item in value.values():
            found = _find_first_string(item, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_first_string(item, keys)
            if found is not None:
                return found
    return None


def _feishu_config(context: IngestContext) -> dict[str, Any]:
    ingest = context.config.get("ingest")
    if not isinstance(ingest, dict):
        return {}
    feishu = ingest.get("feishu")
    return feishu if isinstance(feishu, dict) else {}


def _int_config(value: object, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(1, parsed)


def _fallback_title(target: str) -> str:
    parsed = urlparse(target)
    name = Path(parsed.path.rstrip("/")).name
    return name or parsed.netloc or "feishu-document"


def _host_matches(netloc: str, domains: tuple[str, ...]) -> bool:
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)
