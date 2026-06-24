"""Tests for `openkb.url_ingest` — the URL → raw/ input-acquisition layer."""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

from openkb.url_ingest import (
    _parse_content_disposition_filename,
    _pdf_filename,
    _sanitize_filename,
    _sniff_content_type,
    _unique_path,
    fetch_url_to_raw,
    looks_like_url,
)


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def test_looks_like_url_accepts_http_and_https():
    assert looks_like_url("http://example.com") is True
    assert looks_like_url("https://example.com/foo") is True


def test_looks_like_url_rejects_paths_and_filenames():
    assert looks_like_url("/tmp/foo.pdf") is False
    assert looks_like_url("./relative") is False
    assert looks_like_url("foo.pdf") is False
    assert looks_like_url("") is False


# Content-type sniffing — magic bytes override declared header


def test_sniff_pdf_magic_wins_over_octet_stream():
    """Some CDNs mislabel PDFs as application/octet-stream — magic bytes save us."""
    assert _sniff_content_type(b"%PDF-1.4\n...", "application/octet-stream") == "pdf"


def test_sniff_html_magic_wins_over_pdf_header():
    """Some servers serve an HTML interstitial 'click to download' page with
    Content-Type: application/pdf. Magic bytes must override."""
    assert _sniff_content_type(b"<!doctype html>", "application/pdf") == "html"


def test_sniff_html_magic_handles_bom_and_whitespace():
    assert _sniff_content_type(b"\xef\xbb\xbf<html>", "") == "html"
    assert _sniff_content_type(b"  \n<html>", "") == "html"


def test_sniff_falls_back_to_declared_when_no_magic_match():
    assert _sniff_content_type(b"\x00\x00", "application/pdf") == "pdf"
    assert _sniff_content_type(b"random", "text/html; charset=utf-8") == "html"
    assert _sniff_content_type(b"random", "application/xhtml+xml") == "html"


def test_sniff_returns_unknown_for_unsupported_types():
    assert _sniff_content_type(b"binary", "image/jpeg") == "unknown"
    assert _sniff_content_type(b"binary", "application/json") == "unknown"
    assert _sniff_content_type(b"binary", "") == "unknown"


# Filename sanitization


def test_sanitize_preserves_arxiv_id_with_dot():
    """The dot in arxiv's `2509.11420` is part of the identifier, not an
    extension. `_sanitize_filename` must not strip it when re-adding `.pdf`."""
    assert _sanitize_filename("2509.11420", ".pdf") == "2509.11420.pdf"


def test_sanitize_strips_matching_extension_then_re_adds_it():
    assert _sanitize_filename("paper.pdf", ".pdf") == "paper.pdf"
    assert _sanitize_filename("paper.PDF", ".pdf") == "paper.pdf"


def test_sanitize_replaces_shell_unsafe_chars():
    assert _sanitize_filename("hello world (1).pdf", ".pdf") == "hello-world-1.pdf"
    assert _sanitize_filename("a:b/c\\d?e*f", ".md") == "a-b-c-d-e-f.md"


def test_sanitize_collapses_repeated_dashes_and_trims():
    # Underscores are allowed (part of [a-zA-Z0-9._-]) so they pass through;
    # only sequences of non-allowed chars become dashes, and repeated dashes
    # collapse to one. Leading/trailing dashes/dots/underscores are stripped.
    assert _sanitize_filename("a___b---c", ".pdf") == "a___b-c.pdf"
    assert _sanitize_filename("---trim---", ".md") == "trim.md"
    assert _sanitize_filename("a b c d", ".md") == "a-b-c-d.md"


def test_sanitize_caps_stem_at_80_chars():
    name = "a" * 200
    assert _sanitize_filename(name, ".pdf") == ("a" * 80) + ".pdf"


def test_sanitize_falls_back_to_document_when_empty():
    assert _sanitize_filename("", ".md") == "document.md"
    assert _sanitize_filename("...", ".md") == "document.md"
    assert _sanitize_filename("///", ".pdf") == "document.pdf"


