"""Pluggable document parsers for the file → Markdown step."""
from openkb.parsers.base import ParseResult, Parser
from openkb.parsers.registry import get_parser

__all__ = ["ParseResult", "Parser", "get_parser"]
