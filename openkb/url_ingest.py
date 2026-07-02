"""Fetch remote URLs into the knowledge base's ``raw/`` directory.

This module is the input-acquisition layer for ``openkb add <URL>``: it
takes an http(s) URL, decides whether it points at a PDF or an HTML
document (using both the HTTP ``Content-Type`` header and a magic-byte
sniff so a mistyped header doesn't fool us), saves a local file under
``raw/``, and hands the path back to the caller.

The caller (``openkb.cli.add``) then dispatches the saved path to the
normal local-file ingest pipeline (``add_single_file`` →
``convert_document`` → markitdown / PageIndex), so all the existing
short-vs-long-doc routing applies automatically based on the file
extension and page count.

PDF responses are streamed to disk in chunks (large papers can be tens
of MB). HTML responses are passed through trafilatura's main-content
extractor — saving the raw HTML directly would feed nav/footer/cookie
chrome into the LLM and produce noisy summaries.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

import click

_USER_AGENT = "openkb/url-fetcher (+https://github.com/VectifyAI/OpenKB)"
_TIMEOUT_SECONDS = 30
_CHUNK_BYTES = 64 * 1024
_SNIFF_BYTES = 512
_HTML_MIN_EXTRACT_CHARS = 300
_MAX_FILENAME_STEM = 80


def looks_like_url(s: str) -> bool:
    """Cheap check used by ``openkb add`` to branch into URL ingest."""
    return s.startswith(("http://", "https://"))


def _sniff_content_type(head: bytes, declared: str) -> str:
    """Return ``'pdf'``, ``'html'``, or ``'unknown'`` based on magic bytes,
    falling back to the server's declared Content-Type.

    Magic bytes win when they disagree with the header (some servers
    mislabel PDFs as ``application/octet-stream`` or send HTML
    interstitial pages with ``application/pdf``).
    """
    if head.startswith(b"%PDF-"):
        return "pdf"
    stripped = head.lstrip(b" \t\r\n\xef\xbb\xbf")  # strip BOM + leading whitespace
    if stripped[:1] == b"<":
        return "html"
    declared_main = declared.split(";")[0].strip().lower()
    if declared_main == "application/pdf":
        return "pdf"
    if declared_main.startswith("text/html") or declared_main == "application/xhtml+xml":
        return "html"
    return "unknown"


def _sanitize_filename(name: str, ext: str) -> str:
    """Make a filename safe for shell + filesystem use.

    - URL-decodes percent escapes.
    - Strips the existing extension **only when it matches the target
      ``ext``** — so ``"2509.11420"`` keeps its dot when we're saving a
      ``.pdf`` (the dot is part of the arxiv identifier, not an
      extension).
    - Replaces whitespace / parentheses / other non-``[a-zA-Z0-9._-]``
      chars with ``-``, collapses repeated ``-``, and trims.
    - Caps the stem at 80 chars to avoid filesystem limits.
    - Returns ``<stem><ext>``, falling back to ``document<ext>`` if the
      sanitized stem is empty.
    """
    decoded = unquote(name)
    if ext and decoded.lower().endswith(ext.lower()):
        stem = decoded[: -len(ext)]
    else:
        stem = decoded
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem)
    stem = re.sub(r"-+", "-", stem).strip("-._")
    stem = stem[:_MAX_FILENAME_STEM].rstrip("-._")
    return f"{stem}{ext}" if stem else f"document{ext}"


def _parse_content_disposition_filename(header: str | None) -> str | None:
    """Extract a filename hint from a ``Content-Disposition`` header.

    Handles three forms (in priority order):

    1. ``filename*=UTF-8''percent-encoded`` (RFC 5987)
    2. ``filename="quoted with spaces.pdf"``
    3. ``filename=unquoted-no-spaces.pdf``
    """
    if not header:
        return None
    # RFC 5987 extended form first
    m = re.search(r"filename\*=(?:[\w-]+'[\w-]*')?([^;]+)", header)
    if m:
        return unquote(m.group(1).strip())
    # Quoted form (may contain spaces / parens / commas)
    m = re.search(r'filename="([^"]+)"', header)
    if m:
        return m.group(1)
    # Unquoted form (stops at whitespace or semicolon)
    m = re.search(r"filename=([^\s;]+)", header)
    if m:
        return m.group(1)
    return None


def _pdf_filename(url: str, content_disposition: str | None) -> str:
    """Derive a ``.pdf`` filename for a downloaded PDF.

    Priority: ``Content-Disposition: filename=`` header → URL basename →
    URL host. The result is run through :func:`_sanitize_filename`.
    """
    cd_name = _parse_content_disposition_filename(content_disposition)
    if cd_name:
        return _sanitize_filename(cd_name, ".pdf")
    parsed = urlparse(url)
    last = (parsed.path.rsplit("/", 1)[-1] or parsed.netloc).strip()
    return _sanitize_filename(last or "document", ".pdf")


def _unique_path(target: Path) -> Path:
    """Return ``target`` if it doesn't exist yet, otherwise append ``_2``,
    ``_3``, … to the stem until an unused name is found.

    Prevents silent overwrites in ``raw/`` when two different URLs
    sanitize to the same filename (e.g. two blog posts both titled
    "Introduction" → both ``Introduction.md``).
    """
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    parent = target.parent
    for i in range(2, 10_000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find a free filename for {target} after 10k attempts")


def _download_pdf_chunked(response, head_bytes: bytes, target: Path) -> None:
    """Write the already-read ``head_bytes`` plus the remaining streamed
    body to ``target``. Chunked so very large PDFs (50+ MB) don't sit in
    RAM.
    """
    with open(target, "wb") as fh:
        if head_bytes:
            fh.write(head_bytes)
        while True:
            chunk = response.read(_CHUNK_BYTES)
            if not chunk:
                break
            fh.write(chunk)


def _extract_html(url: str, raw_dir: Path) -> Path | None:
    """Fetch the URL through trafilatura, extract the main content as
    Markdown, and write it to ``raw/<title-slug>.md``.

    Returns the saved path, or None if extraction failed entirely. A
    short extraction (< 300 chars) is saved anyway but flagged on
    stderr — pages that are JS-rendered or paywalled often produce
    near-empty extractions.
    """
    import trafilatura

    raw_html = trafilatura.fetch_url(url)
    if not raw_html:
        click.echo(f"  [ERROR] Could not fetch URL: {url}", err=True)
        return None

    markdown = trafilatura.extract(
        raw_html,
        output_format="markdown",
        include_links=True,
    )
    if not markdown:
        click.echo(
            "  [ERROR] No main content extracted — page may be empty, JS-rendered, or paywalled.",
            err=True,
        )
        return None

    if len(markdown) < _HTML_MIN_EXTRACT_CHARS:
        click.echo(
            f"  [WARN] Only {len(markdown)} chars extracted — page may be "
            f"JS-rendered or behind a login. Saving anyway; inspect the "
            f"resulting wiki entry and use `openkb remove` if it's empty.",
            err=True,
        )

    metadata = trafilatura.extract_metadata(raw_html)
    title = (metadata.title if metadata else None) or url
    filename = _sanitize_filename(title, ".md")
    # Pick a non-colliding name — two blog posts titled "Introduction"
    # would otherwise overwrite each other in raw/ and leave the hash
    # registry pointing at stale bytes.
    target = _unique_path(raw_dir / filename)
    target.write_text(markdown, encoding="utf-8")
    click.echo(
        f"  Extracted: {title!r}\n"
        f"  Saved: raw/{target.name} ({len(markdown) // 1024 or 1} KB clean markdown)"
    )
    return target


def fetch_url_to_raw(url: str, kb_dir: Path) -> Path | None:
    """Fetch ``url`` into ``<kb>/raw/`` and return the local path.

    Routing is decided by HTTP ``Content-Type`` validated against magic
    bytes (in case the server lies):

    - PDF  → urllib chunked download → ``raw/<sanitized>.pdf``
    - HTML → trafilatura main-content extract → ``raw/<title-slug>.md``
    - anything else → error, returns None

    The caller then hands the saved path to ``add_single_file``, so the
    existing PageIndex / markitdown routing by file extension and page
    count takes over from there.
    """
    raw_dir = kb_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    click.echo(f"Downloading: {url}")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
    )
    try:
        response = urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        click.echo(f"  [ERROR] HTTP {exc.code} {exc.reason}", err=True)
        return None
    except urllib.error.URLError as exc:
        click.echo(f"  [ERROR] Network error: {exc.reason}", err=True)
        return None
    except Exception as exc:
        click.echo(f"  [ERROR] Fetch failed: {exc}", err=True)
        return None

    with response:
        declared = response.headers.get("Content-Type", "") or ""
        head_bytes = response.read(_SNIFF_BYTES)
        actual = _sniff_content_type(head_bytes, declared)

        if actual == "pdf":
            # Derive the filename from the *post-redirect* URL — urllib
            # follows redirects by default, so the user-typed URL may
            # not be the URL that actually served the bytes (DOI / short
            # link resolvers, mirror redirects, etc.). Falls back to the
            # original input when the response doesn't expose a final
            # URL.
            final_url = response.geturl() or url
            filename = _pdf_filename(
                final_url,
                response.headers.get("Content-Disposition"),
            )
            target = _unique_path(raw_dir / filename)
            _download_pdf_chunked(response, head_bytes, target)
            size_mb = target.stat().st_size / (1024 * 1024)
            click.echo(f"  Saved: raw/{target.name} ({size_mb:.1f} MB PDF)")
            return target

    if actual == "html":
        return _extract_html(url, raw_dir)

    click.echo(
        f"  [ERROR] Unsupported content type {declared!r} for URL ingest. "
        "Download the file manually and pass its path to `openkb add` instead.",
        err=True,
    )
    return None
