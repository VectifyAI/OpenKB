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
import yaml

from openkb.config import DEFAULT_ENTITY_TYPES, resolve_entity_types
from openkb.lint import list_existing_wiki_targets, strip_ghost_wikilinks
from openkb.schema import INDEX_SEED, get_agents_md

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

# DeepSeek/Qwen require the prompt itself to mention "json" when this kwarg
# is set; the templates below already do.
_JSON_RESPONSE_FORMAT = {"type": "json_object"}

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


# Default entity-type enum lives in the config layer (so config validation is
# centralized there and reusable by any command). ``_ENTITY_TYPE_LIST`` /
# ``_ENTITY_TYPES`` are the default name + validation set used when no
# config-driven set is threaded through; the EFFECTIVE set is resolved per-KB
# via ``resolve_entity_types(config)`` and substituted into the plan +
# entity-page prompts at call time inside ``_compile_concepts`` via the
# ``__ENTITY_TYPES__`` token.
_ENTITY_TYPE_LIST = DEFAULT_ENTITY_TYPES
_ENTITY_TYPES = frozenset(_ENTITY_TYPE_LIST)


_CONCEPTS_PLAN_USER = """\
Based on the summary above, decide how to update the wiki's CONCEPT pages and
ENTITY pages.

A CONCEPT is an abstract, recurring idea/pattern/mechanism (e.g. "agentic
systems"). An ENTITY is a specific named thing — a person, organization,
place, product, named work, or event (e.g. "Anthropic"). Each name goes in
exactly ONE group. A topic may have both (entity "NVIDIA" and concept
"ai-infrastructure-demand"); they cross-link, they do not merge.

Existing concept pages:
{concept_briefs}

Existing entity pages (with source counts = how many docs already cite them):
{entity_briefs}

Return a JSON object with two top-level keys, "concepts" and "entities".

"concepts" is an object with:
1. "create" — new concepts. Array of {{"name": "concept-slug", "title": "Title"}}
2. "update" — existing concepts with significant new info. Same shape.
3. "related" — existing concept slugs to cross-link only. Array of strings.

"entities" is an object with the same three keys, but create/update objects
add a "type" field, one of: __ENTITY_TYPES__. Example:
   {{"name": "anthropic", "title": "Anthropic", "type": "organization"}}

Rules:
- For the first few documents, create 2-3 foundational concepts at most.
- Create an ENTITY page only when the entity is (a) central to this document
  or (b) likely to recur across sources. Do NOT page proper nouns mentioned
  only in passing. Roughly 5-15 entities per document is typical; fewer for
  sparse documents.
- Prefer "update" over "create" for any concept or entity already listed above.
- Do NOT create a concept/entity that overlaps an existing one — use "update".
- Do NOT create concepts that are just the document topic itself.
- "related" is lightweight cross-linking only, no content rewrite.

Return ONLY valid JSON, no fences, no explanation.
"""

_KNOWN_TARGETS_USER = """\
The wiki currently contains these pages, and they are the COMPLETE list of \
valid [[wikilink]] targets you may use in the responses that follow:

{known_targets}

Rules for [[wikilinks]] in all subsequent responses:
- For [[concepts/X]]: X must appear in the whitelist above.
- For [[summaries/Y]]: Y must appear in the whitelist above.
- For [[entities/Z]]: Z must appear in the whitelist above.
- Do NOT invent new wikilink targets. If you want to mention a concept \
or entity that is not in the whitelist, write it as plain text without brackets.
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

_ENTITY_PAGE_USER = """\
Write the entity page for: {title} (type: {type})

This entity relates to the document "{doc_name}" summarized above.

Return a JSON object with three keys:
- "brief": A single sentence (under 100 chars) identifying this entity
- "type": one of __ENTITY_TYPES__
- "content": The full entity page in Markdown — what this entity is, the key
  facts about it from this document, and [[wikilinks]] to related concepts,
  other [[entities/...]], and [[summaries/{doc_name}]] — subject to the
  whitelist rules from the message above.

Return ONLY valid JSON, no fences.
"""

_ENTITY_UPDATE_USER = """\
Update the entity page for: {title} (type: {type})

Current content of this page:
{existing_content}

Integrate the new facts about this entity from document "{doc_name}"
(summarized above). Rewrite the full page — do not just append. Preserve the
existing structure and intent. Follow the whitelist rules from the message
above for all [[wikilinks]].

Return a JSON object with three keys:
- "brief": A single sentence (under 100 chars) identifying this entity
- "type": one of __ENTITY_TYPES__
- "content": The rewritten full entity page in Markdown

