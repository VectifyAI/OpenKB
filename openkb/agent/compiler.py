"""Wiki compilation pipeline for OpenKB.

Pipeline leveraging LLM prompt caching:
  Step 1: Build base context A (schema + document content).
  Step 2: A → generate summary.
  Step 3: A + summary → extract concept list.
  Step 4: Concurrent LLM calls (A cached) → generate each concept page.
  Step 5: Code writes all files, updates index, appends log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import litellm

from openkb.schema import get_agents_md

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """\
You are a wiki compilation agent for a personal knowledge base.

{schema_md}

Write all content in {language} language.
Use [[wikilinks]] to connect related pages (e.g. [[concepts/attention]]).
"""

_SUMMARY_USER = """\
New document: {doc_name}

Full text:
{content}

Write a summary page for this document in Markdown. Include:
- Key concepts, findings, and ideas
- [[wikilinks]] to concepts that could become cross-document concept pages

Return ONLY the Markdown content (no frontmatter, no code fences).
"""

_CONCEPTS_LIST_USER = """\
Based on the summary above, identify the key concepts worth creating as \
standalone wiki concept pages.

Existing concept pages: {existing_concepts}

Return a JSON array of objects, each with:
- "name": concept slug (e.g. "transformer-architecture")
- "title": human-readable title (e.g. "Transformer Architecture")
- "is_update": true if this concept already exists and should be updated

