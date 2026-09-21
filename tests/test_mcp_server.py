"""Tests for openkb.mcp_server (MCP tools: list_taxonomy, search_wiki, etc.)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from openkb.mcp_server import (
    _resolve_kb,
    find_kb_dir,
    get_content_tool,
    get_status,
    list_documents_tool,
    list_kbs,
    list_taxonomy,
    search_wiki,
)


def _make_kb(tmp_path):
    """Create a minimal KB (``.openkb/`` marker + a few wiki pages)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".openkb").mkdir()
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "summaries").mkdir(parents=True)
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts" / "attention.md").write_text(
        '---\ndescription: "How attention works"\n---\n\n# Attention\n\nBody.',
        encoding="utf-8",
    )
    (tmp_path / "wiki" / "summaries" / "doc.md").write_text(
        '---\ndescription: "Overview"\n---\n\n# Doc\n\nDetails about field_xyz appear here.',
        encoding="utf-8",
    )
    return tmp_path


class TestFindKbDir:
    def test_finds_kb_at_cwd(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        assert find_kb_dir() == tmp_path.resolve()

    def test_finds_kb_by_walking_up_from_subdirectory(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)

        assert find_kb_dir() == tmp_path.resolve()

    def test_falls_back_to_global_default_kb(self, tmp_path, monkeypatch):
        no_kb_cwd = tmp_path / "elsewhere"
        no_kb_cwd.mkdir()
        kb_dir = _make_kb(tmp_path / "the-kb")
        monkeypatch.chdir(no_kb_cwd)

        with patch(
            "openkb.mcp_server.load_global_config",
            return_value={"default_kb": str(kb_dir)},
        ):
            assert find_kb_dir() == kb_dir.resolve()

    def test_returns_none_when_no_kb_found_anywhere(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with patch("openkb.mcp_server.load_global_config", return_value={}):
            assert find_kb_dir() is None


class TestMcpListTaxonomy:
    def test_lists_items_as_plain_dicts(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = list_taxonomy()

        assert result == [
            {
                "kind": "concept",
                "slug": "attention",
                "path": "concepts/attention.md",
                "brief": "How attention works",
                "type": None,
            }
        ]

    def test_no_kb_raises_clear_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with patch("openkb.mcp_server.load_global_config", return_value={}):
            with pytest.raises(ValueError, match="No knowledge base found"):
                list_taxonomy()


class TestMcpSearchWiki:
    def test_finds_hit_in_summaries_tier(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = search_wiki("field_xyz")

        assert result["briefs"] == []
        assert len(result["summaries"]) == 1
        assert result["summaries"][0]["path"] == "summaries/doc.md"
        assert result["summaries"][0]["locator"] == {"kind": "line", "value": 4}
        assert result["sources"] == []
        assert result["explorations"] == []

    def test_scope_restricts_tiers(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = search_wiki("field_xyz", scope=["briefs"])

        assert set(result.keys()) == {"briefs"}

    def test_invalid_scope_raises(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(ValueError, match="Unknown scope"):
            search_wiki("field_xyz", scope=["not-a-tier"])


# ---------------------------------------------------------------------------
# _resolve_kb / multi-vault
# ---------------------------------------------------------------------------


class TestResolveKb:
    def test_none_falls_back_to_find_kb_dir(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        assert _resolve_kb(None) == tmp_path.resolve()

    def test_none_raises_when_no_kb_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with patch("openkb.mcp_server.load_global_config", return_value={}):
            with pytest.raises(ValueError, match="No knowledge base found"):
                _resolve_kb(None)

    def test_explicit_path_used_directly(self, tmp_path, monkeypatch):
        # cwd is a different, unrelated directory - only an explicit `kb`
        # path should be used, not cwd-walk.
        other_cwd = tmp_path / "elsewhere"
        other_cwd.mkdir()
        kb_dir = _make_kb(tmp_path / "the-kb")
        monkeypatch.chdir(other_cwd)

        assert _resolve_kb(str(kb_dir)) == kb_dir.resolve()

    def test_registered_name_resolved_via_config(self, tmp_path, monkeypatch):
        kb_dir = _make_kb(tmp_path / "my-kb")
        monkeypatch.chdir(tmp_path)

        with patch("openkb.mcp_server.resolve_kb_alias", return_value=kb_dir):
            assert _resolve_kb("my-kb") == kb_dir

    def test_unknown_name_raises_with_known_kbs_listed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        with patch("openkb.mcp_server.resolve_kb_alias", side_effect=ValueError("bad name")):
            with patch(
                "openkb.mcp_server.registered_kbs", return_value=[("alpha", tmp_path / "alpha")]
            ):
                with pytest.raises(ValueError, match="alpha"):
                    _resolve_kb("nonexistent")


# ---------------------------------------------------------------------------
# list_kbs
# ---------------------------------------------------------------------------


class TestMcpListKbs:
    def test_returns_registered_kbs_as_dicts(self, tmp_path):
        with patch(
            "openkb.mcp_server.registered_kbs",
            return_value=[("alpha", tmp_path / "alpha"), ("beta", tmp_path / "beta")],
        ):
            result = list_kbs()

        assert result == [
            {"name": "alpha", "path": str(tmp_path / "alpha")},
            {"name": "beta", "path": str(tmp_path / "beta")},
        ]


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


class TestMcpGetStatus:
    def test_returns_kb_dir_and_counts(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = get_status()

        assert result["kb_dir"] == str(tmp_path.resolve())
        assert result["counts"]["concepts"] == 1
        assert result["counts"]["summaries"] == 1
        assert result["total_indexed"] == 0

    def test_explicit_kb_path_used(self, tmp_path, monkeypatch):
        other_cwd = tmp_path / "elsewhere"
        other_cwd.mkdir()
        kb_dir = _make_kb(tmp_path / "the-kb")
        monkeypatch.chdir(other_cwd)

        result = get_status(kb=str(kb_dir))

        assert result["kb_dir"] == str(kb_dir.resolve())


# ---------------------------------------------------------------------------
# list_documents (MCP tool)
# ---------------------------------------------------------------------------


class TestMcpListDocuments:
    def test_lists_summaries_as_plain_dicts(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = list_documents_tool()

        assert result == [
            {"kind": "summary", "slug": "doc", "path": "summaries/doc.md", "brief": "Overview"}
        ]

    def test_kind_filter(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        assert list_documents_tool(kind="exploration") == []


# ---------------------------------------------------------------------------
# get_content (MCP tool)
# ---------------------------------------------------------------------------


class TestMcpGetContent:
    def test_reads_a_concept_page(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = get_content_tool("attention", kind="concept")

        assert len(result) == 1
        assert result[0]["error"] is None
        assert "Body." in result[0]["content"]

    def test_returns_error_entry_for_missing_slug(self, tmp_path, monkeypatch):
        _make_kb(tmp_path)
        monkeypatch.chdir(tmp_path)

        result = get_content_tool("nonexistent", kind="concept")

        assert len(result) == 1
        assert result[0]["content"] is None
        assert "not found" in result[0]["error"].lower()

    def test_kb_param_selects_explicit_kb(self, tmp_path, monkeypatch):
        other_cwd = tmp_path / "elsewhere"
        other_cwd.mkdir()
        kb_dir = _make_kb(tmp_path / "the-kb")
        monkeypatch.chdir(other_cwd)

        result = get_content_tool("attention", kind="concept", kb=str(kb_dir))

        assert result[0]["error"] is None