Return ONLY valid JSON, no fences.
"""

# NOTE: the prompt templates intentionally KEEP the literal ``__ENTITY_TYPES__``
# token at import time. The effective entity-type list is resolved per-compile
# from config (see ``resolve_entity_types``) and substituted via ``str.replace``
# at call time inside ``_compile_concepts``. This lets ``entity_types:`` in
# ``.openkb/config.yaml`` override the default enum everywhere at once. The
# token is a plain string (not a ``{}`` placeholder) so it does not collide with
# the ``{{ }}`` JSON braces these templates feed to ``str.format``.

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
    _warn_if_truncated(response, step_name, kwargs.get("max_tokens"))

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
    _warn_if_truncated(response, step_name, kwargs.get("max_tokens"))

    elapsed = time.time() - t0
    sys.stdout.write(f"    {step_name}... {_format_usage(elapsed, response.usage)}\n")
    sys.stdout.flush()
    logger.debug("LLM response [%s]:\n%s", step_name, content[:500] + ("..." if len(content) > 500 else ""))
    return content.strip()


def _warn_if_truncated(response, step_name: str, max_tokens: int | None) -> None:
    """Emit a warning when the LLM hit the max_tokens cap.

    ``json_repair`` will silently salvage the truncated prefix, so without
    this the caller can't tell a short response from a cut-off one.
    """
    try:
        finish_reason = response.choices[0].finish_reason
    except (AttributeError, IndexError):
        return
    if finish_reason != "length":
        return
    cap = f" (max_tokens={max_tokens})" if max_tokens else ""
    logger.warning("LLM [%s] hit length limit%s — output may be truncated.",
                   step_name, cap)
    sys.stdout.write(f"    [WARN] {step_name} hit length limit{cap} — output may be truncated.\n")
    sys.stdout.flush()


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


def _filter_concept_items(items: list, label: str) -> list[dict]:
    """Keep only dicts that carry a non-empty ``name``; warn about anything else."""
    if not isinstance(items, list):
        logger.warning("concepts plan: %s was %s, expected list — dropping",
                       label, type(items).__name__)
        return []
    valid = [c for c in items if isinstance(c, dict) and isinstance(c.get("name"), str) and c["name"].strip()]
    if len(valid) < len(items):
        reasons: list[str] = []
        for c in items:
            if not isinstance(c, dict):
                reasons.append(type(c).__name__)
            elif not isinstance(c.get("name"), str) or not c["name"].strip():
                reasons.append("dict-missing-name")
        logger.warning(
            "concepts plan: dropped %d malformed %s item(s) (reasons: %s)",
            len(items) - len(valid), label, ", ".join(sorted(set(reasons))),
        )
    return valid


def _require_nonempty_content(content, name: str) -> None:
    """Raise if a concept body is missing or whitespace-only."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"LLM returned empty content for concept {name!r}")


def _filter_related_slugs(items: list) -> list[str]:
    """Keep only non-empty string slugs; warn about anything else."""
    if not isinstance(items, list):
        logger.warning("concepts plan: related was %s, expected list — dropping",
                       type(items).__name__)
        return []
    valid = [s for s in items if isinstance(s, str) and s.strip()]
    if len(valid) < len(items):
        bad_types = sorted({type(s).__name__ for s in items if not (isinstance(s, str) and s.strip())})
        logger.warning(
            "concepts plan: dropped %d malformed related item(s) (types: %s)",
            len(items) - len(valid), ", ".join(bad_types),
        )
    return valid


def _filter_entity_items(
    items: object, valid_types: frozenset | None = None
) -> list[dict]:
    """Validate entity create/update objects: require name+title, coerce type.

    Each kept item is normalized to ``{"name", "title", "type"}`` where
    ``type`` falls back to ``"other"`` when missing or outside ``valid_types``
    and ``title`` falls back to ``name``. ``valid_types`` defaults to the
    module-level ``_ENTITY_TYPES`` so callers that don't thread a config-driven
    set keep today's behavior.
    """
    if valid_types is None:
        valid_types = _ENTITY_TYPES
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        title = it.get("title") if isinstance(it.get("title"), str) else name
        etype = it.get("type")
        if not isinstance(etype, str) or etype not in valid_types:
            etype = "other"
        out.append({"name": name, "title": title, "type": etype})
    return out


def _parse_entities_plan(parsed: object, valid_types: frozenset | None = None) -> dict:
    """Extract the entities group from a plan dict, with graceful fallback.

    Returns ``{"create": [...], "update": [...], "related": [...]}``. A
    missing/malformed ``entities`` key yields empty lists, so older or
    partial LLM responses never raise.
    """
    empty = {"create": [], "update": [], "related": []}
    if not isinstance(parsed, dict):
        return empty
    group = parsed.get("entities")
    if not isinstance(group, dict):
        return empty
    return {
        "create": _filter_entity_items(group.get("create", []), valid_types),
        "update": _filter_entity_items(group.get("update", []), valid_types),
        "related": _filter_related_slugs(group.get("related", [])),
    }


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
                fm_text = text[3:end].strip("\n")
                body = text[end + 3:]
                try:
                    fm = yaml.safe_load(fm_text)
                except yaml.YAMLError:
                    fm = None
                if isinstance(fm, dict) and isinstance(fm.get("brief"), str):
                    brief = fm["brief"].strip()
        if not brief:
            brief = body.strip().replace("\n", " ")[:150]
        if brief:
            lines.append(f"- {path.stem}: {brief}")

    return "\n".join(lines) or "(none yet)"


def _read_entity_briefs(wiki_dir: Path) -> str:
    """Read existing entity pages as compact lines for the plan call.

    Formats each as ``- {slug} ({type}, {n} sources) — {brief}``. The source
    count is the cross-document recurrence signal the LLM uses to decide
    create-vs-update and salience. Returns "(none yet)" when empty.
    """
    entities_dir = wiki_dir / "entities"
    if not entities_dir.exists():
        return "(none yet)"

    md_files = sorted(entities_dir.glob("*.md"))
    if not md_files:
        return "(none yet)"

    lines: list[str] = []
    for path in md_files:
        text = path.read_text(encoding="utf-8")
        brief = ""
        etype = "other"
        n_sources = 0
        body = text
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                fm_text = text[3:end].strip("\n")
                body = text[end + 3:]
                try:
                    fm = yaml.safe_load(fm_text)
                except yaml.YAMLError:
                    fm = None
                if isinstance(fm, dict):
                    if isinstance(fm.get("brief"), str):
                        brief = fm["brief"].strip()
                    if isinstance(fm.get("type"), str):
                        etype = fm["type"].strip() or "other"
                    if isinstance(fm.get("sources"), list):
                        n_sources = len(fm["sources"])
        if not brief:
            brief = body.strip().replace("\n", " ")[:150]
        suffix = f" — {brief}" if brief else ""
        lines.append(f"- {path.stem} ({etype}, {n_sources} sources){suffix}")

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