# Content-Disposition parsing


def test_content_disposition_quoted_with_spaces():
    """Quoted form must capture filenames with spaces / parens / commas."""
    cd = 'attachment; filename="My Paper, v3 (final).pdf"'
    assert _parse_content_disposition_filename(cd) == "My Paper, v3 (final).pdf"


def test_content_disposition_unquoted_simple():
    assert _parse_content_disposition_filename("attachment; filename=foo.pdf") == "foo.pdf"


def test_content_disposition_rfc5987_extended():
    """filename*=UTF-8''<percent-encoded> is the modern form for non-ASCII."""
    cd = "attachment; filename*=UTF-8''My%20Paper%20%C3%A9.pdf"
    assert _parse_content_disposition_filename(cd) == "My Paper é.pdf"


def test_content_disposition_none_or_missing_filename():
    assert _parse_content_disposition_filename(None) is None
    assert _parse_content_disposition_filename("attachment") is None
    assert _parse_content_disposition_filename("inline") is None


# _pdf_filename: header → URL basename fallback chain


def test_pdf_filename_prefers_content_disposition():
    name = _pdf_filename(
        "https://x.com/dl?id=123",
        'attachment; filename="My Paper.pdf"',
    )
    assert name == "My-Paper.pdf"


def test_pdf_filename_falls_back_to_url_basename():
    name = _pdf_filename(
        "https://cdn.example.com/abc/69fe2a55_The-Founders-Playbook-05062026_v3%20(1).pdf",
        None,
    )
    assert name == "69fe2a55_The-Founders-Playbook-05062026_v3-1.pdf"


def test_pdf_filename_handles_arxiv_pdf_url_without_extension():
    """arxiv's PDF URL `arxiv.org/pdf/2509.11420` ends without `.pdf` — the
    content-type tells us it's a PDF and `_sanitize_filename` must keep
    the dot in the arxiv ID rather than treating it as an extension."""
    name = _pdf_filename("https://arxiv.org/pdf/2509.11420", None)
    assert name == "2509.11420.pdf"


def test_pdf_filename_falls_back_to_host_when_path_empty():
    name = _pdf_filename("https://example.com/", None)
    assert name == "example.com.pdf"


# ---------------------------------------------------------------------------
# fetch_url_to_raw — integration with urllib + trafilatura mocked
# ---------------------------------------------------------------------------


def _fake_response(*, body: bytes, headers: dict[str, str]):
    """Build a fake urllib response with the given body + headers.

    Headers are case-insensitive in real responses; mimicking that here
    so the test doesn't depend on which case `_fetch_url_to_raw` looks up.
    """
    class _Headers:
        def __init__(self, d):
            self._d = {k.lower(): v for k, v in d.items()}

        def get(self, key, default=None):
            return self._d.get(key.lower(), default)

    resp = MagicMock()
    resp.headers = _Headers(headers)
    # read(N) returns chunks; read() with no arg returns rest
    stream = io.BytesIO(body)
    resp.read = stream.read
    # urllib's HTTPResponse exposes geturl(); default to empty string so
    # callers using `response.geturl() or url` fall through to the input URL.
    resp.geturl = lambda: ""
    resp.__enter__ = lambda self: resp
    resp.__exit__ = lambda *a: None
    return resp


def test_fetch_pdf_writes_chunked_to_raw_dir(tmp_path):
    """End-to-end PDF path: urlopen → magic-byte sniff → chunked write →
    filename comes from URL basename."""
    body = b"%PDF-1.4\n" + b"x" * 100_000  # 100 KB PDF
    resp = _fake_response(
        body=body,
        headers={"Content-Type": "application/pdf"},
    )

    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_to_raw("https://arxiv.org/pdf/2509.11420", tmp_path)

    assert result is not None
    assert result.name == "2509.11420.pdf"
    assert result.exists()
    assert result.read_bytes() == body


