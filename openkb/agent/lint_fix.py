"""Knowledge issue fixer for OpenKB lint."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from agents import Agent, Runner, function_tool
from agents.model_settings import ModelSettings

from openkb.agent.tools import get_wiki_page_content, list_wiki_files, read_wiki_file
from openkb.schema import get_agents_md

MAX_TURNS = 50


@dataclass
class LintFixRunResult:
    """Result text plus whether the target page was actually modified."""

    output: str
    applied: bool = False

    def __str__(self) -> str:
        return self.output

    def __contains__(self, item: str) -> bool:
        return item in self.output


@dataclass
class _LintFixWriteState:
    applied: bool = False


_KNOWLEDGE_FIX_INSTRUCTIONS_TEMPLATE = """\
You are OpenKB's knowledge issue fix agent. A lint issue or user feedback has
challenged a specific claim in one wiki page. Your job is to verify the claim
against the available source material and, only when allowed, make the smallest
faithful fix.

{schema_md}

## Rules
1. Treat files under sources/ as evidence. Treat summaries/ and concepts/ as
   derived wiki content that may contain mistakes.
2. Do not use outside knowledge. If the available files do not prove the
   challenged claim wrong or right, say that it is uncertain.
3. Read the target page first. Then inspect the suggested related files and any
   other clearly relevant wiki files.
4. If applying a fix, preserve the page's frontmatter and overall style.
   Change only the smallest section needed to fix the unsupported or incorrect
   statement.
5. Never weaken a correct statement simply because the user disagrees with it.
6. If there is not enough evidence, do not modify the page.

## Output format
Return Markdown with these sections:
- Verdict: Supported, Incorrect, Partially supported, or Uncertain.
- Evidence checked: concise list of files/pages you inspected.
- Reasoning: short explanation grounded in the checked files.
- Proposed fix: the exact replacement or "None".
- Applied: Yes or No.
"""


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return YAML frontmatter dict and body for a Markdown document."""
    frontmatter, body = _split_frontmatter_block(text)
    if frontmatter is None:
        return {}, text

    raw = frontmatter.removeprefix("---\n").removesuffix("\n---")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return data, body


def _split_frontmatter_block(text: str) -> tuple[str | None, str]:
    """Return raw YAML frontmatter block and Markdown body."""
    if not text.startswith("---\n"):
        return None, text

    end = text.find("\n---", 4)
    if end == -1:
        return None, text

    body_start = end + len("\n---")
    return text[:body_start], text[body_start:].lstrip("\n")


def _preserve_existing_frontmatter(original: str, fixed: str) -> str:
    """Keep the target page's metadata when writing fixed Markdown."""
    original_frontmatter, _ = _split_frontmatter_block(original)
    if original_frontmatter is None:
        return fixed

    _, fixed_body = _split_frontmatter_block(fixed)
    fixed_body = fixed_body.lstrip("\n")
    return f"{original_frontmatter}\n\n{fixed_body}"


def _ensure_md(path: str) -> str:
    return path if Path(path).suffix else f"{path}.md"


def _normalize_wiki_path(path: str, wiki_root: Path) -> str:
    """Validate a user-supplied wiki path and return a normalized relative path."""
    rel = Path(path)
    if rel.is_absolute():
        raise ValueError("Wiki page must be a path relative to wiki/.")

    root = wiki_root.resolve()
    full_path = (root / rel).resolve()
    if not full_path.is_relative_to(root):
        raise ValueError("Wiki page path escapes wiki root.")
    if full_path.suffix != ".md":
        raise ValueError("Wiki page must be a Markdown file.")
    if not full_path.exists():
        raise FileNotFoundError(f"Wiki page not found: {path}")

    normalized = str(full_path.relative_to(root)).replace("\\", "/")
    allowed = (
        normalized == "index.md"
        or normalized.startswith("summaries/")
        or normalized.startswith("concepts/")
        or normalized.startswith("explorations/")
    )
    if not allowed:
        raise ValueError(
            "Lint fixes can only target index.md, summaries/, concepts/, or explorations/ pages."
        )
    return normalized