def _ensure_h2_section(lines: list[str], heading: str, *, quiet: bool = False) -> None:
    """Ensure an H2 section ``heading`` exists in ``lines``; append if missing.

    Recovers from hand-edited or drifted index.md files where the expected
    section was removed or renamed — without this, downstream inserts would
    silently no-op and entries would be dropped.

    ``quiet=True`` suppresses the drift warning. Use it when adding a section
    is the normal, expected operation (e.g. a backlink helper creating a
    ``## Related Documents`` / ``## Entities`` section on a page for the first
    time), as opposed to repairing a drifted index.
    """
    if _get_section_bounds(lines, heading) is not None:
        return
    if not quiet:
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


def _ensure_h2_section_before(
    lines: list[str], heading: str, before: str,
) -> None:
    """Ensure H2 ``heading`` exists, inserting it just before ``before``.

    If ``heading`` is already present, no-op. If ``before`` is absent, fall
    back to :func:`_ensure_h2_section` (append at end). This keeps the
    canonical index order (e.g. ``## Entities`` ahead of ``## Explorations``)
    when recovering an older index.md that predates the section.
    """
    if _get_section_bounds(lines, heading) is not None:
        return
    before_bounds = _get_section_bounds(lines, before)
    if before_bounds is None:
        _ensure_h2_section(lines, heading)
        return
    # ``start`` is the line after the ``before`` heading; insert the new
    # section (heading + blank line) right before that heading line.
    insert_at = before_bounds[0] - 1
    logger.warning(
        "Wiki index is missing %r section; inserting it before %r. "
        "Check whether the file was hand-edited away from the canonical layout.",
        heading, before,
    )
    lines[insert_at:insert_at] = [heading, ""]


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


def _remove_section_entry(lines: list[str], heading: str, link: str) -> bool:
    """Remove the first entry whose line starts with ``- {link}`` in the named
    section. Returns True if an entry was removed.

    Matching is intentionally strict (prefix-only, matching the canonical
    bullet form written by ``_insert_section_entry`` and friends). An earlier
    substring fallback could wrongly delete sibling bullets whose brief text
    referenced the removed link.
    """
    bounds = _get_section_bounds(lines, heading)
    if bounds is None:
        return False

    start, end = bounds
    entry_prefix = f"- {link}"
    for i in range(start, end):
        if lines[i].startswith(entry_prefix):
            del lines[i]
            return True
    return False



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


def _yaml_kv_line(key: str, value: str) -> str:
    """Render a single ``key: value`` line that round-trips through any YAML loader.

    Uses ``json.dumps`` for the value — JSON strings are a strict subset of
    YAML, always single-line, always correctly escaped (newlines, quotes,
    control chars), and never auto-promoted to multi-line block scalars.
    """
    return f"{key}: {json.dumps(value, ensure_ascii=False)}"


def _yaml_list_line(key: str, items: list[str]) -> str:
    """Render ``key: [a, b, c]`` as JSON-style YAML (always single-line, always valid)."""
    return f"{key}: {json.dumps(list(items), ensure_ascii=False)}"


def _parse_yaml_list_value(line: str) -> list[str] | None:
    """Parse the right-hand side of ``key: [...]`` into a list of strings.

    Returns ``None`` when the value cannot be interpreted as a list — callers
    treat that as "leave the frontmatter alone".
    """
    colon = line.find(":")
    if colon == -1:
        return None
    try:
        parsed = yaml.safe_load(line[colon + 1:])
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, list):
        return None
    return [str(x) for x in parsed]


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
                brief_line = _yaml_kv_line("brief", brief)
                if "brief:" in fm:
                    # Lambda to bypass re.sub backref interpretation in the
                    # replacement string (brief may contain \1, \g<…>, etc.).
                    fm = re.sub(r"brief:.*", lambda _m: brief_line, fm)
                else:
                    fm = fm.replace("---\n", f"---\n{brief_line}\n", 1)
                existing = fm + body
        path.write_text(existing, encoding="utf-8")
    else:
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:].lstrip("\n")
        fm_lines = [_yaml_list_line("sources", [source_file])]
        if brief:
            fm_lines.append(_yaml_kv_line("brief", brief))
        frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
        path.write_text(frontmatter + content, encoding="utf-8")


def _write_entity(
    wiki_dir: Path, name: str, content: str, source_file: str,
    is_update: bool, brief: str = "", type_: str = "other",
    aliases: list[str] | None = None,
) -> None:
    """Write or update an entity page in entities/, managing frontmatter.

    Frontmatter fields: ``sources`` (list), ``type`` (one of the entity
    enum), ``brief`` (one-liner), and optional ``aliases`` (list, omitted
    when empty). On update the new source is prepended and the body replaced
    with the LLM rewrite; ``type`` is preserved from the new write.
    """
    entities_dir = wiki_dir / "entities"
    entities_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_concept_name(name)
    path = (entities_dir / f"{safe_name}.md").resolve()
    if not path.is_relative_to(entities_dir.resolve()):
        logger.warning("Entity name escapes entities dir: %s", name)
        return

    # Strip any frontmatter the LLM body may carry.
    clean = content
    if clean.startswith("---"):
        end = clean.find("---", 3)
        if end != -1:
            clean = clean[end + 3:].lstrip("\n")

    def _build_frontmatter(sources: list[str]) -> str:
        fm_lines = [_yaml_list_line("sources", sources)]
        fm_lines.append(_yaml_kv_line("type", type_ or "other"))
        if brief:
            fm_lines.append(_yaml_kv_line("brief", brief))
        if aliases:
            fm_lines.append(_yaml_list_line("aliases", aliases))
        return "---\n" + "\n".join(fm_lines) + "\n---\n\n"

    if is_update and path.exists():
        existing = path.read_text(encoding="utf-8")
        if source_file not in existing:
            existing = _prepend_source_to_frontmatter(existing, source_file)
        end = existing.find("---", 3) if existing.startswith("---") else -1
        if end != -1:
            fm = existing[:end + 3]
            fm = _set_fm_line(fm, "brief", brief) if brief else fm
            fm = _set_fm_line(fm, "type", type_) if type_ else fm
            existing = fm + "\n\n" + clean
        else:
            # Malformed/absent frontmatter (opening ``---`` with no closing
            # delimiter, or no frontmatter at all): rebuild valid frontmatter
            # rather than writing a body-only page. Recover any sources already
            # listed in the broken block first — otherwise a multi-source
            # entity would be truncated to just this document.
            recovered: list[str] = []
            for ln in existing.split("\n"):
                if ln.lstrip().startswith("sources:"):
                    parsed = _parse_yaml_list_value(ln)
                    if parsed:
                        recovered = parsed
                    break
            merged = [source_file] + [s for s in recovered if s != source_file]
            existing = _build_frontmatter(merged) + clean
        path.write_text(existing, encoding="utf-8")
        return

    path.write_text(_build_frontmatter([source_file]) + clean, encoding="utf-8")