def test_fetch_pdf_with_lying_octet_stream_header(tmp_path):
    """Server says octet-stream but the body starts with %PDF — magic bytes
    must win and the file gets the .pdf extension."""
    body = b"%PDF-1.7\n" + b"\x00" * 1000
    resp = _fake_response(
        body=body,
        headers={"Content-Type": "application/octet-stream"},
    )

    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_to_raw("https://cdn.example.com/a/b/file", tmp_path)

    assert result is not None
    assert result.suffix == ".pdf"
    assert result.read_bytes() == body


def test_fetch_pdf_chunks_a_very_large_body(tmp_path):
    """A 1 MB synthetic body still writes correctly via chunked reads."""
    body = b"%PDF-1.4\n" + b"a" * (1024 * 1024)
    resp = _fake_response(body=body, headers={"Content-Type": "application/pdf"})

    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_to_raw("https://x.com/big.pdf", tmp_path)

    assert result is not None
    assert result.stat().st_size == len(body)


def test_fetch_pdf_uses_content_disposition_filename(tmp_path):
    body = b"%PDF-1.4\n..."
    resp = _fake_response(
        body=body,
        headers={
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="My Paper, v3.pdf"',
        },
    )

    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_to_raw("https://x.com/dl?id=1", tmp_path)

    # comma sanitized to dash
    assert result.name == "My-Paper-v3.pdf"


def test_fetch_html_routes_to_trafilatura(tmp_path):
    """HTML responses skip urllib's body (we already consumed the sniff
    head) and hand the URL to trafilatura.fetch_url for proper anti-scrape
    handling. trafilatura.extract gives clean markdown which we save as .md."""
    sniff_head = b"<!doctype html>\n<html><head>..."
    resp = _fake_response(
        body=sniff_head + b"<body>nav nav nav</body>",
        headers={"Content-Type": "text/html; charset=utf-8"},
    )

    fake_md = "# Real Article Title\n\nThis is the body content. " * 20  # ~1 KB
    fake_meta = MagicMock()
    fake_meta.title = "Real Article Title"

    with patch("urllib.request.urlopen", return_value=resp), \
         patch("trafilatura.fetch_url", return_value="<html>...the real HTML...</html>"), \
         patch("trafilatura.extract", return_value=fake_md), \
         patch("trafilatura.extract_metadata", return_value=fake_meta):
        result = fetch_url_to_raw("https://blog.example.com/post", tmp_path)

    assert result is not None
    assert result.name == "Real-Article-Title.md"
    assert result.read_text(encoding="utf-8") == fake_md


def test_fetch_html_warns_on_short_extraction(tmp_path, capsys):
    """JS-rendered pages produce near-empty extractions. The save still
    happens (so the user can inspect what we got) but a stderr warning
    surfaces the suspicion."""
    sniff_head = b"<html>"
    resp = _fake_response(body=sniff_head, headers={"Content-Type": "text/html"})

    short_md = "# Title only"  # 11 chars, well under 300
    fake_meta = MagicMock()
    fake_meta.title = "Title only"

    with patch("urllib.request.urlopen", return_value=resp), \
         patch("trafilatura.fetch_url", return_value="<html>shell</html>"), \
         patch("trafilatura.extract", return_value=short_md), \
         patch("trafilatura.extract_metadata", return_value=fake_meta):
        result = fetch_url_to_raw("https://spa.example.com/page", tmp_path)

    assert result is not None
    assert result.read_text() == short_md
    err = capsys.readouterr().err
    assert "[WARN]" in err
    assert f"{len(short_md)} chars extracted" in err


