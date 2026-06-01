"""Tests for the parser abstraction base types."""
from __future__ import annotations

import pytest

from openkb.parsers.base import ParseResult, Parser


def test_parse_result_defaults_to_empty_images():
    pr = ParseResult(markdown="# Hi")
    assert pr.markdown == "# Hi"
    assert pr.images == {}


def test_parser_is_abstract():
    with pytest.raises(TypeError):
        Parser()  # cannot instantiate abstract base


def test_concrete_parser_must_implement_parse_and_supports():
    class Incomplete(Parser):
        name = "incomplete"
    with pytest.raises(TypeError):
        Incomplete()