def _set_fm_line(fm: str, key: str, value: str) -> str:
    """Set or replace a single scalar ``key:`` line inside a frontmatter block.

    ``fm`` includes the opening and closing ``---`` markers. Uses a lambda
    replacement so values containing regex backrefs are inserted literally.
    """
    line = _yaml_kv_line(key, value)
    if re.search(rf"^{re.escape(key)}:", fm, flags=re.MULTILINE):
        return re.sub(rf"^{re.escape(key)}:.*", lambda _m: line, fm, count=1, flags=re.MULTILINE)
    return fm.replace("---\n", f"---\n{line}\n", 1)


def _prepend_source_to_frontmatter(text: str, source_file: str) -> str:
    """Prepend ``source_file`` to the inline ``sources:`` list in YAML frontmatter.

    Creates the frontmatter or the ``sources:`` line if missing. Returns the
    text unchanged if ``source_file`` is already present in the list, or if
    the frontmatter is malformed (no closing ``---``).
    """
    if not text.startswith("---"):
        return f"---\n{_yaml_list_line('sources', [source_file])}\n---\n\n" + text

    fm_end = text.find("---", 3)
    if fm_end == -1:
        return text

    fm_block = text[:fm_end]
    body = text[fm_end:]
    fm_lines = fm_block.split("\n")

    for i, line in enumerate(fm_lines):
        if not line.lstrip().startswith("sources:"):
            continue
        items = _parse_yaml_list_value(line)
        if items is None:
            return text
        if source_file in items:
            return text
        items.insert(0, source_file)
        fm_lines[i] = _yaml_list_line("sources", items)
        return "\n".join(fm_lines) + body

    fm_lines.insert(1, _yaml_list_line("sources", [source_file]))
    return "\n".join(fm_lines) + body


def _remove_source_from_frontmatter(text: str, source_file: str) -> tuple[str, bool]:
    """Remove ``source_file`` from the inline ``sources:`` list in YAML frontmatter.

    Returns ``(rewritten_text, sources_now_empty)``. ``sources_now_empty`` is
    True when ``source_file`` was the only remaining item in the list (callers
    can use this to decide whether to delete the page entirely).

    If the frontmatter is missing, malformed, has no ``sources:`` line, or
    the source is not present in the list, returns ``(text, False)``.
    """
    if not text.startswith("---"):
        return text, False

    fm_end = text.find("---", 3)
    if fm_end == -1:
        return text, False

    fm_block = text[:fm_end]
    body = text[fm_end:]
    fm_lines = fm_block.split("\n")

    for i, line in enumerate(fm_lines):
        if not line.lstrip().startswith("sources:"):
            continue
        items = _parse_yaml_list_value(line)
        if items is None:
            return text, False
        if source_file not in items:
            return text, False
        items.remove(source_file)
        fm_lines[i] = _yaml_list_line("sources", items)
        return "\n".join(fm_lines) + body, len(items) == 0

    return text, False


def _add_related_link(
    wiki_dir: Path, slug: str, doc_name: str, source_file: str,
    page_dir: str = "concepts",
) -> bool:
    """Add a cross-reference link to an existing page (no LLM call).

    Works for any page directory (``concepts`` or ``entities``). Returns True
    when the page exists (whether or not a link was added), so callers can
    track which related slugs are real pages. The standalone ``See also:``
    paragraph it writes is symmetric with ``remove_doc_from_pages``' cleanup.
    """
    path = wiki_dir / page_dir / f"{slug}.md"
    if not path.exists():
        return False

    text = path.read_text(encoding="utf-8")
    link = f"[[summaries/{doc_name}]]"
    if link in text:
        return True

    if source_file not in text:
        text = _prepend_source_to_frontmatter(text, source_file)

    text += f"\n\nSee also: {link}"
    path.write_text(text, encoding="utf-8")
    return True


def _backlink_summary_pages(
    wiki_dir: Path, doc_name: str, slugs: list[str],
    *, page_dir: str, section: str,
) -> None:
    """Append missing ``[[{page_dir}/slug]]`` wikilinks to the summary page.

    Closes the bidirectional link the pages already hold toward the summary,
    inserting them under ``section`` (created if absent). Shared by the
    concept and entity summary-backlink wrappers below.
    """
    summary_path = wiki_dir / "summaries" / f"{doc_name}.md"
    if not summary_path.exists():
        return

    text = summary_path.read_text(encoding="utf-8")
    missing = [slug for slug in slugs if f"[[{page_dir}/{slug}]]" not in text]
    if not missing:
        return

    lines = text.split("\n")
    _ensure_h2_section(lines, section, quiet=True)
    for slug in reversed(missing):
        _insert_section_entry(lines, section, f"- [[{page_dir}/{slug}]]")
    summary_path.write_text("\n".join(lines), encoding="utf-8")