def test_fetch_html_aborts_when_trafilatura_extracts_nothing(tmp_path):
    """trafilatura.extract returning None means the page is essentially
    empty (JS-only, paywall HTML, etc.). We error out rather than save
    an empty .md."""
    sniff_head = b"<html>"
    resp = _fake_response(body=sniff_head, headers={"Content-Type": "text/html"})

    with patch("urllib.request.urlopen", return_value=resp), \
         patch("trafilatura.fetch_url", return_value="<html>empty</html>"), \
         patch("trafilatura.extract", return_value=None):
        result = fetch_url_to_raw("https://js-only.example.com", tmp_path)

    assert result is None


def test_fetch_unsupported_content_type_rejected(tmp_path, capsys):
    """JSON / image / etc. — refuse with a clear message rather than
    saving binary garbage as `.html` or `.pdf`."""
    resp = _fake_response(
        body=b'{"foo": "bar"}',
        headers={"Content-Type": "application/json"},
    )

    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_to_raw("https://api.example.com/data.json", tmp_path)

    assert result is None
    err = capsys.readouterr().err
    assert "Unsupported content type" in err


def test_fetch_http_404_returns_none(tmp_path, capsys):
    """Server errors don't crash — graceful failure with stderr message."""
    import urllib.error
    err_resp = urllib.error.HTTPError(
        "https://x.com/missing", 404, "Not Found", {}, None,
    )

    with patch("urllib.request.urlopen", side_effect=err_resp):
        result = fetch_url_to_raw("https://x.com/missing", tmp_path)

    assert result is None
    err = capsys.readouterr().err
    assert "HTTP 404" in err


def test_fetch_network_error_returns_none(tmp_path, capsys):
    """DNS failure / connection refused — graceful with clear message."""
    import urllib.error

    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("nodename nor servname provided"),
    ):
        result = fetch_url_to_raw("https://no-such-host.invalid/foo", tmp_path)

    assert result is None
    err = capsys.readouterr().err
    assert "Network error" in err


# ---------------------------------------------------------------------------
# Self-review fixes from PR #55 review pass
# ---------------------------------------------------------------------------


def test_unique_path_returns_target_when_free(tmp_path):
    p = tmp_path / "foo.pdf"
    assert _unique_path(p) == p


def test_unique_path_finds_next_free_slot(tmp_path):
    """_2 / _3 / … must be appended to the stem (not the suffix) and
    must keep probing until a free name is found."""
    (tmp_path / "foo.pdf").write_bytes(b"a")
    (tmp_path / "foo_2.pdf").write_bytes(b"b")

    result = _unique_path(tmp_path / "foo.pdf")
    assert result == tmp_path / "foo_3.pdf"


def test_unique_path_handles_no_suffix(tmp_path):
    """Files without an extension still get a usable suffix."""
    (tmp_path / "README").write_text("x")
    result = _unique_path(tmp_path / "README")
    assert result.name == "README_2"


def test_fetch_pdf_picks_unique_name_when_target_exists(tmp_path):
    """Two URLs that sanitize to the same filename must NOT silently
    overwrite — the second one gets `_2` appended."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "paper.pdf").write_bytes(b"%PDF-existing\nfirst")

    body = b"%PDF-1.7\nsecond URL content"
    resp = _fake_response(body=body, headers={"Content-Type": "application/pdf"})
    # Make response.geturl mimic real urllib (returns the input URL when
    # there's no redirect). The fake response builder doesn't set this.
    resp.geturl = lambda: "https://mirror.example.com/paper.pdf"

    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_to_raw("https://mirror.example.com/paper.pdf", tmp_path)

    # First file is untouched, second went to paper_2.pdf
    assert (raw_dir / "paper.pdf").read_bytes() == b"%PDF-existing\nfirst"
    assert result == raw_dir / "paper_2.pdf"
    assert result.read_bytes() == body


def test_fetch_html_picks_unique_name_when_target_exists(tmp_path, capsys):
    """Two blog posts both titled 'Introduction' must NOT collide. The
    user-facing 'Saved: ...' echo must also reflect the renamed path —
    otherwise the message lies about where the file actually went."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / "Introduction.md").write_text("first blog post body")

    resp = _fake_response(body=b"<html>", headers={"Content-Type": "text/html"})

    second_md = "# Introduction\n\nA completely different second blog post body. " * 10
    fake_meta = MagicMock()
    fake_meta.title = "Introduction"

    with patch("urllib.request.urlopen", return_value=resp), \
         patch("trafilatura.fetch_url", return_value="<html>...</html>"), \
         patch("trafilatura.extract", return_value=second_md), \
         patch("trafilatura.extract_metadata", return_value=fake_meta):
        result = fetch_url_to_raw("https://blog2.example.com/post", tmp_path)

    assert (raw_dir / "Introduction.md").read_text() == "first blog post body"
    assert result == raw_dir / "Introduction_2.md"
    assert result.read_text() == second_md
    out = capsys.readouterr().out
    assert "Saved: raw/Introduction_2.md" in out


