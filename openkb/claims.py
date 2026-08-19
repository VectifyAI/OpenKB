"""Manage deterministic claims in OpenKB page frontmatter.

Each claim is a JSON evidence record. OpenKB controls the `id` and
`superseded_by` fields. Input can contain a `supersedes` field. OpenKB validates
each supersession link in the `supersedes` field. An external adapter can define
the format of the `source_anchor` field before it calls this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

CLAIM_STATUSES = frozenset({"proposed", "validated", "superseded", "abandoned"})
AUTHORITY_ROLES = frozenset({"first_party", "assistant", "document"})
_ISO_AS_OF_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}"
    r"(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)?$"
)


def claim_id(text: str, as_of: str, source_anchor: str) -> str:
    """Return a stable `id` value from `text`, `as_of`, and `source_anchor`.

    Ignore a model-supplied `id` value. Use the first 16 characters of the
    SHA-256 digest.
    """
    payload = json.dumps(
        [text.strip(), as_of.strip(), source_anchor.strip()],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _parse_as_of(value: str) -> tuple[str, date | datetime] | None:
    """Parse the `as_of` field as an ISO date or datetime."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or not _ISO_AS_OF_RE.fullmatch(candidate):
        return None
    if len(candidate) == 10:
        try:
            return "date", date.fromisoformat(candidate)
        except ValueError:
            return None

    normalized = candidate.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if len(normalized) >= 5 and normalized[-5] in "+-" and normalized[-3] != ":":
        normalized = normalized[:-2] + ":" + normalized[-2:]
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return "datetime", parsed


def as_of_allows_supersession(incoming_as_of: str, old_as_of: str) -> bool:
    """Return `True` when the new `as_of` value is not earlier than the old value."""
    incoming = _parse_as_of(incoming_as_of)
    old = _parse_as_of(old_as_of)
    if incoming is None or old is None:
        return False
    if incoming[0] == "date" or old[0] == "date":
        incoming_date = _calendar_day(incoming[1])
        old_date = _calendar_day(old[1])
        return incoming_date >= old_date
    return incoming[1] >= old[1]


