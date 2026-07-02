"""Tests for openkb.cli._validate_skill_name — kebab-case slug enforcement."""

from __future__ import annotations

import pytest

from openkb.cli import _validate_skill_name


@pytest.mark.parametrize(
    "name",
    [
        "karpathy-thinking",
        "us-tax-2026",
        "linalg-tutor",
        "a",
        "a-b-c-d-e-f-g",
    ],
)
def test_accepts_valid_kebab_case(name):
    assert _validate_skill_name(name) is None  # None means OK


@pytest.mark.parametrize(
    "name,reason_fragment",
    [
        ("", "empty"),
        ("UPPER", "lowercase"),
        ("has space", "lowercase"),
        ("under_score", "lowercase"),
        ("dots.bad", "lowercase"),
        ("-leading", "leading"),
        ("trailing-", "trailing"),
        ("double--dash", "consecutive"),
        ("../escape", "lowercase"),
        ("a" * 65, "64 characters"),
        ("café", "lowercase"),
        ("ünicöde", "lowercase"),
    ],
)
def test_rejects_invalid_names(name, reason_fragment):
    msg = _validate_skill_name(name)
    assert msg is not None
    assert reason_fragment in msg.lower()
