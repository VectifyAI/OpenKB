"""Tests for `openkb feedback` — the prefilled-GitHub-issue feedback flow."""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from click.testing import CliRunner

from openkb.cli import (
    _FEEDBACK_REPO,
    _build_feedback_url,
    _collect_feedback_diagnostics,
    cli,
)

# ---------------------------------------------------------------------------
# _build_feedback_url
# ---------------------------------------------------------------------------


def _parse(url: str) -> dict:
    """Parse a prefilled-issue URL into its query-param dict (single values)."""
    parts = urlparse(url)
    qs = parse_qs(parts.query)
    # parse_qs yields lists; flatten the singletons we care about.
    return {k: v[0] for k, v in qs.items()}


def test_build_url_points_at_correct_repo_issue_new():
    url = _build_feedback_url("hello", "bug", {})
    parts = urlparse(url)
    assert parts.scheme == "https"
    assert parts.netloc == "github.com"
    assert parts.path == f"/{_FEEDBACK_REPO}/issues/new"


def test_build_url_title_includes_type_prefix():
    url = _build_feedback_url("attach fails on docx", "bug", {})
    params = _parse(url)
    assert params["title"] == "[bug] attach fails on docx"


def test_build_url_title_omits_prefix_for_other_type():
    """'other' is the catch-all; don't pollute the title with [other]."""
    url = _build_feedback_url("just a comment", "other", {})
    params = _parse(url)
    assert params["title"] == "just a comment"


def test_build_url_title_truncated_at_60_chars():
    long_msg = "a" * 200
    url = _build_feedback_url(long_msg, "bug", {})
    params = _parse(url)
    # 60 chars + ellipsis + prefix
    assert params["title"] == "[bug] " + ("a" * 60) + "…"


def test_build_url_title_uses_first_line_only():
    """A multi-line message should only use line 1 for the title."""
    url = _build_feedback_url("short title\n\ndetailed body here", "feature", {})
    params = _parse(url)
    assert params["title"] == "[feature] short title"


def test_build_url_label_set_for_bug():
    url = _build_feedback_url("x", "bug", {})
    params = _parse(url)
    assert params["labels"] == "bug"


def test_build_url_label_mapped_for_feature():
    """Feature → 'enhancement' (GitHub's conventional label)."""
    url = _build_feedback_url("x", "feature", {})
    params = _parse(url)
    assert params["labels"] == "enhancement"


def test_build_url_no_label_for_other():
    url = _build_feedback_url("x", "other", {})
    params = _parse(url)
    assert "labels" not in params


def test_build_url_diagnostics_attached_when_provided():
    url = _build_feedback_url(
        "x",
        "bug",
        {"openkb": "1.2.3", "python": "3.12.0", "platform": "Linux 6.0"},
    )
    params = _parse(url)
    assert "Diagnostics" in params["body"]
    assert "**openkb**: 1.2.3" in params["body"]
    assert "**python**: 3.12.0" in params["body"]
    assert "**platform**: Linux 6.0" in params["body"]


def test_build_url_no_diagnostics_block_when_empty():
    """When called with an empty dict the function omits the details block.
    Defensive: the CLI always passes a populated dict, but keeping the
    branch tested guards against accidental regression."""
    url = _build_feedback_url("just the message", "bug", {})
    params = _parse(url)
    assert params["body"] == "just the message"
    assert "Diagnostics" not in params["body"]
    assert "<details>" not in params["body"]


# ---------------------------------------------------------------------------
# _collect_feedback_diagnostics
# ---------------------------------------------------------------------------


def test_collect_diagnostics_returns_minimal_non_sensitive_set(tmp_path):
    """Diagnostics should be the small known set — no paths, no env vars."""

    class _Ctx:
        obj = None

    with patch("openkb.cli._find_kb_dir", return_value=None):
        info = _collect_feedback_diagnostics(_Ctx())

    assert set(info.keys()) == {"openkb", "python", "platform", "kb_initialised"}
    assert info["kb_initialised"] == "no"
    # Defensive: no path-like values that would leak the user's home dir.
    for v in info.values():
        assert "/Users/" not in v
        assert "/home/" not in v