def _calendar_day(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def normalize_claim(
    raw: Any,
    *,
    log_label: str = "claim",
    preserve_superseded_by: bool = True,
) -> dict[str, Any] | None:
    """Validate one claim. Set its `id` field from the claim identity fields."""
    if not isinstance(raw, dict):
        logger.warning("%s: OpenKB dropped a claim that is not a JSON object.", log_label)
        return None

    text = raw.get("text")
    as_of = raw.get("as_of")
    status = raw.get("status")
    source_anchor = raw.get("source_anchor")
    if not isinstance(text, str) or not text.strip():
        logger.warning("%s: OpenKB dropped a claim because the `text` field is empty.", log_label)
        return None
    if not isinstance(as_of, str) or _parse_as_of(as_of) is None:
        logger.warning(
            "%s: OpenKB dropped a claim because the `as_of` field is invalid.",
            log_label,
        )
        return None
    if not isinstance(status, str) or status not in CLAIM_STATUSES:
        logger.warning(
            "%s: OpenKB dropped a claim because the `status` field is invalid.",
            log_label,
        )
        return None
    if not isinstance(source_anchor, str) or not source_anchor.strip():
        logger.warning(
            "%s: OpenKB dropped a claim because the `source_anchor` field is empty.",
            log_label,
        )
        return None

    authority = raw.get("authority")
    if authority is not None and (
        not isinstance(authority, str) or authority not in AUTHORITY_ROLES
    ):
        logger.warning(
            "%s: OpenKB dropped a claim because the `authority` field is invalid.",
            log_label,
        )
        return None
    if authority == "assistant" and status == "validated":
        logger.warning(
            "%s: OpenKB changed the assistant claim `status` value from `validated` to `proposed`.",
            log_label,
        )
        status = "proposed"

    text = text.strip()
    as_of = as_of.strip()
    source_anchor = source_anchor.strip()
    normalized: dict[str, Any] = {
        "id": claim_id(text, as_of, source_anchor),
        "text": text,
        "as_of": as_of,
        "status": status,
        "source_anchor": source_anchor,
        "supersedes": _string_list(raw.get("supersedes")),
        "superseded_by": (_string_list(raw.get("superseded_by")) if preserve_superseded_by else []),
    }
    if authority is not None:
        normalized["authority"] = authority
    return normalized


def normalize_claims(
    raw: Any,
    *,
    log_label: str = "claims",
    preserve_superseded_by: bool = True,
) -> list[dict[str, Any]]:
    """Normalize the `claims` JSON array. Keep the first claim for each `id` value."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning(
            "%s: OpenKB ignored the `claims` field because it is not a JSON array.",
            log_label,
        )
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        claim = normalize_claim(
            item,
            log_label=log_label,
            preserve_superseded_by=preserve_superseded_by,
        )
        if claim is not None and claim["id"] not in seen:
            result.append(claim)
            seen.add(claim["id"])
    return result


def merge_claims(existing: list | None, new: list | None) -> list[dict[str, Any]]:
    """Append new claims. Then apply page-local supersession links.

    Keep a stored claim when an incoming duplicate claim has the same `id` value.
    Ignore every field and every supersession link from the incoming duplicate
    claim. Ignore each new `superseded_by` field.
    Apply a supersession link only to a claim on the same page.
    Ignore a supersession link that points to its own claim.
    Apply a supersession link only when the `as_of`, `status`, and `authority`
    fields permit the supersession link.
    Repeated input does not create duplicate claims or links.
    """
    prior = normalize_claims(existing or [], log_label="existing-claims")
    incoming = normalize_claims(
        new or [],
        log_label="new-claims",
        preserve_superseded_by=False,
    )
    by_id = {claim["id"]: dict(claim) for claim in prior}
    order = [claim["id"] for claim in prior]
    pending: list[tuple[str, str, list[str]]] = []

    for claim in incoming:
        claim_id_value = claim["id"]
        if claim_id_value in by_id:
            continue
        by_id[claim_id_value] = dict(claim)
        order.append(claim_id_value)
        pending.append(
            (
                claim_id_value,
                claim["as_of"],
                list(claim.get("supersedes", [])),
            )
        )

    for incoming_id, incoming_as_of, superseded_ids in pending:
        for old_id in superseded_ids:
            if old_id == incoming_id:
                continue
            old = by_id.get(old_id)
            if old is None:
                continue
            stored_incoming = by_id[incoming_id]
            stored_incoming_status = stored_incoming["status"]
            if old["status"] == "validated" and stored_incoming.get("authority") == "assistant":
                logger.warning(
                    "OpenKB rejected supersession link %s <- %s. An assistant claim cannot "
                    "supersede a claim that has a `status` value of `validated`.",
                    old_id,
                    incoming_id,
                )
                continue
            if (
                old.get("authority") == "first_party"
                and stored_incoming.get("authority") != "first_party"
            ):
                logger.warning(
                    "OpenKB rejected supersession link %s <- %s. Only a claim that has an "
                    "`authority` value of `first_party` can supersede a claim that has an "
                    "`authority` value of `first_party`.",
                    old_id,
                    incoming_id,
                )
                continue
            if old["status"] == "validated" and stored_incoming_status not in {
                "validated",
                "abandoned",
            }:
                logger.warning(
                    "OpenKB rejected supersession link %s <- %s. The new claim must have a "
                    "`status` value of `validated` or `abandoned`.",
                    old_id,
                    incoming_id,
                )
                continue
            if not as_of_allows_supersession(incoming_as_of, old["as_of"]):
                logger.warning(
                    "OpenKB rejected supersession link %s <- %s. The new `as_of` value is "
                    "earlier than the old `as_of` value, or OpenKB cannot compare the values.",
                    old_id,
                    incoming_id,
                )
                continue
            old["status"] = "superseded"
            reverse = old.setdefault("superseded_by", [])
            if incoming_id not in reverse:
                reverse.append(incoming_id)

    return [by_id[claim_id_value] for claim_id_value in order]


def current_claims(
    claims: list[dict] | None,
    *,
    include_proposed: bool = False,
) -> list[dict[str, Any]]:
    """Return current claims. Do not change the input.

    By default, use strict current reads. Strict current reads return claims that
    have a `status` value of `validated`. If `include_proposed` is `True`, also
    return claims that have a `status` value of `proposed`. Do not return claims
    that have a `status` value of `superseded` or `abandoned`.
    """
    allowed = {"validated", "proposed"} if include_proposed else {"validated"}
    normalized = normalize_claims(claims or [], log_label="current-claims")
    return [dict(claim) for claim in normalized if claim["status"] in allowed]


def claims_for_prompt(claims: list[dict] | None) -> str:
    """Return compact JSON for an update prompt. Include `id` values and evidence."""
    normalized = normalize_claims(claims or [], log_label="prompt-claims")
    if not normalized:
        return "(none)"
    fields = (
        "id",
        "text",
        "as_of",
        "status",
        "source_anchor",
        "authority",
        "supersedes",
    )
    compact = [{key: claim[key] for key in fields if key in claim} for claim in normalized]
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def extract_raw_claims(page_object: dict | None) -> Any:
    """Return the optional `claims` field from a compiler response."""
    return page_object.get("claims") if isinstance(page_object, dict) else None