def test_fetch_pdf_uses_post_redirect_url_for_filename(tmp_path):
    """When urllib follows a redirect (DOI → publisher CDN, short URLs,
    etc.), the filename must be derived from the final URL — not the
    user's original input — when the response has no Content-Disposition
    to override either."""
    body = b"%PDF-1.7\n..."
    resp = _fake_response(
        body=body,
        headers={"Content-Type": "application/pdf"},  # NO Content-Disposition
    )
    # urllib's HTTPResponse.geturl() returns the post-redirect URL
    resp.geturl = lambda: "https://publisher.example.com/articles/2024/great-paper.pdf"

    with patch("urllib.request.urlopen", return_value=resp):
        result = fetch_url_to_raw("https://doi.org/10.1234/abc", tmp_path)

    # Filename comes from the redirected URL's basename, not "abc"
    assert result is not None
    assert result.name == "great-paper.pdf"


def test_add_single_file_returns_added_on_success(tmp_path, close_coroutine_run):
    """Tri-state return contract: ``"added"`` when the file was newly
    indexed. URL-ingest uses this to decide whether to keep / unlink
    the just-downloaded file."""
    from openkb.cli import add_single_file
    from openkb.converter import ConvertResult

    # Build a minimal KB scaffold
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / ".openkb" / "hashes.json").write_text("{}")
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "log.md").write_text("")

    doc = tmp_path / "raw" / "x.md"
    doc.write_text("# Hello")
    source_path = tmp_path / "wiki" / "sources" / "x.md"
    source_path.write_text("# Hello converted")

    mock_result = ConvertResult(
        raw_path=doc, source_path=source_path,
        is_long_doc=False, file_hash="cafe" * 16,
    )

    with patch("openkb.cli.convert_document", return_value=mock_result), \
         patch("openkb.cli.asyncio.run", side_effect=close_coroutine_run):
        outcome = add_single_file(doc, tmp_path)

    assert outcome == "added"


def test_add_single_file_returns_skipped_on_dedup(tmp_path):
    from openkb.cli import add_single_file
    from openkb.converter import ConvertResult

    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / ".openkb" / "hashes.json").write_text("{}")
    (tmp_path / "raw").mkdir()
    doc = tmp_path / "raw" / "x.md"
    doc.write_text("# Hello")

    skipped = ConvertResult(skipped=True)
    with patch("openkb.cli.convert_document", return_value=skipped):
        outcome = add_single_file(doc, tmp_path)

    assert outcome == "skipped"


