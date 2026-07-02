"""Unit tests for openkb.skill.validator — pure-Python structural checks
on a compiled skill directory. No LLM, no network."""

from __future__ import annotations

from pathlib import Path

from openkb.skill.validator import (
    DESCRIPTION_MAX_CHARS,
    REFERENCE_MAX_BYTES,
    SKILL_MD_MAX_BYTES,
    validate_skill,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_skill(
    parent: Path,
    name: str,
    *,
    frontmatter: str | None = "default",
    body: str = "# body\n",
    refs: dict[str, str] | None = None,
    scripts: dict[str, str] | None = None,
    skill_md_bytes: int | None = None,
) -> Path:
    """Create a skill directory with the requested contents.

    frontmatter:
      "default" -> a valid minimal frontmatter with matching name + long desc
      str       -> use that exact frontmatter block (between --- markers)
      None      -> write the body with NO frontmatter at all
    """
    skill_dir = parent / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    if frontmatter == "default":
        fm = f"name: {name}\ndescription: A useful and descriptive skill activation signal."
    else:
        fm = frontmatter

    if fm is None:
        text = body
    else:
        text = f"---\n{fm}\n---\n\n{body}"

    if skill_md_bytes is not None:
        text = text + ("x" * skill_md_bytes)
    (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")

    if refs:
        refs_dir = skill_dir / "references"
        refs_dir.mkdir(exist_ok=True)
        for relpath, content in refs.items():
            target = refs_dir / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    if scripts:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        for relpath, content in scripts.items():
            target = scripts_dir / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    return skill_dir


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_minimal_valid_skill_passes(tmp_path):
    sd = _write_skill(tmp_path, "demo-skill")
    result = validate_skill(sd)
    assert result.passed, result.errors
    assert result.errors == []
    assert result.warnings == []


# ---------------------------------------------------------------------------
# missing/structural errors
# ---------------------------------------------------------------------------


def test_skill_directory_missing(tmp_path):
    result = validate_skill(tmp_path / "nope")
    assert not result.passed
    assert any("does not exist" in e for e in result.errors)


def test_missing_skill_md(tmp_path):
    sd = tmp_path / "empty-skill"
    sd.mkdir()
    result = validate_skill(sd)
    assert not result.passed
    assert any("SKILL.md" in e for e in result.errors)


def test_no_frontmatter(tmp_path):
    sd = _write_skill(tmp_path, "no-fm", frontmatter=None, body="# just a body\n")
    result = validate_skill(sd)
    assert not result.passed
    assert any("frontmatter" in e.lower() for e in result.errors)


def test_malformed_yaml(tmp_path):
    sd = _write_skill(tmp_path, "bad-yaml", frontmatter="name: : : bad\n  - oops\n[")
    result = validate_skill(sd)
    assert not result.passed
    assert any("YAML" in e or "yaml" in e for e in result.errors)


def test_frontmatter_not_mapping(tmp_path):
    sd = _write_skill(tmp_path, "list-fm", frontmatter="- one\n- two")
    result = validate_skill(sd)
    assert not result.passed
    assert any("mapping" in e for e in result.errors)


# ---------------------------------------------------------------------------
# name field
# ---------------------------------------------------------------------------


def test_name_mismatch_with_directory(tmp_path):
    sd = _write_skill(
        tmp_path,
        "dir-name",
        frontmatter="name: other-name\ndescription: A nice long description here.",
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("doesn't match directory" in e for e in result.errors)


def test_name_invalid_uppercase(tmp_path):
    sd = tmp_path / "BadName"
    sd.mkdir()
    (sd / "SKILL.md").write_text(
        "---\nname: BadName\ndescription: A nice long description here.\n---\n"
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("kebab-case" in e for e in result.errors)


def test_name_invalid_underscore(tmp_path):
    sd = tmp_path / "bad_name"
    sd.mkdir()
    (sd / "SKILL.md").write_text(
        "---\nname: bad_name\ndescription: A nice long description here.\n---\n"
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("kebab-case" in e for e in result.errors)


def test_name_missing(tmp_path):
    sd = _write_skill(
        tmp_path,
        "no-name-field",
        frontmatter="description: A nice long description here.",
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("name" in e for e in result.errors)


# ---------------------------------------------------------------------------
# description field
# ---------------------------------------------------------------------------


def test_description_missing(tmp_path):
    sd = _write_skill(tmp_path, "no-desc", frontmatter="name: no-desc")
    result = validate_skill(sd)
    assert not result.passed
    assert any("description" in e for e in result.errors)


def test_description_too_long(tmp_path):
    long_desc = "x" * (DESCRIPTION_MAX_CHARS + 1)
    sd = _write_skill(
        tmp_path,
        "long-desc",
        frontmatter=f"name: long-desc\ndescription: {long_desc}",
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("description" in e and "chars" in e for e in result.errors)


def test_description_too_short_is_warning_not_error(tmp_path):
    sd = _write_skill(
        tmp_path,
        "short-desc",
        frontmatter="name: short-desc\ndescription: too short",
    )
    result = validate_skill(sd)
    # Errors should be empty — short desc is a warning, not blocking.
    assert result.errors == []
    assert result.passed  # passes default semantics
    assert any("too short" in w for w in result.warnings)
    assert not result.passed_strict  # but fails under --strict


# ---------------------------------------------------------------------------
# file sizes
# ---------------------------------------------------------------------------


def test_skill_md_too_big(tmp_path):
    sd = _write_skill(
        tmp_path,
        "big-skill",
        skill_md_bytes=SKILL_MD_MAX_BYTES + 1,
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("SKILL.md" in e and "bytes" in e for e in result.errors)


def test_reference_too_big(tmp_path):
    big = "y" * (REFERENCE_MAX_BYTES + 1)
    sd = _write_skill(
        tmp_path,
        "big-ref",
        refs={"huge.md": big},
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("huge.md" in e and "bytes" in e for e in result.errors)


# ---------------------------------------------------------------------------
# wikilinks
# ---------------------------------------------------------------------------


def test_wikilink_resolves(tmp_path):
    sd = _write_skill(
        tmp_path,
        "with-ref",
        body="See [[references/topic.md]] for details.\n",
        refs={"topic.md": "# topic\n"},
    )
    result = validate_skill(sd)
    assert result.passed, result.errors


def test_wikilink_missing_target(tmp_path):
    sd = _write_skill(
        tmp_path,
        "broken-ref",
        body="See [[references/missing.md]] for details.\n",
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("missing.md" in e for e in result.errors)


def test_wikilink_without_md_suffix_resolves(tmp_path):
    sd = _write_skill(
        tmp_path,
        "ref-no-suffix",
        body="See [[references/topic]] for details.\n",
        refs={"topic.md": "# topic\n"},
    )
    result = validate_skill(sd)
    assert result.passed, result.errors


def test_wikilink_dotted_stem_without_md_suffix_resolves(tmp_path):
    # A reference name whose stem contains a dot (e.g. "api.v2") must still get
    # the implicit ".md". Path.suffix would treat ".v2" as the suffix and skip
    # appending ".md", falsely reporting the existing api.v2.md as missing.
    sd = _write_skill(
        tmp_path,
        "ref-dotted-stem",
        body="See [[references/api.v2]] for details.\n",
        refs={"api.v2.md": "# api v2\n"},
    )
    result = validate_skill(sd)
    assert result.passed, result.errors


def test_wikilink_escaping_references_dir_is_rejected(tmp_path):
    # A link that resolves outside references/ (e.g. "../SKILL") must error —
    # not be accepted just because the resolved target (here SKILL.md) exists.
    sd = _write_skill(
        tmp_path,
        "ref-escape",
        body="See [[references/../SKILL]] for details.\n",
        refs={"topic.md": "# topic\n"},
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("outside the references/" in e for e in result.errors)


# ---------------------------------------------------------------------------
# scripts/ imports — strict mode only
# ---------------------------------------------------------------------------


def test_scripts_stdlib_only_no_warning(tmp_path):
    sd = _write_skill(
        tmp_path,
        "stdlib-script",
        scripts={"do.py": "import os\nimport sys\nfrom pathlib import Path\n"},
    )
    result = validate_skill(sd, strict=True)
    assert result.passed_strict, (result.errors, result.warnings)


def test_scripts_non_stdlib_warning_only_in_strict(tmp_path):
    sd = _write_skill(
        tmp_path,
        "requests-script",
        scripts={"fetch.py": "import requests\nimport os\n"},
    )

    # Non-strict: no warning surfaced (we still pass).
    result = validate_skill(sd, strict=False)
    assert result.passed
    assert result.warnings == []

    # Strict: warning surfaced, fails strict.
    result = validate_skill(sd, strict=True)
    assert result.passed  # no errors
    assert not result.passed_strict
    assert any("requests" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# passed vs passed_strict semantics
# ---------------------------------------------------------------------------


def test_passed_vs_passed_strict_semantics(tmp_path):
    # Skill that has a warning (short desc) but no errors.
    sd = _write_skill(
        tmp_path,
        "warn-only",
        frontmatter="name: warn-only\ndescription: short",
    )
    result = validate_skill(sd)
    assert result.passed is True
    assert result.passed_strict is False


# ---------------------------------------------------------------------------
# foreign wikilinks — links to the producer's wiki (concepts/, summaries/,
# sources/) are dead on the consumer's machine and waste context tokens.
# Only [[references/...]] is valid in shipped artifacts.
# ---------------------------------------------------------------------------


def test_validator_errors_on_concepts_wikilink_in_body(tmp_path):
    sd = _write_skill(
        tmp_path,
        "leaks-concepts",
        body="# body\n\nSee [[concepts/attention]] for details.\n",
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("foreign wikilinks" in e and "concepts" in e for e in result.errors)


def test_validator_errors_on_summaries_wikilink_in_body(tmp_path):
    sd = _write_skill(
        tmp_path,
        "leaks-summaries",
        body="# body\n\nSee [[summaries/paper]] for the framing.\n",
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("foreign wikilinks" in e and "summaries" in e for e in result.errors)


def test_validator_errors_on_sources_wikilink_in_body(tmp_path):
    sd = _write_skill(
        tmp_path,
        "leaks-sources",
        body="# body\n\nQuote from [[sources/book#page-12]].\n",
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("foreign wikilinks" in e and "sources" in e for e in result.errors)


def test_validator_errors_on_foreign_wikilink_in_reference(tmp_path):
    """References ship with the skill — they must also be self-contained."""
    sd = _write_skill(
        tmp_path,
        "leaky-ref",
        body="See [[references/depth]] for more.\n",
        refs={"depth.md": "# depth\n\nLink to [[concepts/foo]] here.\n"},
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("depth.md" in e and "foreign wikilinks" in e for e in result.errors)


def test_validator_accepts_references_only_links(tmp_path):
    """`[[references/...]]` ships with the skill so it's valid."""
    sd = _write_skill(
        tmp_path,
        "refs-only",
        body="See [[references/depth]] for the worked example.\n",
        refs={"depth.md": "# depth\n\nA self-contained reference page.\n"},
    )
    result = validate_skill(sd)
    assert result.passed, result.errors


def test_validator_accepts_plain_body_with_no_wikilinks(tmp_path):
    """A skill with prose and zero wikilinks is fine — provenance lives
    on the producer's side, not in the shipped artifact."""
    sd = _write_skill(
        tmp_path,
        "plain",
        body="# body\n\n- Rule 1: when X, prefer Y.\n- Rule 2: avoid Z.\n",
    )
    result = validate_skill(sd)
    assert result.passed, result.errors


# ---------------------------------------------------------------------------
# new round-2 checks: angle brackets in description + unknown frontmatter keys
# ---------------------------------------------------------------------------


def test_validator_rejects_angle_brackets_in_description(tmp_path):
    """Anthropic's activation parser breaks on < or > in description."""
    sd = _write_skill(
        tmp_path,
        "demo",
        frontmatter="name: demo\ndescription: Reason about <transformers> here.",
    )
    result = validate_skill(sd)
    assert not result.passed
    assert any("'<' or '>'" in e for e in result.errors)


def test_validator_warns_on_unknown_frontmatter_keys(tmp_path):
    """Anthropic spec only allows a fixed set of frontmatter keys."""
    sd = _write_skill(
        tmp_path,
        "demo",
        frontmatter=(
            "name: demo\ndescription: A valid description string here.\n"
            "random_key: foo\nanother_one: bar"
        ),
    )
    result = validate_skill(sd)
    # Passes in default (warnings only)
    assert result.passed
    # But strict mode catches it
    assert not result.passed_strict
    assert any("unknown keys" in w for w in result.warnings)
