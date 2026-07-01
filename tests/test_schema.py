"""Tests for openkb.schema constants (wiki AGENTS_MD schema doc)."""

from openkb.schema import AGENTS_MD


def test_agents_md_documents_type_and_description():
    # The new OKF-aligned frontmatter fields must be documented.
    assert "type:" in AGENTS_MD
    assert "description:" in AGENTS_MD
    # The code-manages-frontmatter contract must remain intact.
    assert "managed by code" in AGENTS_MD
