"""Test compiler and query integration for claims."""

from __future__ import annotations

import json

import pytest

from openkb import frontmatter
from openkb.agent.compiler import (
    _CLAIMS_FIELD_GUIDANCE,
    _CONCEPT_UPDATE_USER,
    _ENTITY_UPDATE_USER,
    _SUMMARY_USER,
    _claims_from_model,
    _write_concept,
    _write_entity,
    _write_summary,
)
from openkb.agent.tools import read_current_claims
from openkb.claims import claim_id, normalize_claim


def claim(
    text: str,
    *,
    as_of: str = "2026-01-01",
    status: str = "validated",
    anchor: str = "fixture:1",
    **extra,
):
    value = {
        "text": text,
        "as_of": as_of,
        "status": status,
        "source_anchor": anchor,
        "supersedes": [],
    }
    value.update(extra)
    normalized = normalize_claim(value)
    assert normalized is not None
    return normalized


def test_compiler_prompts_request_code_managed_claims():
    for prompt in (_SUMMARY_USER, _CONCEPT_UPDATE_USER, _ENTITY_UPDATE_USER):
        assert '"source_anchor"' in prompt
        assert '"as_of"' in prompt
        assert '"status"' in prompt
        assert '"supersedes"' in prompt
    assert '"id"' in _CLAIMS_FIELD_GUIDANCE
    assert 'Do not set the "id" field' in _CLAIMS_FIELD_GUIDANCE
    assert "latest state that the source evidence supports" in _CONCEPT_UPDATE_USER


def test_frontmatter_structured_claims_round_trip():
    stored = claim("The synthetic value is 10.")
    page = (
        frontmatter.block(
            [frontmatter.kv_line("type", "Summary"), frontmatter.json_line("claims", [stored])]
        )
        + "The page contains a synthetic value."
    )
    parsed = frontmatter.parse(page)
    assert parsed["claims"][0]["id"] == stored["id"]
    assert "\nclaims: " in page
    assert "--- not a delimiter ---" not in page