def _coerce_source_paths(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def collect_related_files(wiki_root: Path, target_path: str) -> list[str]:
    """Collect likely source/summary files for a knowledge fix request.

    This intentionally stays lightweight for the MVP. It follows common
    frontmatter fields produced by the compiler instead of building a full
    provenance graph.
    """
    root = wiki_root.resolve()
    target = (root / target_path).resolve()
    if not target.is_relative_to(root) or not target.exists():
        return []

    related: list[str] = []

    def add(path: str) -> None:
        candidate = _ensure_md(path.strip())
        full = (root / candidate).resolve()
        if full.is_relative_to(root) and full.exists():
            normalized = str(full.relative_to(root)).replace("\\", "/")
            if normalized not in related and normalized != target_path:
                related.append(normalized)

    def add_full_text(path: str) -> None:
        full = (root / path).resolve()
        if full.suffix == ".json":
            return
        if full.is_relative_to(root) and full.exists():
            normalized = str(full.relative_to(root)).replace("\\", "/")
            if normalized not in related and normalized != target_path:
                related.append(normalized)

    fm, _ = _split_frontmatter(target.read_text(encoding="utf-8"))

    full_text = fm.get("full_text")
    if isinstance(full_text, str):
        add_full_text(full_text)

    for source in _coerce_source_paths(fm.get("sources")):
        add(source)

    # Concept pages usually point to summaries; follow those summaries to their
    # original full_text source when present.
    for path in list(related):
        if not path.startswith("summaries/"):
            continue
        summary = root / path
        summary_fm, _ = _split_frontmatter(summary.read_text(encoding="utf-8"))
        summary_full_text = summary_fm.get("full_text")
        if isinstance(summary_full_text, str):
            add_full_text(summary_full_text)

    return related[:12]


def build_knowledge_fix_agent(
    wiki_root: str,
    model: str,
    target_path: str,
    apply: bool = False,
    language: str = "en",
    write_state: _LintFixWriteState | None = None,
) -> Agent:
    """Build the source-grounded knowledge fix agent.

    In review-only mode the agent receives read tools only. In apply mode it
    receives a single write tool that can only overwrite the target page.
    """
    root = Path(wiki_root)
    schema_md = get_agents_md(root)
    instructions = _KNOWLEDGE_FIX_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
    instructions += f"\n\nIMPORTANT: Write the fix report in {language} language."
    if apply:
        instructions += (
            "\nYou may call write_target_file only after you have verified that the "
            "challenged wiki content is incorrect or unsupported by the evidence. "
            "When writing fixed Markdown, keep all existing YAML frontmatter "
            "fields exactly as they are and change only the Markdown body."
        )
    else:
        instructions += "\nYou are in review-only mode. Do not modify files."

    @function_tool
    def list_files(directory: str) -> str:
        """List Markdown files in a wiki subdirectory such as summaries or concepts."""
        return list_wiki_files(directory, wiki_root)

    @function_tool
    def read_file(path: str) -> str:
        """Read a Markdown file from the wiki."""
        return read_wiki_file(path, wiki_root)

    @function_tool
    def get_page_content(doc_name: str, pages: str) -> str:
        """Read specific pages from a PageIndex source document."""
        return get_wiki_page_content(doc_name, pages, wiki_root)

    tools = [list_files, read_file, get_page_content]

    if apply:

        @function_tool
        def write_target_file(content: str) -> str:
            """Overwrite only the challenged target wiki page with fixed Markdown."""
            full_path = (root.resolve() / target_path).resolve()
            if not full_path.is_relative_to(root.resolve()):
                return "Access denied: target path escapes wiki root."
            original = full_path.read_text(encoding="utf-8")
            full_path.write_text(
                _preserve_existing_frontmatter(original, content),
                encoding="utf-8",
            )
            if write_state is not None:
                write_state.applied = True
            return f"Written: {target_path}"

        tools.append(write_target_file)

    return Agent(
        name="wiki-knowledge-fixer",
        instructions=instructions,
        tools=tools,
        model=f"litellm/{model}",
        model_settings=ModelSettings(parallel_tool_calls=False),
    )


async def run_knowledge_fix(
    kb_dir: Path,
    target_path: str,
    claim: str,
    model: str,
    note: str | None = None,
    apply: bool = False,
) -> LintFixRunResult:
    """Verify a knowledge issue and optionally apply a source-grounded fix."""
    from openkb.config import load_config

    wiki_root = kb_dir / "wiki"
    normalized_target = _normalize_wiki_path(target_path, wiki_root)

    config = load_config(kb_dir / ".openkb" / "config.yaml")
    language: str = config.get("language", "en")
    related = collect_related_files(wiki_root, normalized_target)
    related_text = "\n".join(f"- {path}" for path in related) or "- None found automatically"

    mode = "apply fix if verified" if apply else "review only"
    user_note = note or "None"
    prompt = f"""\
Mode: {mode}

Target wiki page: {normalized_target}

Challenged claim:
{claim}

User note:
{user_note}

Suggested related files to inspect:
{related_text}

Please verify whether the challenged claim is faithful to the source material.
Start by reading the target page. Then read the suggested related files and any
other clearly relevant files needed to decide. If PageIndex source content is
needed, use get_page_content with narrow page ranges based on the summary tree.
"""

    write_state = _LintFixWriteState()
    agent = build_knowledge_fix_agent(
        str(wiki_root),
        model,
        normalized_target,
        apply=apply,
        language=language,
        write_state=write_state,
    )
    result = await Runner.run(agent, prompt, max_turns=MAX_TURNS)
    output = result.final_output or "Knowledge fix completed. No output produced."
    return LintFixRunResult(
        output=output,
        applied=write_state.applied,
    )
