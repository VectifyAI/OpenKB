"""Deterministic structural validator for compiled skills.

Pure Python — no LLM calls. Catches the common failure modes that would
make a skill un-loadable or misleading to the agents that install it:

  * SKILL.md missing or unparseable
  * frontmatter present, parses as YAML, is a mapping
  * required fields: name (matches dir + slug regex), description
  * description length within bounds (warns < 20 chars, errors > 1024)
  * description must not contain '<' or '>' (breaks activation parser)
  * frontmatter keys limited to the Anthropic Skills allowed set
    (warns on unknown keys; matches Anthropic's quick_validate.py)
  * files within size limits (SKILL.md ≤ 50 KB / references/*.md ≤ 100 KB)
  * `[[references/...]]` wikilinks resolve to actual files
  * (strict mode) scripts/*.py imports only stdlib modules

This is the deterministic counterpart to ``openkb skill eval`` — eval
measures whether the description fires; validate ensures the structure
is well-formed.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # already a project dep (pyyaml)

from openkb.skill import (
    extract_body as _extract_body,
)
from openkb.skill import (
    extract_frontmatter as _extract_frontmatter,
)

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DESCRIPTION_MAX_CHARS = 1024
SKILL_MD_MAX_BYTES = 50 * 1024
REFERENCE_MAX_BYTES = 100 * 1024
NAME_MAX_LEN = 64
WIKILINK_RE = re.compile(r"\[\[references/([a-z0-9._/-]+)\]\]", re.IGNORECASE)
# Foreign wikilinks point at the producer's wiki, which is not shipped
# with the skill. On the consumer's machine they are dead links *and*
# wasted context tokens — see "Linking rules" in skill_create.md.
FOREIGN_WIKILINK_RE = re.compile(
    r"\[\[(concepts|summaries|sources)/[^\]]+\]\]",
    re.IGNORECASE,
)
ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "description",
    "license",
    "allowed-tools",
    "metadata",
    "compatibility",
}


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors

    @property
    def passed_strict(self) -> bool:
        return not self.errors and not self.warnings


def validate_skill(skill_dir: Path, *, strict: bool = False) -> ValidationResult:
    """Run all structural checks on a single compiled skill directory.

    Args:
        skill_dir: ``<kb>/output/skills/<name>``
        strict: if True, warnings are surfaced; otherwise only errors

    Returns a ValidationResult. Use ``result.passed`` for the default
    semantics (errors block, warnings don't); use ``result.passed_strict``
    when running ``--strict``.
    """
    result = ValidationResult()
    skill_dir = Path(skill_dir)

    if not skill_dir.is_dir():
        result.errors.append(f"Skill directory does not exist: {skill_dir}")
        return result

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        result.errors.append("Missing required file: SKILL.md")
        return result

    # File size
    skill_size = skill_md.stat().st_size
    if skill_size > SKILL_MD_MAX_BYTES:
        result.errors.append(f"SKILL.md is {skill_size} bytes; max is {SKILL_MD_MAX_BYTES} bytes.")

    text = skill_md.read_text(encoding="utf-8")

    # Frontmatter
    fm = _extract_frontmatter(text)
    if fm is None:
        result.errors.append("SKILL.md has no YAML frontmatter (must start with `---` ... `---`).")
        return result

    try:
        meta = yaml.safe_load(fm) or {}
    except yaml.YAMLError as exc:
        result.errors.append(f"Frontmatter is not valid YAML: {exc}")
        return result

    if not isinstance(meta, dict):
        result.errors.append("Frontmatter must be a YAML mapping.")
        return result

    extras = set(meta.keys()) - ALLOWED_FRONTMATTER_KEYS
    if extras:
        # Treat as warning, not error — keeps strict mode user-controllable
        result.warnings.append(
            f"Frontmatter contains unknown keys: {sorted(extras)}. "
            f"Anthropic Skills spec only allows: "
            f"{sorted(ALLOWED_FRONTMATTER_KEYS)}."
        )

    # name field
    name = meta.get("name")
    if not name:
        result.errors.append("Frontmatter is missing required field 'name:'.")
    elif not isinstance(name, str):
        result.errors.append("Frontmatter 'name:' must be a string.")
    else:
        if name != skill_dir.name:
            result.errors.append(
                f"Frontmatter 'name: {name}' doesn't match directory name '{skill_dir.name}'."
            )
        if not SKILL_NAME_RE.match(name) or len(name) > NAME_MAX_LEN:
            result.errors.append(
                f"Frontmatter 'name: {name}' must be kebab-case "
                f"(lowercase a-z0-9 + dashes), 1-{NAME_MAX_LEN} chars."
            )

    # description field
    desc = meta.get("description")
    if not desc:
        result.errors.append("Frontmatter is missing required field 'description:'.")
    elif not isinstance(desc, str):
        result.errors.append("Frontmatter 'description:' must be a string.")
    else:
        if len(desc) > DESCRIPTION_MAX_CHARS:
            result.errors.append(
                f"Frontmatter 'description:' is {len(desc)} chars; "
                f"max is {DESCRIPTION_MAX_CHARS} chars."
            )
        if len(desc) < 20:
            result.warnings.append(
                f"Frontmatter 'description:' is only {len(desc)} chars — "
                f"too short to be a useful activation signal."
            )
        if "<" in desc or ">" in desc:
            result.errors.append(
                "Frontmatter 'description:' must not contain '<' or '>' "
                "characters — they break the activation parser in Claude Code."
            )

    # Foreign wikilinks. The skill ships *without* the producer's wiki, so
    # any [[concepts/...]] / [[summaries/...]] / [[sources/...]] left in
    # the description, body, or references is a dead link on the
    # consumer's machine plus wasted context tokens. The compile prompt's
    # "Linking rules" section makes this explicit; this is the structural
    # enforcement. Scan each location separately so the error message
    # tells the author where to look.
    body = _extract_body(text)
    if isinstance(desc, str):
        desc_foreign = FOREIGN_WIKILINK_RE.findall(desc)
        if desc_foreign:
            kinds = sorted({k.lower() for k in desc_foreign})
            result.errors.append(
                f"SKILL.md `description:` contains foreign wikilinks "
                f"({', '.join(kinds)}) back to the producer's wiki. "
                f"Descriptions are the consumer-visible activation signal — "
                f"paraphrase the reference inline."
            )
    body_foreign = FOREIGN_WIKILINK_RE.findall(body)
    if body_foreign:
        kinds = sorted({k.lower() for k in body_foreign})
        result.errors.append(
            f"SKILL.md body contains foreign wikilinks ({', '.join(kinds)}) "
            f"back to the producer's wiki. Those don't ship with the skill "
            f"and are dead on the consumer's machine — paraphrase the "
            f"content inline or move it into `references/<slug>.md`."
        )
    refs_dir = skill_dir / "references"
    if refs_dir.is_dir():
        for ref in refs_dir.rglob("*.md"):
            ref_text = ref.read_text(encoding="utf-8", errors="replace")
            if FOREIGN_WIKILINK_RE.search(ref_text):
                result.errors.append(
                    f"{ref.relative_to(skill_dir)} contains foreign "
                    f"wikilinks back to the producer's wiki. References "
                    f"ship with the skill and must be self-contained."
                )

    # references/ wikilink resolution
    wikilinks = WIKILINK_RE.findall(text)
    for link in wikilinks:
        # The link may already include the .md suffix; append it otherwise.
        # Test the literal ".md" rather than Path.suffix — a dotted stem like
        # "api.v2" has a truthy suffix (".v2"), so Path.suffix would skip the
        # ".md" and then look for a non-existent extension-less file.
        target = refs_dir / (link if link.lower().endswith(".md") else f"{link}.md")
        # A reference must resolve to a file *under* references/ — reject any
        # that escape it (e.g. "[[references/../SKILL]]"), which would also
        # break the relative_to(skill_dir) call in the not-found message below.
        if not target.resolve().is_relative_to(refs_dir.resolve()):
            result.errors.append(
                f"SKILL.md references [[references/{link}]] which resolves "
                f"outside the references/ directory."
            )
            continue
        if not target.exists():
            result.errors.append(
                f"SKILL.md references [[references/{link}]] but "
                f"{target.relative_to(skill_dir)} doesn't exist."
            )

    # references/*.md file sizes
    if refs_dir.is_dir():
        for ref in refs_dir.rglob("*.md"):
            size = ref.stat().st_size
            if size > REFERENCE_MAX_BYTES:
                result.errors.append(
                    f"{ref.relative_to(skill_dir)} is {size} bytes; "
                    f"max is {REFERENCE_MAX_BYTES} bytes."
                )

    # scripts/*.py imports — strict only
    if strict:
        scripts_dir = skill_dir / "scripts"
        if scripts_dir.is_dir():
            for script in scripts_dir.rglob("*.py"):
                bad = _non_stdlib_imports(script)
                if bad:
                    result.warnings.append(
                        f"{script.relative_to(skill_dir)} imports non-stdlib "
                        f"modules: {', '.join(sorted(bad))}. Skill scripts run "
                        f"in unknown environments — prefer stdlib only."
                    )

    return result


def _non_stdlib_imports(script: Path) -> set[str]:
    """Return imported module names that aren't in the Python stdlib."""
    try:
        tree = ast.parse(script.read_text(encoding="utf-8"))
    except SyntaxError:
        return {"<syntax-error>"}
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
    bad: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if stdlib and root not in stdlib:
                    bad.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if stdlib and root not in stdlib:
                    bad.add(root)
    return bad