def _backlink_pages(
    wiki_dir: Path, doc_name: str, slugs: list[str], *, page_dir: str,
) -> None:
    """Append the source summary wikilink to each page under '## Related
    Documents'. Shared by the concept and entity page-backlink wrappers."""
    link = f"[[summaries/{doc_name}]]"
    pages_dir = wiki_dir / page_dir

    for slug in slugs:
        path = pages_dir / f"{slug}.md"
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if link in text:
            continue
        lines = text.split("\n")
        _ensure_h2_section(lines, "## Related Documents", quiet=True)
        _insert_section_entry(lines, "## Related Documents", f"- {link}")
        path.write_text("\n".join(lines), encoding="utf-8")


def _backlink_summary(wiki_dir: Path, doc_name: str, concept_slugs: list[str]) -> None:
    """Link the summary page back to every related concept (no LLM call)."""
    _backlink_summary_pages(
        wiki_dir, doc_name, concept_slugs,
        page_dir="concepts", section="## Related Concepts",
    )


def _backlink_concepts(wiki_dir: Path, doc_name: str, concept_slugs: list[str]) -> None:
    """Link every related concept page back to the source summary (no LLM call)."""
    _backlink_pages(wiki_dir, doc_name, concept_slugs, page_dir="concepts")


def _backlink_summary_entities(wiki_dir: Path, doc_name: str, entity_slugs: list[str]) -> None:
    """Link the summary page back to every related entity under '## Entities'."""
    _backlink_summary_pages(
        wiki_dir, doc_name, entity_slugs,
        page_dir="entities", section="## Entities",
    )


def _backlink_entities(wiki_dir: Path, doc_name: str, entity_slugs: list[str]) -> None:
    """Link every related entity page back to the source summary (no LLM call)."""
    _backlink_pages(wiki_dir, doc_name, entity_slugs, page_dir="entities")