def test_add_single_file_returns_failed_on_pipeline_error(tmp_path, raise_after_closing_coroutine):
    """A pipeline failure (e.g. transient LLM error during compilation)
    must be distinguishable from dedup-skip, so URL-ingest can preserve
    the raw file for retry instead of deleting it."""
    from openkb.cli import add_single_file
    from openkb.converter import ConvertResult

    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / ".openkb" / "hashes.json").write_text("{}")
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "log.md").write_text("")

    doc = tmp_path / "raw" / "x.md"
    doc.write_text("# Hello")
    source_path = tmp_path / "wiki" / "sources" / "x.md"
    source_path.write_text("# Hello")

    mock_result = ConvertResult(
        raw_path=doc, source_path=source_path,
        is_long_doc=False, file_hash="cafe" * 16,
    )

    # Make both compile attempts raise to drive the failure path.
    with patch("openkb.cli.convert_document", return_value=mock_result), \
         patch(
             "openkb.cli.asyncio.run",
             side_effect=raise_after_closing_coroutine(RuntimeError("LLM 503")),
         ), \
         patch("openkb.cli.time.sleep"):
        outcome = add_single_file(doc, tmp_path)

    assert outcome == "failed"


def test_url_ingest_cleans_up_orphan_on_dedup_skip(tmp_path, monkeypatch):
    """End-to-end: when the URL-fetched file is already in the registry,
    add_single_file returns "skipped" and the CLI unlinks it from raw/
    so the user doesn't accumulate untracked duplicates."""
    from click.testing import CliRunner
    from openkb.cli import cli
    from openkb.converter import ConvertResult

    # Minimal KB
    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / ".openkb" / "hashes.json").write_text("{}")
    (tmp_path / "raw").mkdir()

    # Fake the URL fetch — write directly to where url_ingest would
    fetched_path = tmp_path / "raw" / "paper.pdf"
    fetched_path.write_bytes(b"%PDF-fake")

    runner = CliRunner()
    # fetch_url_to_raw is lazy-imported inside `add`, so patch it at the
    # source module — that's where the `from ... import` resolves.
    with patch("openkb.cli._find_kb_dir", return_value=tmp_path), \
         patch("openkb.url_ingest.fetch_url_to_raw", return_value=fetched_path), \
         patch("openkb.cli.convert_document",
               return_value=ConvertResult(skipped=True)):
        result = runner.invoke(cli, ["add", "https://example.com/paper.pdf"])

    assert result.exit_code == 0, result.output
    assert "[SKIP]" in result.output
    # Orphan cleanup: the URL-fetched file must be gone from raw/.
    assert not fetched_path.exists()


def test_url_ingest_keeps_raw_file_on_pipeline_failure(tmp_path, raise_after_closing_coroutine):
    """The point of the tri-state return: a pipeline failure (e.g. LLM
    timeout during compilation) must NOT delete the downloaded file —
    the user can retry without re-downloading, and we don't lose data
    when indexing has already succeeded but compilation hasn't."""
    from click.testing import CliRunner
    from openkb.cli import cli
    from openkb.converter import ConvertResult

    (tmp_path / ".openkb").mkdir()
    (tmp_path / ".openkb" / "config.yaml").write_text("model: gpt-4o-mini\n")
    (tmp_path / ".openkb" / "hashes.json").write_text("{}")
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "log.md").write_text("")

    fetched_path = tmp_path / "raw" / "paper.pdf"
    fetched_path.write_bytes(b"%PDF-fake")
    source_path = tmp_path / "wiki" / "sources" / "paper.md"
    source_path.write_text("# fake")

    mock_result = ConvertResult(
        raw_path=fetched_path, source_path=source_path,
        is_long_doc=False, file_hash="cafe" * 16,
    )

    runner = CliRunner()
    with patch("openkb.cli._find_kb_dir", return_value=tmp_path), \
         patch("openkb.url_ingest.fetch_url_to_raw", return_value=fetched_path), \
         patch("openkb.cli.convert_document", return_value=mock_result), \
         patch(
             "openkb.cli.asyncio.run",
             side_effect=raise_after_closing_coroutine(RuntimeError("LLM 503")),
         ), \
         patch("openkb.cli.time.sleep"):
        result = runner.invoke(cli, ["add", "https://example.com/paper.pdf"])

    assert result.exit_code == 0, result.output
    assert "[ERROR] Compilation failed" in result.output
    # The raw file must be preserved so the user can retry.
    assert fetched_path.exists()
