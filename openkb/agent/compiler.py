"""Wiki compilation pipeline for OpenKB.

Pipeline leveraging LLM prompt caching:
  Step 1: Build base context A (schema + document content).
  Step 2: A → generate summary.
  Step 3: A + summary → concepts plan (create/update/related).
  Step 4: Concurrent LLM calls (A cached) → generate new + rewrite updated concepts.
  Step 5: Code adds cross-ref links to related concepts, updates index.

Anthropic prompt caching is enabled via ``cache_control`` markers at two
breakpoints: end of the document message (caches system + doc across all
N+M+2 calls) and end of the assistant summary message (caches the additional
summary prefix across N+M concept-generation calls). Providers that do not
support cache_control receive a normalized list-of-blocks content payload,
which LiteLLM passes through cleanly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import threading
import time
import unicodedata
from pathlib import Path

import litellm

from openkb.lint import list_existing_wiki_targets, strip_ghost_wikilinks
from openkb.schema import get_agents_md

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """\
You are OpenKB's wiki compilation agent for a personal knowledge base.

{schema_md}

Write all content in {language} language.
Use [[wikilinks]] to connect related pages (e.g. [[concepts/attention]]).
"""

_SUMMARY_USER = """\
New document: {doc_name}

Full text:
{content}

Write a summary page for this document in Markdown.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) describing the document's main contribution
- "content": The full summary in Markdown. Include key concepts, findings, ideas, \
and [[wikilinks]] to concepts that could become cross-document concept pages

Return ONLY valid JSON, no fences.
"""


_CONCEPTS_PLAN_USER = """\
Based on the summary above, decide how to update the wiki's concept pages.

Existing concept pages:
{concept_briefs}

Return a JSON object with three keys:

1. "create" — new concepts not covered by any existing page. Array of objects:
   {{"name": "concept-slug", "title": "Human-Readable Title"}}

2. "update" — existing concepts that have significant new information from \
this document worth integrating. Array of objects:
   {{"name": "existing-slug", "title": "Existing Title"}}

3. "related" — existing concepts tangentially related to this document but \
not needing content changes, just a cross-reference link. Array of slug strings.

Rules:
- For the first few documents, create 2-3 foundational concepts at most.
- Do NOT create a concept that overlaps with an existing one — use "update".
- Do NOT create concepts that are just the document topic itself.
- "related" is for lightweight cross-linking only, no content rewrite needed.

Return ONLY valid JSON, no fences, no explanation.
"""

_KNOWN_TARGETS_USER = """\
The wiki currently contains these pages, and they are the COMPLETE list of \
valid [[wikilink]] targets you may use in the responses that follow:

{known_targets}

Rules for [[wikilinks]] in all subsequent responses:
- For [[concepts/X]]: X must appear in the whitelist above.
- For [[summaries/Y]]: Y must appear in the whitelist above.
- Do NOT invent new wikilink targets. If you want to mention a concept \
that is not in the whitelist, write it as plain text without brackets.
"""

_CONCEPT_PAGE_USER = """\
Write the concept page for: {title}

This concept relates to the document "{doc_name}" summarized above.
{update_instruction}

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) defining this concept
- "content": The full concept page in Markdown. Include clear explanation, \
key details from the source document, and [[wikilinks]] to related concepts \
and [[summaries/{doc_name}]] — subject to the wikilink rules from the \
whitelist message above.

Return ONLY valid JSON, no fences.
"""

_CONCEPT_UPDATE_USER = """\
Update the concept page for: {title}

Current content of this page:
{existing_content}

New information from document "{doc_name}" (summarized above) should be \
integrated into this page. Rewrite the full page incorporating the new \
information naturally — do not just append. Preserve the existing structure \
and intent of the page.

For [[wikilinks]] in the rewrite, follow the whitelist rules from the \
message above: keep links whose target is in the whitelist, convert any \
existing links whose target is NOT in the whitelist to plain text, and do \
not invent new wikilink targets.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) defining this concept (may differ from before)
- "content": The rewritten full concept page in Markdown

Return ONLY valid JSON, no fences.
"""

_SUMMARY_REWRITE_USER = """\
Task: Rewrite the summary you wrote above into a final version that is \
consistent with the concept pages now in the wiki (per the whitelist message \
above).

STRICT rules:
- Preserve every factual claim, finding, and detail from your draft. Do \
NOT add or remove technical content, examples, or claims.
- For [[wikilinks]], follow the whitelist message above: keep valid links, \
replace targets not in the whitelist with plain text, do not invent new \
wikilink targets.
- You MAY upgrade plain-text mentions to [[wikilinks]] when the concept \
appears in the whitelist — this is encouraged.
- Keep the headings, paragraph structure, and approximately the same length \
as the draft.