def _remove_doc_from_pages(
    wiki_dir: Path,
    doc_name: str,
    *,
    page_dir: str,
    keep_empty: bool = False,
) -> dict[str, list[str]]:
    """Update or delete pages in ``page_dir`` affected by removing a document.

    For each ``{page_dir}/*.md`` whose frontmatter ``sources:`` lists
    ``summaries/{doc_name}``:

    - Remove that source from the frontmatter list.
    - Remove any ``- [[summaries/{doc_name}]]`` entries from the
      ``## Related Documents`` section.
    - Remove any standalone ``See also: [[summaries/{doc_name}]]`` lines
      (left by ``_add_related_link``).
    - If the ``sources:`` list becomes empty AND ``keep_empty`` is False,
      delete the page entirely.

    Shared by the concept and entity removal wrappers so the cleanup (in
    particular the standalone ``See also:`` strip) can never drift between
    the two page types.

    Returns ``{"modified": [slugs...], "deleted": [slugs...]}``.
    """
    pages_dir = wiki_dir / page_dir
    if not pages_dir.is_dir():
        return {"modified": [], "deleted": []}

    source_file = f"summaries/{doc_name}.md"
    bare_source = f"summaries/{doc_name}"
    link = f"[[{bare_source}]]"

    modified: list[str] = []
    deleted: list[str] = []

    for path in sorted(pages_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        # Cheap filter: skip pages that don't reference the doc at all.
        if source_file not in text and bare_source not in text:
            continue

        new_text, sources_empty = _remove_source_from_frontmatter(text, source_file)

        # Drop the doc's entry from the "## Related Documents" section.
        if link in new_text:
            lines = new_text.split("\n")
            while _remove_section_entry(lines, "## Related Documents", link):
                pass
            new_text = "\n".join(lines)

        # Drop standalone "See also: [[summaries/{doc_name}]]" lines.
        # The dominant form (written by ``_add_related_link``) is a
        # paragraph: preceded by a blank line and trailed by either a
        # newline or end-of-string. The first regex matches that shape
        # exactly, preserving one trailing newline so paragraph spacing
        # in surrounding content survives.
        new_text = re.sub(
            rf"\n\n[ \t]*See also:[ \t]*\[\[{re.escape(bare_source)}\]\][ \t]*(\n|\Z)",
            r"\1",
            new_text,
        )
        # Fallback for hand-edited inline "See also:" lines that lack the
        # paragraph-break separator above. Bounded to a single line via
        # `[ \t]` and an optional trailing newline.
        new_text = re.sub(
            rf"^[ \t]*See also:[ \t]*\[\[{re.escape(bare_source)}\]\][ \t]*\n?",
            "",
            new_text,
            flags=re.MULTILINE,
        )

        if sources_empty and not keep_empty:
            path.unlink()
            deleted.append(path.stem)
        elif new_text != text:
            path.write_text(new_text, encoding="utf-8")
            modified.append(path.stem)

    return {"modified": modified, "deleted": deleted}


def scan_affected_pages(pages_dir: Path, source_file_marker: str) -> list[tuple[str, int]]:
    """Return ``(slug, remaining_sources)`` for pages under ``pages_dir`` whose
    frontmatter ``sources:`` list contains ``source_file_marker``.

    Used by the ``openkb remove`` dry-run preview. Lives here, beside
    ``remove_doc_from_concept_pages`` / ``remove_doc_from_entity_pages`` and
    sharing ``_parse_yaml_list_value`` with them, so the preview and the
    executor can't drift apart on how the sources list is parsed (a hand-rolled
    comma-split here once kept the JSON quotes and matched nothing).
    """
    affected: list[tuple[str, int]] = []
    if not pages_dir.is_dir():
        return affected
    for path in sorted(pages_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            continue
        fm_end = text.find("---", 3)
        if fm_end == -1:
            continue
        for line in text[:fm_end].split("\n"):
            if line.lstrip().startswith("sources:"):
                items = _parse_yaml_list_value(line)
                if items is not None and source_file_marker in items:
                    affected.append((path.stem, max(len(items) - 1, 0)))
                break
    return affected


def remove_doc_from_concept_pages(
    wiki_dir: Path,
    doc_name: str,
    *,
    keep_empty: bool = False,
) -> dict[str, list[str]]:
    """Update or delete concept pages affected by removing a document.

    ``keep_empty`` retains concept pages whose only source was the removed
    doc (leaving ``sources: []``) — useful when the doc is being replaced by
    a newer version that will repopulate the source on the next ``openkb
    add``. Returns ``{"modified": [slugs...], "deleted": [slugs...]}``.
    """
    return _remove_doc_from_pages(
        wiki_dir, doc_name, page_dir="concepts", keep_empty=keep_empty,
    )


def remove_doc_from_entity_pages(
    wiki_dir: Path,
    doc_name: str,
    *,
    keep_empty: bool = False,
) -> dict[str, list[str]]:
    """Update or delete entity pages affected by removing a document.

    Mirrors ``remove_doc_from_concept_pages`` for the entities/ directory.
    Returns ``{"modified": [...], "deleted": [...]}``.
    """
    return _remove_doc_from_pages(
        wiki_dir, doc_name, page_dir="entities", keep_empty=keep_empty,
    )


def remove_doc_from_index(wiki_dir: Path, doc_name: str, concept_slugs_deleted: list[str],
                          entity_slugs_deleted: list[str] | None = None) -> None:
    """Remove the document's entry from ``index.md`` along with any concept
    and entity entries for pages that were deleted as a side effect.

    No-op when ``index.md`` doesn't exist. Section headings are kept even
    when their last entry is removed — adding a new doc later repopulates
    them.
    """
    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        return

    lines = index_path.read_text(encoding="utf-8").split("\n")

    doc_link = f"[[summaries/{doc_name}]]"
    while _remove_section_entry(lines, "## Documents", doc_link):
        pass

    for slug in concept_slugs_deleted:
        concept_link = f"[[concepts/{slug}]]"
        while _remove_section_entry(lines, "## Concepts", concept_link):
            pass

    for slug in (entity_slugs_deleted or []):
        entity_link = f"[[entities/{slug}]]"
        while _remove_section_entry(lines, "## Entities", entity_link):
            pass

    index_path.write_text("\n".join(lines), encoding="utf-8")


def _update_index(
    wiki_dir: Path, doc_name: str, concept_names: list[str],
    doc_brief: str = "", concept_briefs: dict[str, str] | None = None,
    doc_type: str = "short",
    entity_names: list[str] | None = None,
    entity_meta: dict[str, tuple[str, str]] | None = None,
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
        index_path.write_text(INDEX_SEED, encoding="utf-8")

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

    entity_names = entity_names or []
    entity_meta = entity_meta or {}
    if entity_names:
        # Keep canonical order: Entities sits before Explorations. On an older
        # index.md that predates the Entities section, plain ``_ensure_h2_section``
        # would append it after Explorations.
        _ensure_h2_section_before(lines, "## Entities", "## Explorations")
    for name in entity_names:
        link = f"[[entities/{name}]]"
        # Callers always populate entity_meta alongside entity_names; the
        # default is a defensive fallback, never hit in practice.
        etype, brief = entity_meta.get(name, ("other", ""))
        entry = f"- {link} ({etype})"
        if brief:
            entry += f" — {brief}"
        if _section_contains_link(lines, "## Entities", link):
            _replace_section_entry(lines, "## Entities", link, entry)
        else:
            _insert_section_entry(lines, "## Entities", entry)

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
    entity_types: list[str] | None = None,
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

    # Effective entity types for this compile (config-driven; defaults to the
    # canonical enum when unset, keeping behavior byte-identical to today).
    if entity_types is None:
        entity_types = list(_ENTITY_TYPE_LIST)
    types_str = ", ".join(entity_types)
    valid_types = frozenset(entity_types)

    # --- Step 2: Get concepts plan (A cached) ---
    concept_briefs = _read_concept_briefs(wiki_dir)
    entity_briefs = _read_entity_briefs(wiki_dir)

    # Second cache breakpoint: end of the assistant summary message. Covers
    # (system + doc + summary) for the plan call and every concept call.
    summary_msg = {"role": "assistant", "content": _cached_text(summary)}

    plan_raw = _llm_call(model, [
        system_msg,
        doc_msg,
        summary_msg,
        {"role": "user", "content": _CONCEPTS_PLAN_USER.format(
            concept_briefs=concept_briefs,
            entity_briefs=entity_briefs,
        ).replace("__ENTITY_TYPES__", types_str)},
    ], "concepts-plan", response_format=_JSON_RESPONSE_FORMAT)

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
        preview = plan_raw[:500] + ("..." if len(plan_raw) > 500 else "")
        logger.warning(
            "Failed to parse concepts plan: %s. Raw output (first 500 chars): %r",
            exc, preview,
        )
        logger.debug("Concepts plan raw output (full, %d chars): %s",
                     len(plan_raw), plan_raw)
        sys.stdout.write(
            f"    [WARN] concepts plan unparseable for {doc_name} — "
            f"no concept pages generated. See log (stderr) for details.\n"
        )
        sys.stdout.flush()
        if rewrite_summary:
            _write_v1_summary_stripped()
        _update_index(wiki_dir, doc_name, [], doc_brief=doc_brief, doc_type=doc_type)
        return

    # Fallback: if LLM returns a flat list, treat all items as "create".
    # The new plan contract nests concepts under a "concepts" key alongside
    # an "entities" key; the legacy flat shape (create/update/related at top
    # level) is still honored by falling back to ``parsed`` itself.
    if not isinstance(parsed, (list, dict)):
        # A JSON scalar (int/str/None/bool) is valid JSON but not a usable
        # plan. ``_parse_json`` normally rejects scalars, but guard here too
        # so ``parsed.get(...)`` can never raise AttributeError and abort the
        # compile — treat it as an empty/unparseable plan.
        logger.warning(
            "Concepts plan parsed to a %s scalar, not an object/array — "
            "treating as empty plan for %s.",
            type(parsed).__name__, doc_name,
        )
        if rewrite_summary:
            _write_v1_summary_stripped()
        _update_index(wiki_dir, doc_name, [], doc_brief=doc_brief, doc_type=doc_type)
        return

    if isinstance(parsed, list):
        plan = {"create": _filter_concept_items(parsed, "list"),
                "update": [], "related": []}
        entities_plan = {"create": [], "update": [], "related": []}
    else:
        concepts_group = (
            parsed.get("concepts")
            if isinstance(parsed.get("concepts"), dict)
            else parsed
        )
        plan = {
            "create": _filter_concept_items(concepts_group.get("create", []), "create"),
            "update": _filter_concept_items(concepts_group.get("update", []), "update"),
            "related": _filter_related_slugs(concepts_group.get("related", [])),
        }
        entities_plan = _parse_entities_plan(parsed, valid_types)

    create_items = plan["create"]
    update_items = plan["update"]
    related_items = plan["related"]
    entity_create = entities_plan["create"]
    entity_update = entities_plan["update"]
    entity_related = entities_plan["related"]

    # "related" must reference pages that ALREADY exist on disk (the plan
    # prompt asks for existing slugs). The LLM sometimes lists non-existent
    # slugs here; keeping them would whitelist [[concepts/...]] /
    # [[entities/...]] links as valid AND back-link them into the summary, yet
    # no page is ever created (related items are linked, never generated) —
    # producing a flood of dangling wikilinks. Drop the non-existent ones so
    # body references to them are stripped as ghosts instead.
    related_items = [
        s for s in related_items
        if (wiki_dir / "concepts" / f"{_sanitize_concept_name(s)}.md").exists()
    ]
    entity_related = [
        s for s in entity_related
        if (wiki_dir / "entities" / f"{_sanitize_concept_name(s)}.md").exists()
    ]

    # Distinguish "filters dropped everything" from "LLM emitted an empty plan".
    # Count entity items too, so a plan that emitted only entities — all of
    # which were dropped as malformed — still surfaces the warning.
    def _raw_group_count(group: object) -> int:
        if not isinstance(group, dict):
            return 0
        return sum(
            len(group.get(k, [])) if isinstance(group.get(k), list) else 0
            for k in ("create", "update", "related")
        )

    if isinstance(parsed, list):
        original_total = len(parsed)
    else:
        original_total = _raw_group_count(concepts_group) + _raw_group_count(parsed.get("entities"))
    post_filter_total = (
        len(create_items) + len(update_items) + len(related_items)
        + len(entity_create) + len(entity_update) + len(entity_related)
    )
    if original_total > 0 and post_filter_total == 0:
        sys.stdout.write(
            f"    [WARN] plan for {doc_name} had {original_total} "
            f"item(s), all dropped as malformed — see log (stderr).\n"
        )
        sys.stdout.flush()

    if (not create_items and not update_items and not related_items
            and not entity_create and not entity_update and not entity_related):
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
    entity_planned = {
        _sanitize_concept_name(e["name"]) for e in entity_create + entity_update
    } | {
        _sanitize_concept_name(s) for s in entity_related
    }
    known_targets: set[str] = (
        list_existing_wiki_targets(wiki_dir)
        | {f"concepts/{s}" for s in planned_slugs}
        | {f"entities/{s}" for s in entity_planned}
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
            ], f"concept: {name}", response_format=_JSON_RESPONSE_FORMAT)
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            # Parse succeeded: do NOT fall back to ``raw`` (the JSON string).
            # An empty/None ``content`` field yields "" so
            # ``_require_nonempty_content`` raises and the page is skipped,
            # rather than writing the raw JSON as the markdown body.
            content = parsed.get("content") or ""
        except (json.JSONDecodeError, ValueError):
            # Parse FAILED: ``raw`` is the legitimate non-JSON body fallback.
            brief, content = "", raw
        _require_nonempty_content(content, name)
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
            ], f"update: {name}", response_format=_JSON_RESPONSE_FORMAT)
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            # Parse succeeded: do NOT fall back to ``raw`` (the JSON string).
            content = parsed.get("content") or ""
        except (json.JSONDecodeError, ValueError):
            # Parse FAILED: ``raw`` is the legitimate non-JSON body fallback.
            brief, content = "", raw
        _require_nonempty_content(content, name)
        return name, content, True, brief

    async def _gen_entity_create(ent: dict) -> tuple[str, str, str, str]:
        name = ent["name"]
        title = ent.get("title", name)
        etype = ent.get("type", "other")
        async with semaphore:
            raw = await _llm_call_async(model, [
                system_msg,
                doc_msg,             # cached (BP1)
                summary_msg,         # cached (BP2)
                known_targets_msg,   # cached (BP3) — whitelist
                {"role": "user", "content": _ENTITY_PAGE_USER.format(
                    title=title, type=etype, doc_name=doc_name,
                ).replace("__ENTITY_TYPES__", types_str)},
            ], f"entity: {name}", response_format=_JSON_RESPONSE_FORMAT)
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            etype_out = parsed.get("type") if parsed.get("type") in valid_types else etype
            # Parse succeeded: do NOT fall back to ``raw`` (the JSON string).
            content = parsed.get("content") or ""
        except (json.JSONDecodeError, ValueError):
            # Parse FAILED: ``raw`` is the legitimate non-JSON body fallback.
            brief, etype_out, content = "", etype, raw
        _require_nonempty_content(content, name)
        return name, content, brief, etype_out

    async def _gen_entity_update(ent: dict) -> tuple[str, str, str, str]:
        name = ent["name"]
        title = ent.get("title", name)
        etype = ent.get("type", "other")
        epath = wiki_dir / "entities" / f"{_sanitize_concept_name(name)}.md"
        if epath.exists():
            raw_text = epath.read_text(encoding="utf-8")
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
                {"role": "user", "content": _ENTITY_UPDATE_USER.format(
                    title=title, type=etype, doc_name=doc_name,
                    existing_content=existing_content,
                ).replace("__ENTITY_TYPES__", types_str)},
            ], f"entity-update: {name}", response_format=_JSON_RESPONSE_FORMAT)
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            etype_out = parsed.get("type") if parsed.get("type") in valid_types else etype
            # Parse succeeded: do NOT fall back to ``raw`` (the JSON string).
            content = parsed.get("content") or ""
        except (json.JSONDecodeError, ValueError):
            # Parse FAILED: ``raw`` is the legitimate non-JSON body fallback.
            brief, etype_out, content = "", etype, raw
        _require_nonempty_content(content, name)
        return name, content, brief, etype_out

    tasks = []
    tasks.extend(_gen_create(c) for c in create_items)
    tasks.extend(_gen_update(c) for c in update_items)

    # --- Step 3 (entities): build the entity task list up front so it can be
    # gathered concurrently with the concept tasks below. Entity coroutines
    # return 4-arity tuples (name, content, brief, type), so their results are
    # processed in their own loop rather than mixed with the concept tuples.
    entity_tasks = []
    entity_tasks.extend(_gen_entity_create(e) for e in entity_create)
    entity_tasks.extend(_gen_entity_update(e) for e in entity_update)

    concept_names: list[str] = []
    concept_briefs_map: dict[str, str] = {}
    pending_writes: list[tuple[str, str, bool, str]] = []
    entity_names: list[str] = []
    entity_meta: dict[str, tuple[str, str]] = {}
    entity_pending: list[tuple[str, str, str, str]] = []

    # Concepts and entities are independent and share the cached prompt
    # context + the same concurrency ``semaphore``, so overlap them in one
    # outer gather instead of running entities only after concepts finish.
    total = len(tasks)
    etotal = len(entity_tasks)
    if tasks:
        sys.stdout.write(f"    Generating {total} concept(s) (concurrency={max_concurrency})...\n")
        sys.stdout.flush()
    if entity_tasks:
        sys.stdout.write(
            f"    Generating {etotal} entity(ies) (concurrency={max_concurrency})...\n"
        )
        sys.stdout.flush()

    results, entity_results = ([], [])
    if tasks or entity_tasks:
        results, entity_results = await asyncio.gather(
            asyncio.gather(*tasks, return_exceptions=True),
            asyncio.gather(*entity_tasks, return_exceptions=True),
        )

    if tasks:
        failure_types: list[str] = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Concept generation failed: %s", r)
                failure_types.append(type(r).__name__)
                continue
            name, page_content, is_update, brief = r
            pending_writes.append((name, page_content, is_update, brief))
            safe_name = _sanitize_concept_name(name)
            concept_names.append(safe_name)
            if brief:
                concept_briefs_map[safe_name] = brief

        # Include exception type names inline so the stdout line is
        # self-contained — per-failure WARNINGs go to stderr.
        written = len(pending_writes)
        if written < total:
            reason = (
                ", ".join(sorted(set(failure_types)))
                if failure_types else "see log (stderr)"
            )
            sys.stdout.write(
                f"    [WARN] {total} concept(s) planned but only {written} written "
                f"for {doc_name} ({reason}).\n"
            )
            sys.stdout.flush()

    if entity_tasks:
        entity_failure_types: list[str] = []
        for r in entity_results:
            if isinstance(r, Exception):
                logger.warning("Entity generation failed: %s", r)
                entity_failure_types.append(type(r).__name__)
                continue
            name, page_content, brief, etype = r
            entity_pending.append((name, page_content, brief, etype))

        ewritten = len(entity_pending)
        if ewritten < etotal:
            reason = (
                ", ".join(sorted(set(entity_failure_types)))
                if entity_failure_types else "see log (stderr)"
            )
            sys.stdout.write(
                f"    [WARN] {etotal} entity(ies) planned but only {ewritten} written "
                f"for {doc_name} ({reason}).\n"
            )
            sys.stdout.flush()

    # Strip ghost wikilinks from entity bodies and write each page.
    for name, page_content, brief, etype in entity_pending:
        cleaned, ghosts = strip_ghost_wikilinks(page_content, known_targets)
        if ghosts:
            logger.info(
                "stripped %d ghost wikilink(s) from entity %s: %s",
                len(ghosts), name, ghosts[:5],
            )
        safe = _sanitize_concept_name(name)
        is_update = (wiki_dir / "entities" / f"{safe}.md").exists()
        _write_entity(wiki_dir, name, cleaned, source_file, is_update,
                      brief=brief, type_=etype)
        entity_names.append(safe)
        entity_meta[safe] = (etype, brief)

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

    # --- Step 3d: Process entity related items + backlinks (code only) ---
    # Reuse _add_related_link (page_dir="entities") so related-entity
    # cross-refs are written in the same "See also:" form the concept path
    # uses — and torn down symmetrically by _remove_doc_from_pages.
    entity_related_slugs = [
        slug for slug in (_sanitize_concept_name(s) for s in entity_related)
        if _add_related_link(wiki_dir, slug, doc_name, source_file, page_dir="entities")
    ]

    entity_backlink_slugs = entity_names + entity_related_slugs
    if entity_backlink_slugs:
        _backlink_summary_entities(wiki_dir, doc_name, entity_backlink_slugs)
        _backlink_entities(wiki_dir, doc_name, entity_backlink_slugs)

    # --- Step 4: Update index (code only) ---
    _update_index(wiki_dir, doc_name, concept_names,
                  doc_brief=doc_brief, concept_briefs=concept_briefs_map,
                  doc_type=doc_type, entity_names=entity_names,
                  entity_meta=entity_meta)


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
    entity_types = resolve_entity_types(config)

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
    summary_raw = _llm_call(model, [system_msg, doc_msg], "summary",
                             response_format=_JSON_RESPONSE_FORMAT)
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
        doc_type="short", rewrite_summary=True, entity_types=entity_types,
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
    entity_types = resolve_entity_types(config)

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
        doc_type="pageindex", entity_types=entity_types,
    )