Only include concepts for significant themes. For the first document, \
create 2-3 foundational concepts at most. Do NOT create concepts that are \
just the document topic itself (e.g. don't create "machine-translation" \
for a translation paper).

Return ONLY valid JSON array, no fences, no explanation.
"""

_CONCEPT_PAGE_USER = """\
Write the concept page for: {title}

This concept relates to the document "{doc_name}" summarized above.
{update_instruction}

Return ONLY the Markdown content (no frontmatter, no code fences). Include:
- Clear explanation of the concept
- Key details from the source document
- [[wikilinks]] to related concepts and [[summaries/{doc_name}]]
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

import threading


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
    """Format messages for debug output, truncating long content."""
    parts = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if len(content) > max_content:
            preview = content[:max_content] + f"... ({len(content)} chars)"
        else:
            preview = content
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


async def _llm_call_async(model: str, messages: list[dict], step_name: str) -> str:
    """Async LLM call with timing output and debug logging."""
    logger.debug("LLM request [%s]:\n%s", step_name, _fmt_messages(messages))

    t0 = time.time()

    response = await litellm.acompletion(model=model, messages=messages)
    content = response.choices[0].message.content or ""

    elapsed = time.time() - t0
    sys.stdout.write(f"    {step_name}... {_format_usage(elapsed, response.usage)}\n")
    sys.stdout.flush()
    logger.debug("LLM response [%s]:\n%s", step_name, content[:500] + ("..." if len(content) > 500 else ""))
    return content.strip()


def _parse_json(text: str) -> list | dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.index("\n")
        cleaned = cleaned[first_nl + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    return json.loads(cleaned.strip())


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


def _find_source_filename(doc_name: str, kb_dir: Path) -> str:
    """Find the original filename in raw/ for a given doc stem."""
    raw_dir = kb_dir / "raw"
    if raw_dir.exists():
        for f in raw_dir.iterdir():
            if f.stem == doc_name:
                return f.name
    return f"{doc_name}.pdf"


def _write_summary(wiki_dir: Path, doc_name: str, source_file: str, summary: str) -> None:
    """Write summary page with frontmatter."""
    summaries_dir = wiki_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = f"---\nsources: [{source_file}]\n---\n\n"
    (summaries_dir / f"{doc_name}.md").write_text(frontmatter + summary, encoding="utf-8")


def _write_concept(wiki_dir: Path, name: str, content: str, source_file: str, is_update: bool) -> None:
    """Write or update a concept page, managing the sources frontmatter."""
    concepts_dir = wiki_dir / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    path = concepts_dir / f"{name}.md"

    if is_update and path.exists():
        existing = path.read_text(encoding="utf-8")
        if source_file not in existing:
            if existing.startswith("---"):
                end = existing.index("---", 3)
                fm = existing[:end + 3]
                body = existing[end + 3:]
                if "sources:" in fm:
                    fm = fm.replace("sources: [", f"sources: [{source_file}, ")
                existing = fm + body
            existing += f"\n\n{content}"
        path.write_text(existing, encoding="utf-8")
    else:
        frontmatter = f"---\nsources: [{source_file}]\n---\n\n"
        path.write_text(frontmatter + content, encoding="utf-8")


def _update_index(wiki_dir: Path, doc_name: str, concept_names: list[str]) -> None:
    """Append document and concept entries to index.md."""
    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        return

    text = index_path.read_text(encoding="utf-8")

    doc_entry = f"- [[summaries/{doc_name}]]"
    if doc_entry not in text:
        if "## Documents" in text:
            text = text.replace("## Documents\n", f"## Documents\n{doc_entry}\n", 1)

    for name in concept_names:
        concept_entry = f"- [[concepts/{name}]]"
        if concept_entry not in text:
            if "## Concepts" in text:
                text = text.replace("## Concepts\n", f"## Concepts\n{concept_entry}\n", 1)

    index_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

DEFAULT_COMPILE_CONCURRENCY = 5


async def compile_short_doc(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    model: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a short document using a multi-step LLM pipeline with caching.

    Step 1: Build base context A (schema + doc content).
    Step 2: A → generate summary.
    Step 3: A + summary → extract concept list.
    Step 4: Concurrent LLM calls (A cached) → generate each concept page.
    Step 5: Code writes files, updates index.
    """
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_dir = kb_dir / "wiki"
    schema_md = get_agents_md(wiki_dir)
    source_file = _find_source_filename(doc_name, kb_dir)
    content = source_path.read_text(encoding="utf-8")

    # Base context A: system + document
    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _SUMMARY_USER.format(
        doc_name=doc_name, content=content,
    )}

    # --- Step 1: Generate summary ---
    summary = _llm_call(model, [system_msg, doc_msg], "summary")
    _write_summary(wiki_dir, doc_name, source_file, summary)

    # --- Step 2: Extract concept list (A cached) ---
    _, existing_concepts = _read_wiki_context(wiki_dir)

    concepts_list_raw = _llm_call(model, [
        system_msg,
        doc_msg,
        {"role": "assistant", "content": summary},
        {"role": "user", "content": _CONCEPTS_LIST_USER.format(
            existing_concepts=", ".join(existing_concepts) if existing_concepts else "(none yet)",
        )},
    ], "concepts-list", max_tokens=512)

    try:
        concepts_list = _parse_json(concepts_list_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse concepts list: %s", exc)
        logger.debug("Raw: %s", concepts_list_raw)
        _update_index(wiki_dir, doc_name, [])
        return

    if not concepts_list:
        _update_index(wiki_dir, doc_name, [])
        return

    # --- Step 3: Generate concept pages concurrently (A cached) ---
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _gen_concept(concept: dict) -> tuple[str, str, bool]:
        name = concept["name"]
        title = concept.get("title", name)
        is_update = concept.get("is_update", False)
        update_instruction = (
            "This concept page already exists. Add new information from this document "
            "without duplicating existing content."
            if is_update else ""
        )

        async with semaphore:
            page_content = await _llm_call_async(model, [
                system_msg,
                doc_msg,
                {"role": "assistant", "content": summary},
                {"role": "user", "content": _CONCEPT_PAGE_USER.format(
                    title=title, doc_name=doc_name,
                    update_instruction=update_instruction,
                )},
            ], f"concept:{name}")

        return name, page_content, is_update

    sys.stdout.write(f"    Generating {len(concepts_list)} concept(s) (concurrency={max_concurrency})...\n")
    sys.stdout.flush()

    results = await asyncio.gather(
        *[_gen_concept(c) for c in concepts_list],
        return_exceptions=True,
    )

    concept_names = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Concept generation failed: %s", r)
            continue
        name, page_content, is_update = r
        _write_concept(wiki_dir, name, page_content, source_file, is_update)
        concept_names.append(name)

    # --- Step 4: Update index (code only) ---
    _update_index(wiki_dir, doc_name, concept_names)


async def compile_long_doc(
    doc_name: str,
    summary_path: Path,
    doc_id: str,
    kb_dir: Path,
    model: str,
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
    source_file = _find_source_filename(doc_name, kb_dir)
    summary = summary_path.read_text(encoding="utf-8")

    # Base context A
    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _LONG_DOC_SUMMARY_USER.format(
        doc_name=doc_name, doc_id=doc_id, content=summary,
    )}

    # --- Step 1: Extract concept list ---
    _, existing_concepts = _read_wiki_context(wiki_dir)

    # Get a concise overview first (for concept generation context)
    overview = _llm_call(model, [system_msg, doc_msg], "overview")

    concepts_list_raw = _llm_call(model, [
        system_msg,
        doc_msg,
        {"role": "assistant", "content": overview},
        {"role": "user", "content": _CONCEPTS_LIST_USER.format(
            existing_concepts=", ".join(existing_concepts) if existing_concepts else "(none yet)",
        )},
    ], "concepts-list", max_tokens=512)

    try:
        concepts_list = _parse_json(concepts_list_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse concepts list: %s", exc)
        logger.debug("Raw: %s", concepts_list_raw)
        _update_index(wiki_dir, doc_name, [])
        return

    if not concepts_list:
        _update_index(wiki_dir, doc_name, [])
        return

    # --- Step 2: Generate concept pages concurrently ---
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _gen_concept(concept: dict) -> tuple[str, str, bool]:
        name = concept["name"]
        title = concept.get("title", name)
        is_update = concept.get("is_update", False)
        update_instruction = (
            "This concept page already exists. Add new information."
            if is_update else ""
        )

        async with semaphore:
            page_content = await _llm_call_async(model, [
                system_msg,
                doc_msg,
                {"role": "assistant", "content": overview},
                {"role": "user", "content": _CONCEPT_PAGE_USER.format(
                    title=title, doc_name=doc_name,
                    update_instruction=update_instruction,
                )},
            ], f"concept:{name}")

        return name, page_content, is_update

    sys.stdout.write(f"    Generating {len(concepts_list)} concept(s) (concurrency={max_concurrency})...\n")
    sys.stdout.flush()

    results = await asyncio.gather(
        *[_gen_concept(c) for c in concepts_list],
        return_exceptions=True,
    )

    concept_names = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Concept generation failed: %s", r)
            continue
        name, page_content, is_update = r
        _write_concept(wiki_dir, name, page_content, source_file, is_update)
        concept_names.append(name)

    # --- Step 3: Update index (code only) ---
    _update_index(wiki_dir, doc_name, concept_names)
