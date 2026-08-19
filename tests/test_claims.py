"""Test deterministic claim behavior."""

from __future__ import annotations

import hashlib
import json

import pytest

from openkb.claims import (
    AUTHORITY_ROLES,
    CLAIM_STATUSES,
    as_of_allows_supersession,
    claim_id,
    claims_for_prompt,
    current_claims,
    merge_claims,
    normalize_claim,
)


def raw(**overrides):
    claim = {
        "text": "A synthetic fact is true.",
        "as_of": "2026-01-01",
        "status": "validated",
        "source_anchor": "fixture:turn-1",
        "authority": "document",
        "supersedes": [],
    }
    claim.update(overrides)
    return claim


class TestNormalization:
    def test_id_is_code_owned_and_deterministic(self):
        claim = normalize_claim(raw(id="model-invented-id"))
        assert claim is not None
        expected = hashlib.sha256(
            '["A synthetic fact is true.","2026-01-01","fixture:turn-1"]'.encode()
        ).hexdigest()[:16]
        assert claim["id"] == expected
        assert claim["id"] != "model-invented-id"

    def test_required_fields_and_statuses(self):
        for status in CLAIM_STATUSES:
            assert normalize_claim(raw(status=status)) is not None
        assert normalize_claim(raw(text="")) is None
        assert normalize_claim(raw(as_of="yesterday")) is None
        assert normalize_claim(raw(source_anchor="")) is None
        assert normalize_claim(raw(status="confirmed")) is None

    def test_optional_generic_authority_is_validated(self):
        assert AUTHORITY_ROLES == {"first_party", "assistant", "document"}
        first_party = normalize_claim(raw(authority="first_party"))
        assert first_party is not None
        assert first_party["authority"] == "first_party"
        assert normalize_claim(raw(authority="provider-specific")) is None
        no_authority = normalize_claim(raw(authority=None))
        assert no_authority is not None
        assert "authority" not in no_authority

    @pytest.mark.parametrize(
        ("incoming", "old", "allowed"),
        [
            ("2026-01-02", "2026-01-01", True),
            ("2026-01-01", "2026-01-01", True),
            ("2025-12-31", "2026-01-01", False),
            ("2026-01-01T12:00:00Z", "2026-01-01T07:00:00-05:00", True),
        ],
    )
    def test_chronology_guard(self, incoming, old, allowed):
        assert as_of_allows_supersession(incoming, old) is allowed


