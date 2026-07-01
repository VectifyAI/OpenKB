"""Tests for openkb.frontmatter — the shared frontmatter helper module."""

from __future__ import annotations

import yaml

from openkb import frontmatter as fm


class TestKvLine:
    def test_basic(self):
        assert fm.kv_line("type", "Concept") == 'type: "Concept"'

    def test_value_with_colon_and_quotes_round_trips(self):
        line = fm.kv_line("description", 'a: "b" — c')
        assert yaml.safe_load(line)["description"] == 'a: "b" — c'

    def test_unicode_preserved(self):
        assert fm.kv_line("title", "café") == 'title: "café"'


class TestListLine:
    def test_basic(self):
        assert fm.list_line("sources", ["a", "b"]) == 'sources: ["a", "b"]'


class TestBlock:
    def test_assembles_with_delimiters(self):
        out = fm.block([fm.kv_line("type", "Concept")])
        assert out == '---\ntype: "Concept"\n---\n\n'


class TestSplit:
    def test_basic(self):
        text = '---\ntype: "Concept"\n---\n\nbody here'
        block, body = fm.split(text)
        assert block == '---\ntype: "Concept"\n---\n'
        assert body == "\nbody here"
        assert block + body == text  # lossless

    def test_dashes_inside_value_do_not_truncate(self):
        text = '---\ntype: "Concept"\ndescription: "--- x ---"\n---\nbody'
        block, body = fm.split(text)
        assert 'description: "--- x ---"' in block
        assert body == "body"

    def test_no_frontmatter(self):
        assert fm.split("no frontmatter here") is None

    def test_unterminated_frontmatter(self):
        assert fm.split("---\ntype: x\nbut no close") is None


class TestParse:
    def test_basic(self):
        assert fm.parse('---\ntype: "Concept"\nx: 1\n---\nbody') == {"type": "Concept", "x": 1}

    def test_dashes_inside_value(self):
        d = fm.parse('---\ntype: "Concept"\ndescription: "--- x"\n---\nbody')
        assert d["type"] == "Concept"
        assert d["description"] == "--- x"

    def test_absent_returns_empty(self):
        assert fm.parse("plain body") == {}

    def test_malformed_yaml_returns_empty(self):
        # an unclosed flow sequence makes safe_load raise; we must degrade to {}
        assert fm.parse("---\nfoo: [unclosed\n---\n") == {}


class TestSetLine:
    def test_replaces_existing(self):
        out = fm.set_line('---\ntype: "Old"\n---\n', "type", "New")
        assert 'type: "New"' in out
        assert "Old" not in out

    def test_inserts_when_absent(self):
        out = fm.set_line('---\nsources: ["a"]\n---\n', "type", "Concept")
        assert 'type: "Concept"' in out
        assert 'sources: ["a"]' in out

    def test_value_with_regex_backref_is_literal(self):
        out = fm.set_line('---\ndescription: "old"\n---\n', "description", r"a\1b")
        # lambda replacement keeps the backref literal; json.dumps escapes it,
        # and a YAML loader reads it back unchanged.
        assert fm.parse(out)["description"] == r"a\1b"


class TestDropLine:
    def test_removes_key(self):
        out = fm.drop_line('---\ntype: "C"\nbrief: gone\n---\n', "brief")
        assert "brief" not in out
        assert 'type: "C"' in out

    def test_noop_when_absent(self):
        block = '---\ntype: "C"\n---\n'
        assert fm.drop_line(block, "brief") == block


class TestParseListValue:
    def test_basic(self):
        assert fm.parse_list_value('sources: ["a", "b"]') == ["a", "b"]

    def test_non_list_returns_none(self):
        assert fm.parse_list_value("type: Concept") is None