def test_collect_diagnostics_flags_kb_present(tmp_path):
    class _Ctx:
        obj = None

    with patch("openkb.cli._find_kb_dir", return_value=tmp_path):
        info = _collect_feedback_diagnostics(_Ctx())

    assert info["kb_initialised"] == "yes"


# ---------------------------------------------------------------------------
# CLI: openkb feedback
# ---------------------------------------------------------------------------


def test_feedback_one_liner_opens_browser_with_url():
    """Default path: build URL, print it for copy-fallback, and open browser."""
    runner = CliRunner()
    with patch("webbrowser.open") as mock_open:
        result = runner.invoke(cli, ["feedback", "--type", "bug", "test message"])

    assert result.exit_code == 0, result.output
    mock_open.assert_called_once()
    called_url = mock_open.call_args[0][0]
    assert called_url.startswith("https://github.com/VectifyAI/OpenKB/issues/new?")
    # The URL is also printed so the user has a copy if auto-open fails.
    assert called_url in result.output


def test_feedback_empty_message_aborts_with_exit_1():
    """Interactive mode: if user submits nothing, abort cleanly (no issue URL)."""
    runner = CliRunner()
    # input="" simulates Ctrl-D on an empty stdin.
    result = runner.invoke(cli, ["feedback", "--type", "bug"], input="")
    assert result.exit_code == 1
    assert "No feedback provided" in result.output


def test_feedback_prompts_for_type_when_not_given_via_flag():
    """If --type isn't on the command line and stdin is a TTY, prompt the user."""
    runner = CliRunner()
    with patch("webbrowser.open"), patch("openkb.cli._stdin_is_tty", return_value=True):
        result = runner.invoke(
            cli,
            ["feedback", "missing-type prompt test"],
            input="feature\n",
        )

    assert result.exit_code == 0
    # The URL printed for fallback-copy carries the chosen type's label.
    url_line = [ln for ln in result.output.splitlines() if "issues/new" in ln][-1]
    assert "labels=enhancement" in url_line


# ---------------------------------------------------------------------------
# Regressions from the self-review on PR #53
# ---------------------------------------------------------------------------


def test_feedback_skips_type_prompt_when_stdin_is_not_a_tty():
    """In CI / piped contexts the second prompt would hang or abort
    confusingly — the command must fall through to a default."""
    runner = CliRunner()
    with patch("webbrowser.open"), patch("openkb.cli._stdin_is_tty", return_value=False):
        result = runner.invoke(cli, ["feedback", "non-tty feedback"])

    assert result.exit_code == 0, result.output
    # Non-TTY → falls back to "other", which has no label param
    url_line = [ln for ln in result.output.splitlines() if "issues/new" in ln][-1]
    assert "labels=" not in url_line
    # And the title should NOT have a type prefix
    assert "%5Bother%5D" not in url_line  # urlencoded "[other]"


def test_feedback_warns_when_webbrowser_open_returns_false():
    """`webbrowser.open` returns False on headless boxes without raising —
    the command must surface that to the user, not silently pretend
    success."""
    runner = CliRunner()
    with patch("webbrowser.open", return_value=False) as mock_open:
        result = runner.invoke(
            cli,
            ["feedback", "--type", "bug", "headless test"],
        )

    assert result.exit_code == 0, result.output
    mock_open.assert_called_once()
    # The success-confirmation message must NOT appear
    assert "Opened GitHub in your browser" not in result.output
    # The user must see a clear "no browser available" indication
    assert "no browser available" in result.output


def test_feedback_confirms_when_webbrowser_open_succeeds():
    runner = CliRunner()
    with patch("webbrowser.open", return_value=True):
        result = runner.invoke(
            cli,
            ["feedback", "--type", "bug", "happy path"],
        )

    assert result.exit_code == 0, result.output
    assert "Opened GitHub in your browser" in result.output


def test_openkb_version_helper_matches_package_version():
    """`_openkb_version` in cli.py must delegate to `openkb.__version__`
    so the chat REPL and the feedback issue body never disagree on the
    fallback string."""
    from openkb import __version__
    from openkb.cli import _openkb_version

    assert _openkb_version() == __version__