class TestSyntheticCorrectionRegression:
    """A first-party correction must supersede old assistant claims.

    The old assistant claims have a `status` value of `proposed`.
    """

    def test_without_correction_stale_proposal_remains_proposed(self):
        revision = raw(
            text="Synthetic Device uses revision A.",
            status="proposed",
            authority="assistant",
        )
        operating_limit = raw(
            text="Synthetic Device has an operating limit of 40 units.",
            source_anchor="fixture:turn-2",
            status="proposed",
            authority="assistant",
        )
        merged = merge_claims([], [revision, operating_limit])
        assert [claim["text"] for claim in current_claims(merged)] == []
        assert {claim["text"] for claim in current_claims(merged, include_proposed=True)} == {
            revision["text"],
            operating_limit["text"],
        }

    def test_first_party_correction_is_current_and_old_records_remain(self):
        revision = raw(
            text="Synthetic Device uses revision A.",
            status="proposed",
            authority="assistant",
        )
        operating_limit = raw(
            text="Synthetic Device has an operating limit of 40 units.",
            source_anchor="fixture:turn-2",
            status="proposed",
            authority="assistant",
        )
        revision_id = claim_id(revision["text"], revision["as_of"], revision["source_anchor"])
        operating_limit_id = claim_id(
            operating_limit["text"],
            operating_limit["as_of"],
            operating_limit["source_anchor"],
        )
        correction_revision = raw(
            text="Synthetic Device uses revision B.",
            as_of="2026-01-02",
            source_anchor="fixture:turn-3",
            authority="first_party",
            supersedes=[revision_id],
        )
        correction_limit = raw(
            text="Synthetic Device has an operating limit of 100 units.",
            as_of="2026-01-02",
            source_anchor="fixture:turn-3",
            authority="first_party",
            supersedes=[operating_limit_id],
        )

        merged = merge_claims(
            [],
            [revision, operating_limit, correction_revision, correction_limit],
        )
        correction_revision_id = claim_id(
            correction_revision["text"],
            correction_revision["as_of"],
            correction_revision["source_anchor"],
        )
        by_id = {claim["id"]: claim for claim in merged}
        assert by_id[revision_id]["status"] == "superseded"
        assert by_id[operating_limit_id]["status"] == "superseded"
        assert correction_revision["text"] in {claim["text"] for claim in current_claims(merged)}
        assert correction_limit["text"] in {claim["text"] for claim in current_claims(merged)}
        assert correction_revision["text"] not in {
            claim["text"]
            for claim in current_claims(merged, include_proposed=True)
            if claim["status"] == "superseded"
        }
        assert revision_id in by_id and operating_limit_id in by_id
        assert by_id[revision_id]["superseded_by"] == [correction_revision_id]

    def test_replay_is_idempotent(self):
        old = raw(text="The synthetic value was 10.", as_of="2026-01-01")
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        new = raw(
            text="The synthetic value is 12.",
            as_of="2026-01-02",
            source_anchor="fixture:turn-2",
            supersedes=[old_id],
        )
        once = merge_claims([old], [new])
        twice = merge_claims(once, [new])
        assert twice == once

    def test_duplicate_identity_cannot_inject_supersession_edge(self):
        claim_a = raw(
            text="Synthetic value A is 10.",
            authority="first_party",
            source_anchor="fixture:first-party-a",
        )
        claim_b = raw(
            text="Synthetic value B is 20.",
            authority="first_party",
            source_anchor="fixture:first-party-b",
        )
        claim_b_id = claim_id(claim_b["text"], claim_b["as_of"], claim_b["source_anchor"])
        duplicate_a = {
            **claim_a,
            "authority": "assistant",
            "supersedes": [claim_b_id],
        }
        merged = merge_claims([claim_a, claim_b], [duplicate_a])
        by_id = {claim["id"]: claim for claim in merged}
        assert by_id[claim_b_id]["status"] == "validated"
        assert by_id[claim_b_id]["superseded_by"] == []
        assert {item["text"] for item in current_claims(merged)} == {
            claim_a["text"],
            claim_b["text"],
        }

    def test_backward_time_link_is_rejected(self):
        old = raw(as_of="2026-02-01")
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        earlier = raw(
            text="The proposed synthetic value is 12.",
            as_of="2026-01-01",
            source_anchor="fixture:turn-0",
            supersedes=[old_id],
        )
        merged = merge_claims([old], [earlier])
        assert merged[0]["status"] == "validated"
        assert merged[0]["superseded_by"] == []

    def test_proposed_claim_cannot_remove_validated_current_fact(self):
        old = raw(text="The synthetic value is 10.")
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        proposed = raw(
            text="The proposed synthetic value is 12.",
            as_of="2026-01-02",
            source_anchor="fixture:proposal",
            status="proposed",
            supersedes=[old_id],
        )
        merged = merge_claims([old], [proposed])
        by_id = {claim["id"]: claim for claim in merged}
        assert by_id[old_id]["status"] == "validated"
        assert by_id[old_id]["superseded_by"] == []
        assert [item["text"] for item in current_claims(merged)] == [old["text"]]

    def test_duplicate_proposed_identity_cannot_launder_validated_status(self):
        old = raw(text="The synthetic value is 10.")
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        stored_proposal = raw(
            text="The proposed synthetic value is 12.",
            as_of="2026-01-02",
            source_anchor="fixture:proposal",
            status="proposed",
        )
        relabel = {**stored_proposal, "status": "validated", "supersedes": [old_id]}
        merged = merge_claims([old, stored_proposal], [relabel])
        by_id = {claim["id"]: claim for claim in merged}
        assert by_id[old_id]["status"] == "validated"
        assert by_id[old_id]["superseded_by"] == []
        assert [item["text"] for item in current_claims(merged)] == [old["text"]]

    def test_validated_assistant_cannot_supersede_first_party(self):
        old = raw(
            text="The synthetic value is 10.",
            authority="first_party",
        )
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        assistant = raw(
            text="The assistant claim conflicts with the stored claim.",
            as_of="2026-01-02",
            source_anchor="fixture:assistant",
            authority="assistant",
            status="validated",
            supersedes=[old_id],
        )
        merged = merge_claims([old], [assistant])
        by_id = {claim["id"]: claim for claim in merged}
        assistant_id = claim_id(assistant["text"], assistant["as_of"], assistant["source_anchor"])
        assert by_id[old_id]["status"] == "validated"
        assert by_id[old_id]["superseded_by"] == []
        assert by_id[assistant_id]["status"] == "proposed"
        assert [item["text"] for item in current_claims(merged)] == [old["text"]]

    def test_assistant_abandonment_cannot_remove_validated_document_fact(self):
        old = raw(
            text="The document value is 10.",
            authority="document",
        )
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        assistant = raw(
            text="The assistant claim marks the document fact as abandoned.",
            as_of="2026-01-02",
            source_anchor="fixture:assistant-abandonment",
            authority="assistant",
            status="abandoned",
            supersedes=[old_id],
        )
        merged = merge_claims([old], [assistant])
        by_id = {claim["id"]: claim for claim in merged}
        assert by_id[old_id]["status"] == "validated"
        assert by_id[old_id]["superseded_by"] == []
        assert [item["text"] for item in current_claims(merged)] == [old["text"]]

    def test_duplicate_assistant_identity_cannot_launder_first_party_authority(self):
        old = raw(
            text="The synthetic value is 10.",
            authority="first_party",
        )
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        stored_assistant = raw(
            text="The assistant claim conflicts with the stored claim.",
            as_of="2026-01-02",
            source_anchor="fixture:assistant",
            authority="assistant",
            status="validated",
        )
        relabel = {
            **stored_assistant,
            "authority": "first_party",
            "supersedes": [old_id],
        }
        merged = merge_claims([old, stored_assistant], [relabel])
        by_id = {claim["id"]: claim for claim in merged}
        assistant_id = claim_id(
            stored_assistant["text"],
            stored_assistant["as_of"],
            stored_assistant["source_anchor"],
        )
        assert by_id[old_id]["status"] == "validated"
        assert by_id[old_id]["superseded_by"] == []
        assert by_id[assistant_id]["authority"] == "assistant"
        assert by_id[assistant_id]["status"] == "proposed"
        assert [item["text"] for item in current_claims(merged)] == [old["text"]]

    def test_document_cannot_supersede_first_party(self):
        old = raw(
            text="The synthetic value is 10.",
            authority="first_party",
        )
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        document = raw(
            text="The document claim says that the value is 12.",
            as_of="2026-01-02",
            source_anchor="fixture:document",
            authority="document",
            supersedes=[old_id],
        )
        merged = merge_claims([old], [document])
        by_id = {claim["id"]: claim for claim in merged}
        assert by_id[old_id]["status"] == "validated"
        assert by_id[old_id]["superseded_by"] == []

    def test_first_party_can_supersede_first_party(self):
        old = raw(
            text="The synthetic measurement was 10.",
            authority="first_party",
        )
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        replacement = raw(
            text="The synthetic measurement is 12.",
            as_of="2026-01-02",
            source_anchor="fixture:first-party-replacement",
            authority="first_party",
            supersedes=[old_id],
        )
        merged = merge_claims([old], [replacement])
        by_id = {claim["id"]: claim for claim in merged}
        assert by_id[old_id]["status"] == "superseded"
        assert [item["text"] for item in current_claims(merged)] == [replacement["text"]]

    @pytest.mark.parametrize("authority", ["document", None])
    def test_generic_non_first_party_supersession_is_preserved(self, authority):
        old = raw(text="The synthetic measurement was 10.", authority=authority)
        old_id = claim_id(old["text"], old["as_of"], old["source_anchor"])
        replacement = raw(
            text="The synthetic measurement is 12.",
            as_of="2026-01-02",
            source_anchor="fixture:replacement",
            authority=authority,
            supersedes=[old_id],
        )
        merged = merge_claims([old], [replacement])
        by_id = {claim["id"]: claim for claim in merged}
        assert by_id[old_id]["status"] == "superseded"
        assert [item["text"] for item in current_claims(merged)] == [replacement["text"]]

    def test_supersession_is_page_local_and_self_links_are_ignored(self):
        local = raw(text="The local synthetic value is 10.")
        local_id = claim_id(local["text"], local["as_of"], local["source_anchor"])
        incoming = raw(
            text="The local synthetic value is 12.",
            as_of="2026-01-02",
            source_anchor="fixture:turn-2",
            supersedes=["foreign-page-id", local_id],
        )
        merged = merge_claims([local], [incoming])
        assert len(merged) == 2
        assert merged[0]["status"] == "superseded"
        self_ref = raw(
            text="This claim has a supersession link to itself.",
            source_anchor="fixture:turn-9",
        )
        self_id = claim_id(self_ref["text"], self_ref["as_of"], self_ref["source_anchor"])
        self_ref["supersedes"] = [self_id]
        assert merge_claims([], [self_ref])[0]["superseded_by"] == []


class TestReadHelpers:
    def test_prompt_serialization_is_compact_and_exposes_page_ids(self):
        claim = normalize_claim(raw())
        assert claim is not None
        serialized = claims_for_prompt([claim])
        parsed = json.loads(serialized)
        assert parsed[0]["id"] == claim["id"]
        assert parsed[0]["source_anchor"] == "fixture:turn-1"
        assert "superseded_by" not in parsed[0]

    def test_current_claims_does_not_mutate_input(self):
        claim = normalize_claim(raw())
        assert claim is not None
        original = [dict(claim)]
        assert current_claims(original) == original
        assert original == [dict(claim)]