def test_summary_writer_stores_single_line_claims(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    stored = claim("The synthetic summary value is 10.")
    _write_summary(
        wiki,
        "fixture",
        "# Summary\n\nThe page contains a synthetic value.",
        claims=[stored],
    )
    text = (wiki / "summaries/fixture.md").read_text(encoding="utf-8")
    assert frontmatter.parse(text)["claims"][0]["text"] == stored["text"]
    assert len([line for line in text.splitlines() if line.startswith("claims:")]) == 1


def test_fresh_summary_write_normalizes_model_claim_fields(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    raw = {
        "id": "model-invented",
        "text": "The raw synthetic value is 10.",
        "as_of": "2026-01-01",
        "status": "validated",
        "source_anchor": "fixture:raw",
        "supersedes": [],
        "superseded_by": ["model-invented-reverse-link"],
    }
    _write_summary(wiki, "raw", "# Summary\n\nThe page contains a synthetic value.", claims=[raw])
    stored = frontmatter.parse((wiki / "summaries/raw.md").read_text())["claims"][0]
    assert stored["id"] != "model-invented"
    assert stored["superseded_by"] == []


@pytest.mark.parametrize("page_kind", ["summary", "concept", "entity"])
def test_model_first_party_is_capped_before_page_write(tmp_path, page_kind):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    old = claim(
        "The stored value is 10.",
        anchor="fixture:first-party",
        authority="first_party",
    )
    raw_model_claim = {
        "text": "The compiler model claims that the value is 12.",
        "as_of": "2026-01-02",
        "status": "validated",
        "source_anchor": "fixture:model",
        "authority": "first_party",
        "supersedes": [old["id"]],
    }
    model_claims = _claims_from_model(
        {"claims": [raw_model_claim]},
        log_label=f"test:{page_kind}",
    )
    assert model_claims is not None
    assert model_claims[0]["authority"] == "document"

    if page_kind == "summary":
        _write_summary(wiki, "fixture", "# Summary\n\nThe old value is 10.", claims=[old])
        _write_summary(wiki, "fixture", "# Summary\n\nThe new value is 12.", claims=model_claims)
        path = wiki / "summaries/fixture.md"
    elif page_kind == "concept":
        _write_concept(
            wiki,
            "fixture",
            "# Concept\n\nThe old value is 10.",
            "summaries/old.md",
            False,
            claims=[old],
        )
        _write_concept(
            wiki,
            "fixture",
            "# Concept\n\nThe new value is 12.",
            "summaries/new.md",
            True,
            claims=model_claims,
        )
        path = wiki / "concepts/fixture.md"
    else:
        _write_entity(
            wiki,
            "fixture",
            "# Entity\n\nThe old value is 10.",
            "summaries/old.md",
            False,
            claims=[old],
        )
        _write_entity(
            wiki,
            "fixture",
            "# Entity\n\nThe new value is 12.",
            "summaries/new.md",
            True,
            claims=model_claims,
        )
        path = wiki / "entities/fixture.md"

    stored = frontmatter.parse(path.read_text())["claims"]
    by_id = {item["id"]: item for item in stored}
    assert by_id[old["id"]]["status"] == "validated"
    assert by_id[old["id"]]["superseded_by"] == []
    model_id = claim_id(
        raw_model_claim["text"],
        raw_model_claim["as_of"],
        raw_model_claim["source_anchor"],
    )
    assert by_id[model_id]["authority"] == "document"


def test_summary_rewrite_merges_existing_claim_history(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    old = claim("The synthetic summary value was 10.", anchor="fixture:old")
    _write_summary(wiki, "fixture", "# Summary\n\nThe old value is 10.", claims=[old])
    replacement = claim(
        "The synthetic summary value is 12.",
        as_of="2026-01-02",
        anchor="fixture:new",
        supersedes=[old["id"]],
    )
    _write_summary(wiki, "fixture", "# Summary\n\nThe new value is 12.", claims=[replacement])
    parsed = frontmatter.parse((wiki / "summaries/fixture.md").read_text())
    by_id = {item["id"]: item for item in parsed["claims"]}
    assert by_id[old["id"]]["status"] == "superseded"
    assert replacement["id"] in by_id[old["id"]]["superseded_by"]


def test_concept_update_merges_history_and_preserves_old_body_history(tmp_path):
    wiki = tmp_path / "wiki"
    concepts = wiki / "concepts"
    concepts.mkdir(parents=True)
    old = claim("The synthetic threshold was 10.", anchor="fixture:old")
    _write_concept(
        wiki,
        "synthetic-threshold",
        "# Threshold\n\nThe old threshold is 10.",
        "summaries/old.md",
        False,
        claims=[old],
    )
    replacement = claim(
        "The synthetic threshold is 12.",
        as_of="2026-01-02",
        anchor="fixture:new",
        supersedes=[old["id"]],
    )
    _write_concept(
        wiki,
        "synthetic-threshold",
        "# Threshold\n\nThe new threshold is 12.",
        "summaries/new.md",
        True,
        claims=[replacement],
    )
    parsed = frontmatter.parse((concepts / "synthetic-threshold.md").read_text())
    by_id = {item["id"]: item for item in parsed["claims"]}
    assert by_id[old["id"]]["status"] == "superseded"
    assert replacement["id"] in by_id[old["id"]]["superseded_by"]
    assert "The new threshold is 12." in (concepts / "synthetic-threshold.md").read_text()


def test_entity_writer_round_trips_claims(tmp_path):
    stored = claim("The synthetic entity is a document fixture.", anchor="fixture:entity")
    _write_entity(
        tmp_path,
        "synthetic-entity",
        "# Entity\n\nThe page contains a synthetic value.",
        "summaries/fixture.md",
        False,
        type_="other",
        claims=[stored],
    )
    path = tmp_path / "entities/synthetic-entity.md"
    assert frontmatter.parse(path.read_text())["claims"][0]["id"] == stored["id"]


def test_query_read_helper_returns_validated_only_by_default(tmp_path):
    wiki = tmp_path / "wiki"
    page = wiki / "summaries/fixture.md"
    page.parent.mkdir(parents=True)
    validated = claim("The synthetic value is 10.")
    proposed = claim(
        "The proposed synthetic value is 12.",
        status="proposed",
        anchor="fixture:proposal",
        authority="assistant",
    )
    page.write_text(
        frontmatter.block(
            [
                frontmatter.kv_line("type", "Summary"),
                frontmatter.json_line("claims", [validated, proposed]),
            ]
        )
        + "The page contains a synthetic value."
    )
    strict = json.loads(read_current_claims("summaries/fixture.md", str(wiki)))
    broad = json.loads(
        read_current_claims("summaries/fixture.md", str(wiki), include_proposed=True)
    )
    assert [item["text"] for item in strict] == [validated["text"]]
    assert {item["text"] for item in broad} == {validated["text"], proposed["text"]}
    assert (
        claim_id(validated["text"], validated["as_of"], validated["source_anchor"])
        == validated["id"]
    )


def test_query_read_helper_rejects_path_escape(tmp_path):
    assert read_current_claims("../outside.md", str(tmp_path / "wiki")).startswith("Access denied")