Return ONLY the rewritten Markdown content (no JSON, no fences, no frontmatter).
"""

_LONG_DOC_SUMMARY_USER = """\
This is a PageIndex summary for long document "{doc_name}" (doc_id: {doc_id}):

{content}

Based on this structured summary, write a concise overview that captures \
the key themes and findings. This will be used to generate concept pages.

Return ONLY the Markdown content (no frontmatter, no code fences).
"""


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _cached_text(text: str) -> list[dict]:
    """Wrap a text payload into a content-block list with an Anthropic
    ephemeral cache_control marker.

    LiteLLM passes the marker through to Anthropic (and OpenRouter →
    Anthropic). For providers that ignore cache_control, the list-of-blocks
    payload remains a valid OpenAI-compatible content shape.
    """
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


class _Spinner:
    """Animated dots spinner that runs in a background thread."""

    def __init__(self, label: str):
        self._label = label
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        sys.stdout.write(f"    {self._label}")
        sys.stdout.flush()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(timeout=1.0):
            sys.stdout.write(".")
            sys.stdout.flush()

    def stop(self, suffix: str = "") -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write(f" {suffix}\n")
        sys.stdout.flush()


def _format_usage(elapsed: float, usage) -> str:
    """Format timing and token usage into a short summary string."""
    cached = getattr(usage, "prompt_tokens_details", None)
    cache_info = ""
    if cached and hasattr(cached, "cached_tokens") and cached.cached_tokens:
        cache_info = f", cached={cached.cached_tokens}"
    return f"{elapsed:.1f}s (in={usage.prompt_tokens}, out={usage.completion_tokens}{cache_info})"


def _fmt_messages(messages: list[dict], max_content: int = 200) -> str:
    """Format messages for debug output, truncating long content.

    Accepts both plain-string content and the list-of-blocks shape used by
    cache_control-tagged messages (joins all text blocks for preview).
    """
    parts = []
    for msg in messages:
        role = msg["role"]
        raw = msg["content"]
        if isinstance(raw, list):
            text = "".join(b.get("text", "") for b in raw if isinstance(b, dict))
        else:
            text = raw
        if len(text) > max_content:
            preview = text[:max_content] + f"... ({len(text)} chars)"
        else:
            preview = text
        parts.append(f"      [{role}] {preview}")
    return "\n".join(parts)


def _llm_call(model: str, messages: list[dict], step_name: str, **kwargs) -> str:
    """Single LLM call with animated progress and debug logging."""
    logger.debug("LLM request [%s]:\n%s", step_name, _fmt_messages(messages))
    if kwargs:
        logger.debug("LLM kwargs [%s]: %s", step_name, kwargs)

    spinner = _Spinner(step_name)
    spinner.start()
    t0 = time.time()

    response = litellm.completion(model=model, messages=messages, **kwargs)
    content = response.choices[0].message.content or ""

    spinner.stop(_format_usage(time.time() - t0, response.usage))
    logger.debug("LLM response [%s]:\n%s", step_name, content[:500] + ("..." if len(content) > 500 else ""))
    return content.strip()


async def _llm_call_async(model: str, messages: list[dict], step_name: str, **kwargs) -> str:
    """Async LLM call with timing output and debug logging."""
    logger.debug("LLM request [%s]:\n%s", step_name, _fmt_messages(messages))
    if kwargs:
        logger.debug("LLM kwargs [%s]: %s", step_name, kwargs)

    t0 = time.time()

    response = await litellm.acompletion(model=model, messages=messages, **kwargs)
    content = response.choices[0].message.content or ""

    elapsed = time.time() - t0
    sys.stdout.write(f"    {step_name}... {_format_usage(elapsed, response.usage)}\n")
    sys.stdout.flush()
    logger.debug("LLM response [%s]:\n%s", step_name, content[:500] + ("..." if len(content) > 500 else ""))
    return content.strip()


def _parse_json(text: str) -> list | dict:
    """Parse JSON from LLM response, handling fences, prose, and malformed JSON."""
    from json_repair import repair_json
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        cleaned = cleaned[first_nl + 1:] if first_nl != -1 else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    result = json.loads(repair_json(cleaned.strip()))
    if not isinstance(result, (dict, list)):
        raise ValueError(f"Expected JSON object or array, got {type(result).__name__}")
    return result


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def _read_wiki_context(wiki_dir: Path) -> tuple[str, list[str]]:
    """Read current index.md content and list of existing concept slugs."""
    index_path = wiki_dir / "index.md"
    index_content = index_path.read_text(encoding="utf-8") if index_path.exists() else ""

    concepts_dir = wiki_dir / "concepts"
    existing = sorted(p.stem for p in concepts_dir.glob("*.md")) if concepts_dir.exists() else []

    return index_content, existing


def _read_concept_briefs(wiki_dir: Path) -> str:
    """Read existing concept pages and return compact one-line summaries.

    For each concept, reads the ``brief:`` field from YAML frontmatter if
    present; otherwise falls back to truncating the first 150 chars of the body
    (newlines collapsed to spaces).  Formats each as ``- {slug}: {brief}``.

    Returns "(none yet)" if the concepts directory is missing or empty.
    """
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.exists():
        return "(none yet)"

    md_files = sorted(concepts_dir.glob("*.md"))
    if not md_files:
        return "(none yet)"

    lines: list[str] = []
    for path in md_files:
        text = path.read_text(encoding="utf-8")
        brief = ""
        body = text
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                fm = text[:end + 3]
                body = text[end + 3:]
                for line in fm.split("\n"):
                    if line.startswith("brief:"):
                        brief = line[len("brief:"):].strip()
                        break
        if not brief:
            brief = body.strip().replace("\n", " ")[:150]
        if brief:
            lines.append(f"- {path.stem}: {brief}")

    return "\n".join(lines) or "(none yet)"


def _iter_h2_headings(lines: list[str]) -> list[tuple[int, str]]:
    """Return ``[(line_index, normalized_heading), ...]`` for every ATX H2.

    A line counts as H2 when it starts with ``"## "`` (two hashes + space).
    ``normalized_heading`` is the line with trailing whitespace stripped, so
    ``"## Documents "`` normalizes to ``"## Documents"`` — letting callers
    use exact-string comparison without tripping on stray whitespace.

    Used by ``_get_section_bounds`` so heading lookup and the next-section
    boundary share one scan and one normalization rule.
    """
    return [
        (i, line.rstrip())
        for i, line in enumerate(lines)
        if line.startswith("## ")
    ]


def _get_section_bounds(lines: list[str], heading: str) -> tuple[int, int] | None:
    """Return the [start, end) bounds for a Markdown H2 section.

    Uses ``_iter_h2_headings`` so the same H2 detection that finds the
    target heading also determines the section's end (the next H2). A
    drifted ``"## Documents "`` matches ``"## Documents"`` because both
    sides are normalized.
    """
    headings = _iter_h2_headings(lines)
    for k, (idx, normalized) in enumerate(headings):
        if normalized == heading:
            start = idx + 1
            end = headings[k + 1][0] if k + 1 < len(headings) else len(lines)
            return start, end
    return None


def _ensure_h2_section(lines: list[str], heading: str) -> None:
    """Ensure an H2 section ``heading`` exists in ``lines``; append if missing.

    Recovers from hand-edited or drifted index.md files where the expected
    section was removed or renamed — without this, downstream inserts would
    silently no-op and entries would be dropped.
    """
    if _get_section_bounds(lines, heading) is not None:
        return
    logger.warning(
        "Wiki page is missing %r section; appending it. "
        "Check whether the file was hand-edited away from the canonical layout.",
        heading,
    )
    while lines and lines[-1] == "":
        lines.pop()
    if lines:
        lines.append("")
    lines.append(heading)
    lines.append("")


def _section_contains_link(lines: list[str], heading: str, link: str) -> bool:
    """Check whether an index entry already exists inside the named section."""
    bounds = _get_section_bounds(lines, heading)
    if bounds is None:
        return False

    start, end = bounds
    entry_prefix = f"- {link}"
    return any(line.startswith(entry_prefix) for line in lines[start:end])


def _replace_section_entry(lines: list[str], heading: str, link: str, entry: str) -> bool:
    """Replace the first matching entry within a specific section."""
    bounds = _get_section_bounds(lines, heading)
    if bounds is None:
        return False

    start, end = bounds
    entry_prefix = f"- {link}"
    for i in range(start, end):
        if lines[i].startswith(entry_prefix):
            lines[i] = entry
            return True
    return False


def _insert_section_entry(lines: list[str], heading: str, entry: str) -> bool:
    """Insert a new entry at the top of a specific section."""
    bounds = _get_section_bounds(lines, heading)
    if bounds is None:
        return False

    start, _ = bounds
    lines.insert(start, entry)
    return True



def _write_summary(wiki_dir: Path, doc_name: str, summary: str,
                    doc_type: str = "short") -> None:
    """Write summary page with frontmatter."""
    if summary.startswith("---"):
        end = summary.find("---", 3)
        if end != -1:
            summary = summary[end + 3:].lstrip("\n")
    summaries_dir = wiki_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    ext = "md" if doc_type == "short" else "json"
    fm_lines = [
        f"doc_type: {doc_type}",
        f"full_text: sources/{doc_name}.{ext}",
    ]
    frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
    (summaries_dir / f"{doc_name}.md").write_text(frontmatter + summary, encoding="utf-8")


_SAFE_NAME_RE = re.compile(r'[^\w\-]')


def _sanitize_concept_name(name: str) -> str:
    """Sanitize a concept name for safe use as a filename."""
    name = unicodedata.normalize("NFKC", name)
    sanitized = _SAFE_NAME_RE.sub("-", name).strip("-")
    return sanitized or "unnamed-concept"


def _write_concept(wiki_dir: Path, name: str, content: str, source_file: str, is_update: bool, brief: str = "") -> None:
    """Write or update a concept page, managing the sources frontmatter."""
    concepts_dir = wiki_dir / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_concept_name(name)
    path = (concepts_dir / f"{safe_name}.md").resolve()
    if not path.is_relative_to(concepts_dir.resolve()):
        logger.warning("Concept name escapes concepts dir: %s", name)
        return

    if is_update and path.exists():
        existing = path.read_text(encoding="utf-8")
        if source_file not in existing:
            existing = _prepend_source_to_frontmatter(existing, source_file)
        # Strip frontmatter from LLM content to avoid duplicate blocks
        clean = content
        if clean.startswith("---"):
            end = clean.find("---", 3)
            if end != -1:
                clean = clean[end + 3:].lstrip("\n")
        # Replace body with LLM rewrite (prompt asks for full rewrite, not delta)
        if existing.startswith("---"):
            end = existing.find("---", 3)
            if end != -1:
                existing = existing[:end + 3] + "\n\n" + clean
            else:
                existing = clean
        else:
            existing = clean
        if brief and existing.startswith("---"):
            end = existing.find("---", 3)
            if end != -1:
                fm = existing[:end + 3]
                body = existing[end + 3:]
                if "brief:" in fm:
                    fm = re.sub(r"brief:.*", f"brief: {brief}", fm)
                else:
                    fm = fm.replace("---\n", f"---\nbrief: {brief}\n", 1)
                existing = fm + body
        path.write_text(existing, encoding="utf-8")
    else:
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:].lstrip("\n")
        fm_lines = [f"sources: [{source_file}]"]
        if brief:
            fm_lines.append(f"brief: {brief}")
        frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
        path.write_text(frontmatter + content, encoding="utf-8")


def _prepend_source_to_frontmatter(text: str, source_file: str) -> str:
    """Prepend ``source_file`` to the inline ``sources:`` list in YAML frontmatter.

    Creates the frontmatter or the ``sources:`` line if missing. Returns the
    text unchanged if ``source_file`` is already present in the list, or if
    the frontmatter is malformed (no closing ``---``).
    """
    if not text.startswith("---"):
        return f"---\nsources: [{source_file}]\n---\n\n" + text

    fm_end = text.find("---", 3)
    if fm_end == -1:
        return text

    fm_block = text[:fm_end]
    body = text[fm_end:]
    fm_lines = fm_block.split("\n")

    for i, line in enumerate(fm_lines):
        if not line.lstrip().startswith("sources:"):
            continue
        lb = line.find("[")
        rb = line.rfind("]")
        if lb == -1 or rb == -1 or rb < lb:
            return text
        items = [s.strip() for s in line[lb + 1:rb].split(",") if s.strip()]
        if source_file in items:
            return text
        items.insert(0, source_file)
        fm_lines[i] = f"sources: [{', '.join(items)}]"
        return "\n".join(fm_lines) + body

    fm_lines.insert(1, f"sources: [{source_file}]")
    return "\n".join(fm_lines) + body


def _add_related_link(wiki_dir: Path, concept_slug: str, doc_name: str, source_file: str) -> None:
    """Add a cross-reference link to an existing concept page (no LLM call)."""
    concepts_dir = wiki_dir / "concepts"
    path = concepts_dir / f"{concept_slug}.md"
    if not path.exists():
        return

    text = path.read_text(encoding="utf-8")
    link = f"[[summaries/{doc_name}]]"
    if link in text:
        return

    if source_file not in text:
        text = _prepend_source_to_frontmatter(text, source_file)

    text += f"\n\nSee also: {link}"
    path.write_text(text, encoding="utf-8")


def _backlink_summary(wiki_dir: Path, doc_name: str, concept_slugs: list[str]) -> None:
    """Append missing concept wikilinks to the summary page (no LLM call).

    After all concepts are generated, this ensures the summary page links
    back to every related concept — closing the bidirectional link that
    concept pages already have toward the summary.

    If a ``## Related Concepts`` section already exists, new links are
    appended into it rather than creating a duplicate section.
    """
    summary_path = wiki_dir / "summaries" / f"{doc_name}.md"
    if not summary_path.exists():
        return

    text = summary_path.read_text(encoding="utf-8")
    missing = [slug for slug in concept_slugs if f"[[concepts/{slug}]]" not in text]
    if not missing:
        return

    lines = text.split("\n")
    _ensure_h2_section(lines, "## Related Concepts")
    for slug in reversed(missing):
        _insert_section_entry(lines, "## Related Concepts", f"- [[concepts/{slug}]]")
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def _backlink_concepts(wiki_dir: Path, doc_name: str, concept_slugs: list[str]) -> None:
    """Append missing summary wikilink to each concept page (no LLM call).

    Ensures every concept page links back to the source document's summary,
    regardless of whether the LLM included the link in its output.

    If a ``## Related Documents`` section already exists, the link is
    appended into it rather than creating a duplicate section.
    """
    link = f"[[summaries/{doc_name}]]"
    concepts_dir = wiki_dir / "concepts"

    for slug in concept_slugs:
        path = concepts_dir / f"{slug}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if link in text:
            continue
        lines = text.split("\n")
        _ensure_h2_section(lines, "## Related Documents")
        _insert_section_entry(lines, "## Related Documents", f"- {link}")
        path.write_text("\n".join(lines), encoding="utf-8")

def _update_index(
    wiki_dir: Path, doc_name: str, concept_names: list[str],
    doc_brief: str = "", concept_briefs: dict[str, str] | None = None,
    doc_type: str = "short",
) -> None:
    """Append document and concept entries to index.md.

    When ``doc_brief`` or entries in ``concept_briefs`` are provided, entries
    are written as ``- [[link]] (type) — brief text``. Existing entries are
    detected within their own section by exact entry prefix and skipped to
    avoid duplicates.
    ``doc_type`` is ``"short"`` or ``"pageindex"`` — shown in the entry so the
    query agent knows how to access detailed content.
    """
    if concept_briefs is None:
        concept_briefs = {}

    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        index_path.write_text(
            "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )

    lines = index_path.read_text(encoding="utf-8").split("\n")

    _ensure_h2_section(lines, "## Documents")
    if concept_names:
        _ensure_h2_section(lines, "## Concepts")

    doc_link = f"[[summaries/{doc_name}]]"
    if not _section_contains_link(lines, "## Documents", doc_link):
        doc_entry = f"- {doc_link} ({doc_type})"
        if doc_brief:
            doc_entry += f" — {doc_brief}"
        _insert_section_entry(lines, "## Documents", doc_entry)

    for name in concept_names:
        concept_link = f"[[concepts/{name}]]"
        concept_entry = f"- {concept_link}"
        if name in concept_briefs:
            concept_entry += f" — {concept_briefs[name]}"
        if _section_contains_link(lines, "## Concepts", concept_link):
            if name in concept_briefs:
                _replace_section_entry(lines, "## Concepts", concept_link, concept_entry)
        else:
            _insert_section_entry(lines, "## Concepts", concept_entry)

    index_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DEFAULT_COMPILE_CONCURRENCY = 5


def _format_known_targets(targets: set[str]) -> str:
    """Format the whitelist as a bulleted Markdown list for prompt injection."""
    if not targets:
        return "(none yet — do not use any [[wikilinks]] in your output)"
    return "\n".join(f"- {t}" for t in sorted(targets))


async def _compile_concepts(
    wiki_dir: Path,
    kb_dir: Path,
    model: str,
    system_msg: dict,
    doc_msg: dict,
    summary: str,
    doc_name: str,
    max_concurrency: int,
    doc_brief: str = "",
    doc_type: str = "short",
    rewrite_summary: bool = False,
) -> None:
    """Shared Steps 2-4: concepts plan → generate/update → index.

    Uses ``_CONCEPTS_PLAN_USER`` to get a plan with create/update/related
    actions, then executes each action type accordingly. Concept bodies are
    generated in memory, scrubbed of unresolved wikilinks, and only then
    written to disk. When ``rewrite_summary=True`` (short-doc path), the
    summary is rewritten by the LLM after concepts are finalized so its
    wikilinks reflect the actual concept pages on disk.
    """
    source_file = f"summaries/{doc_name}.md"

    # --- Step 2: Get concepts plan (A cached) ---
    concept_briefs = _read_concept_briefs(wiki_dir)

    # Second cache breakpoint: end of the assistant summary message. Covers
    # (system + doc + summary) for the plan call and every concept call.
    summary_msg = {"role": "assistant", "content": _cached_text(summary)}

    plan_raw = _llm_call(model, [
        system_msg,
        doc_msg,
        summary_msg,
        {"role": "user", "content": _CONCEPTS_PLAN_USER.format(
            concept_briefs=concept_briefs,
        )},
    ], "concepts-plan", max_tokens=1024)

    def _write_v1_summary_stripped() -> None:
        """Fallback writer for the v1 summary on early-return paths.

        Strips against the set of wikilink targets currently on disk before
        writing, so the v1 summary's LLM-hallucinated links don't slip past
        the ghost-link defense when plan parsing fails or the plan is empty.
        ``plan.create`` slugs are unknown at this point, so the whitelist
        is just what physically exists.
        """
        fallback_targets = list_existing_wiki_targets(wiki_dir)
        fallback_targets.add(f"summaries/{doc_name}")
        cleaned, ghosts = strip_ghost_wikilinks(summary, fallback_targets)
        if ghosts:
            logger.info(
                "stripped %d ghost wikilink(s) from fallback v1 summary %s: %s",
                len(ghosts), doc_name, ghosts[:5],
            )
        _write_summary(wiki_dir, doc_name, cleaned)

    try:
        parsed = _parse_json(plan_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse concepts plan: %s", exc)
        logger.debug("Raw: %s", plan_raw)
        if rewrite_summary:
            _write_v1_summary_stripped()
        _update_index(wiki_dir, doc_name, [], doc_brief=doc_brief, doc_type=doc_type)
        return

    # Fallback: if LLM returns a flat list, treat all items as "create"
    if isinstance(parsed, list):
        plan = {"create": parsed, "update": [], "related": []}
    else:
        plan = {
            "create": parsed.get("create", []),
            "update": parsed.get("update", []),
            "related": parsed.get("related", []),
        }

    create_items = plan["create"]
    update_items = plan["update"]
    related_items = plan["related"]

    if not create_items and not update_items and not related_items:
        if rewrite_summary:
            _write_v1_summary_stripped()
        _update_index(wiki_dir, doc_name, [], doc_brief=doc_brief, doc_type=doc_type)
        return

    # Build the whitelist of valid wikilink targets the LLM may emit. It
    # combines what already exists on disk with what *this* round will
    # produce (plan.create + plan.update + plan.related), plus the
    # summary about to be written for this document.
    planned_slugs = {
        _sanitize_concept_name(c["name"]) for c in create_items + update_items
    } | {
        _sanitize_concept_name(s) for s in related_items
    }
    known_targets: set[str] = (
        list_existing_wiki_targets(wiki_dir)
        | {f"concepts/{s}" for s in planned_slugs}
        | {f"summaries/{doc_name}"}
    )
    known_targets_str = _format_known_targets(known_targets)

    # Third cache breakpoint: the whitelist of valid wikilink targets. By
    # carrying this list in its own cached user message — placed between
    # summary_msg (BP2) and each per-concept user turn — every concept
    # generation call and the summary-rewrite call reuses the whitelist
    # tokens from cache instead of re-billing them on every request. This
    # matters as the KB grows (the list can reach 5-10k tokens for a
    # 500-concept wiki). Plan call deliberately omits this message — at
    # plan time the whitelist isn't known yet, and plan uses concept_briefs
    # via _CONCEPTS_PLAN_USER instead.
    known_targets_msg = {
        "role": "user",
        "content": _cached_text(_KNOWN_TARGETS_USER.format(
            known_targets=known_targets_str,
        )),
    }

    # --- Step 3: Generate/update concept pages concurrently (A cached) ---
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _gen_create(concept: dict) -> tuple[str, str, bool, str]:
        name = concept["name"]
        title = concept.get("title", name)
        async with semaphore:
            raw = await _llm_call_async(model, [
                system_msg,
                doc_msg,             # cached (BP1)
                summary_msg,         # cached (BP2)
                known_targets_msg,   # cached (BP3) — whitelist
                {"role": "user", "content": _CONCEPT_PAGE_USER.format(
                    title=title, doc_name=doc_name,
                    update_instruction="",
                )},
            ], f"concept: {name}")
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            content = parsed.get("content", raw)
        except (json.JSONDecodeError, ValueError):
            brief, content = "", raw
        return name, content, False, brief

    async def _gen_update(concept: dict) -> tuple[str, str, bool, str]:
        name = concept["name"]
        title = concept.get("title", name)
        concept_path = wiki_dir / "concepts" / f"{_sanitize_concept_name(name)}.md"
        if concept_path.exists():
            raw_text = concept_path.read_text(encoding="utf-8")
            if raw_text.startswith("---"):
                parts = raw_text.split("---", 2)
                existing_content = parts[2].strip() if len(parts) >= 3 else raw_text
            else:
                existing_content = raw_text
        else:
            existing_content = "(page not found — create from scratch)"
        async with semaphore:
            raw = await _llm_call_async(model, [
                system_msg,
                doc_msg,             # cached (BP1)
                summary_msg,         # cached (BP2)
                known_targets_msg,   # cached (BP3) — whitelist
                {"role": "user", "content": _CONCEPT_UPDATE_USER.format(
                    title=title, doc_name=doc_name,
                    existing_content=existing_content,
                )},
            ], f"update: {name}")
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            content = parsed.get("content", raw)
        except (json.JSONDecodeError, ValueError):
            brief, content = "", raw
        return name, content, True, brief

    tasks = []
    tasks.extend(_gen_create(c) for c in create_items)
    tasks.extend(_gen_update(c) for c in update_items)

    concept_names: list[str] = []
    concept_briefs_map: dict[str, str] = {}
    pending_writes: list[tuple[str, str, bool, str]] = []

    if tasks:
        total = len(tasks)
        sys.stdout.write(f"    Generating {total} concept(s) (concurrency={max_concurrency})...\n")
        sys.stdout.flush()

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                logger.warning("Concept generation failed: %s", r)
                continue
            name, page_content, is_update, brief = r
            pending_writes.append((name, page_content, is_update, brief))
            safe_name = _sanitize_concept_name(name)
            concept_names.append(safe_name)
            if brief:
                concept_briefs_map[safe_name] = brief

    # Strip unresolved wikilinks from concept bodies before writing. The
    # whitelist includes existing files + this round's planned slugs +
    # the summary for this document.
    for i, (name, page_content, is_update, brief) in enumerate(pending_writes):
        cleaned, ghosts = strip_ghost_wikilinks(page_content, known_targets)
        if ghosts:
            logger.info(
                "stripped %d ghost wikilink(s) from concept %s: %s",
                len(ghosts), name, ghosts[:5],
            )
        pending_writes[i] = (name, cleaned, is_update, brief)

    # --- Optional Step 3a: LLM rewrite the summary with full whitelist ---
    # Only for the short-doc path. The long-doc path leaves the indexer-
    # written summary untouched.
    #
    # The rewrite call is best-effort: on any failure (API error, empty
    # response, exception) we fall back to the v1 summary stripped against
    # the full whitelist, so the summary is always written and never wiped.
    if rewrite_summary:
        candidate: str | None = None
        try:
            # No max_tokens cap — matches the v1 summary call. The rewrite
            # prompt asks the model to keep length within ±20% of the v1.
            rewrite_raw = _llm_call(model, [
                system_msg,
                doc_msg,            # cached (BP1)
                summary_msg,        # cached (BP2) — contains the v1 summary text
                known_targets_msg,  # cached (BP3) — whitelist
                {"role": "user", "content": _SUMMARY_REWRITE_USER},
            ], "summary-rewrite")
            candidate = rewrite_raw.strip()
            # Strip frontmatter if the model added one anyway.
            if candidate.startswith("---"):
                end = candidate.find("---", 3)
                if end != -1:
                    candidate = candidate[end + 3:].lstrip("\n")
            # Safety net: strip any wikilink the rewrite emitted that is
            # not in the whitelist.
            candidate, summary_ghosts = strip_ghost_wikilinks(
                candidate, known_targets
            )
            if summary_ghosts:
                logger.info(
                    "stripped %d ghost wikilink(s) from summary %s: %s",
                    len(summary_ghosts), doc_name, summary_ghosts[:5],
                )
        except Exception as exc:
            logger.warning(
                "summary-rewrite failed for %s: %s. Falling back to v1.",
                doc_name, exc,
            )
            candidate = None

        if candidate:
            final_summary = candidate
        else:
            # Rewrite produced no content (empty response or exception).
            # Strip the v1 summary against the same whitelist so the
            # fallback doesn't reintroduce ghost links.
            if candidate is not None:
                logger.warning(
                    "summary-rewrite returned empty for %s; using v1 fallback.",
                    doc_name,
                )
            final_summary, fallback_ghosts = strip_ghost_wikilinks(
                summary, known_targets,
            )
            if fallback_ghosts:
                logger.info(
                    "stripped %d ghost wikilink(s) from v1 fallback summary %s: %s",
                    len(fallback_ghosts), doc_name, fallback_ghosts[:5],
                )
        _write_summary(wiki_dir, doc_name, final_summary)

    # --- Write concept pages to disk ---
    for name, page_content, is_update, brief in pending_writes:
        _write_concept(
            wiki_dir, name, page_content, source_file, is_update, brief=brief,
        )

    # --- Step 3b: Process related items (code only, no LLM) ---
    sanitized_related = [_sanitize_concept_name(s) for s in related_items]
    for slug in sanitized_related:
        _add_related_link(wiki_dir, slug, doc_name, source_file)

    # --- Step 3c: Backlink — summary ↔ concepts (code only) ---
    all_concept_slugs = concept_names + sanitized_related
    if all_concept_slugs:
        _backlink_summary(wiki_dir, doc_name, all_concept_slugs)
        _backlink_concepts(wiki_dir, doc_name, all_concept_slugs)

    # --- Step 4: Update index (code only) ---
    _update_index(wiki_dir, doc_name, concept_names,
                  doc_brief=doc_brief, concept_briefs=concept_briefs_map,
                  doc_type=doc_type)


async def compile_short_doc(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    model: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a short document using a multi-step LLM pipeline with caching.

    Step 1: Build base context A (schema + doc content), generate summary.
    Steps 2-4: Delegated to ``_compile_concepts``.
    """
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_dir = kb_dir / "wiki"
    schema_md = get_agents_md(wiki_dir)
    content = source_path.read_text(encoding="utf-8")

    # Base context A: system + document. cache_control marker on the doc
    # message creates a cache breakpoint that covers (system + doc) for
    # every downstream call (summary, concepts-plan, every concept page).
    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _cached_text(_SUMMARY_USER.format(
        doc_name=doc_name, content=content,
    ))}

    # --- Step 1: Generate summary (v1, held in memory) ---
    # The summary is NOT written to disk yet — it's used as cache context
    # for the plan + concept-generation calls, then rewritten into a final
    # v2 (with a whitelist of known wikilink targets) inside
    # _compile_concepts before being written to disk.
    summary_raw = _llm_call(model, [system_msg, doc_msg], "summary")
    try:
        summary_parsed = _parse_json(summary_raw)
        doc_brief = summary_parsed.get("brief", "")
        summary = summary_parsed.get("content", summary_raw)
    except (json.JSONDecodeError, ValueError):
        doc_brief = ""
        summary = summary_raw

    # --- Steps 2-4: Concept plan → generate/update → summary rewrite → index ---
    await _compile_concepts(
        wiki_dir, kb_dir, model, system_msg, doc_msg,
        summary, doc_name, max_concurrency, doc_brief=doc_brief,
        doc_type="short", rewrite_summary=True,
    )


async def compile_long_doc(
    doc_name: str,
    summary_path: Path,
    doc_id: str,
    kb_dir: Path,
    model: str,
    doc_description: str = "",
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a long (PageIndex) document's concepts and index.

    The summary page is already written by the indexer. This function
    generates concept pages and updates the index.
    """
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_dir = kb_dir / "wiki"
    schema_md = get_agents_md(wiki_dir)
    summary_content = summary_path.read_text(encoding="utf-8")

    # Base context A. cache_control marker on the doc message creates a
    # cache breakpoint covering (system + doc) for every concept call.
    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _cached_text(_LONG_DOC_SUMMARY_USER.format(
        doc_name=doc_name, doc_id=doc_id, content=summary_content,
    ))}

    # --- Step 1: Generate overview ---
    overview = _llm_call(model, [system_msg, doc_msg], "overview")

    # --- Steps 2-4: Concept plan → generate/update → index ---
    await _compile_concepts(
        wiki_dir, kb_dir, model, system_msg, doc_msg,
        overview, doc_name, max_concurrency, doc_brief=doc_description,
        doc_type="pageindex",
    )
