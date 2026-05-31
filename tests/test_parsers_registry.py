"""Tests for parser selection / registry."""
from __future__ import annotations

import pytest

from openkb.parsers.registry import get_parser
from openkb.parsers.local import LocalParser


def _kwargs():
    return {"doc_name": "d", "images_dir": None, "source_dir": None}


def test_default_is_local():
    p = get_parser({}, **_kwargs())
    assert isinstance(p, LocalParser)


def test_explicit_local():
    p = get_parser({"parser": "local"}, **_kwargs())
    assert isinstance(p, LocalParser)


def test_override_wins_over_config():
    p = get_parser({"parser": "mistral"}, override="local", **_kwargs())
    assert isinstance(p, LocalParser)


def test_unknown_name_raises_with_valid_options():
    with pytest.raises(ValueError) as exc:
        get_parser({"parser": "nope"}, **_kwargs())
    assert "nope" in str(exc.value)
    assert "local" in str(exc.value)
