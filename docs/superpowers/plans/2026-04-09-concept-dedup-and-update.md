# Concept Dedup & Existing Page Update — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the compiler enough context about existing concepts to make smart dedup/update decisions, and add the ability to rewrite existing concept pages with new information — all without breaking prompt caching.

**Architecture:** Extend the deterministic pipeline in `compiler.py` with: (1) concept briefs read from disk before the concepts-plan LLM call, (2) a new JSON output format with create/update/related actions, (3) a new concurrent "update" path that sends existing page content to the LLM for rewriting, (4) a code-only "related" path for cross-ref links. Extract shared logic between `compile_short_doc` and `compile_long_doc` into `_compile_concepts`.

**Tech Stack:** Python, litellm, asyncio, pytest

---

### Task 1: Add `_read_concept_briefs` and test

**Files:**
- Modify: `openkb/agent/compiler.py:199-207` (File I/O helpers section)
- Modify: `tests/test_compiler.py:98-116` (TestReadWikiContext section)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compiler.py`:

```python
from openkb.agent.compiler import _read_concept_briefs

class TestReadConceptBriefs:
    def test_empty_wiki(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        assert _read_concept_briefs(wiki) == "(none yet)"

    def test_no_concepts_dir(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        assert _read_concept_briefs(wiki) == "(none yet)"

    def test_reads_briefs_with_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\nAttention allows models to focus on relevant input parts selectively.",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- attention: Attention allows models" in result

    def test_reads_briefs_without_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "rnn.md").write_text(
            "Recurrent neural networks process sequences step by step.",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- rnn: Recurrent neural networks" in result

    def test_truncates_long_content(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "long.md").write_text("A" * 300, encoding="utf-8")
        result = _read_concept_briefs(wiki)
        brief_line = result.split("\n")[0]
        # slug + ": " + 150 chars = well under 200
        assert len(brief_line) < 200

    def test_sorted_alphabetically(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "zebra.md").write_text("Zebra concept.", encoding="utf-8")
        (concepts / "alpha.md").write_text("Alpha concept.", encoding="utf-8")
        result = _read_concept_briefs(wiki)
        lines = result.strip().split("\n")
        assert lines[0].startswith("- alpha:")
        assert lines[1].startswith("- zebra:")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compiler.py::TestReadConceptBriefs -v`
Expected: FAIL with `ImportError: cannot import name '_read_concept_briefs'`

- [ ] **Step 3: Implement `_read_concept_briefs`**

Add to `openkb/agent/compiler.py` in the File I/O helpers section (after `_read_wiki_context`):

```python
def _read_concept_briefs(wiki_dir: Path) -> str:
    """Read existing concept pages and return compact briefs for the LLM.

    Returns a string like:
        - attention: Attention allows models to focus on relevant input parts...
        - transformer: The Transformer is a neural network architecture...

    Or "(none yet)" if no concept pages exist.
    """
    concepts_dir = wiki_dir / "concepts"
    if not concepts_dir.exists():
        return "(none yet)"
    briefs = []
    for p in sorted(concepts_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        # Skip YAML frontmatter
        if text.startswith("---"):
            parts = text.split("---", 2)
            body = parts[2].strip() if len(parts) >= 3 else ""
        else:
            body = text.strip()
        brief = body[:150].replace("\n", " ")
        if brief:
            briefs.append(f"- {p.stem}: {brief}")
    return "\n".join(briefs) or "(none yet)"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_compiler.py::TestReadConceptBriefs -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Update the import in test file**

Add `_read_concept_briefs` to the existing import block at the top of `tests/test_compiler.py`:

```python
from openkb.agent.compiler import (
    compile_long_doc,
    compile_short_doc,
    _parse_json,
    _write_summary,
    _write_concept,
    _update_index,
    _read_wiki_context,
    _read_concept_briefs,
)
```

- [ ] **Step 6: Commit**

```bash
git add openkb/agent/compiler.py tests/test_compiler.py
git commit -m "feat: add _read_concept_briefs for concept dedup context"
```

---

### Task 2: Replace prompt template and update JSON parsing

**Files:**
- Modify: `openkb/agent/compiler.py:53-70` (prompt templates section)
- Modify: `tests/test_compiler.py:21-31` (TestParseJson section)

- [ ] **Step 1: Write the failing test for new JSON format**

Add to `tests/test_compiler.py`:

```python
class TestParseConceptsPlan:
    def test_dict_format(self):
        text = json.dumps({
            "create": [{"name": "foo", "title": "Foo"}],
            "update": [{"name": "bar", "title": "Bar"}],
            "related": ["baz"],
        })
        parsed = _parse_json(text)
        assert isinstance(parsed, dict)
        assert len(parsed["create"]) == 1
        assert len(parsed["update"]) == 1
        assert parsed["related"] == ["baz"]

    def test_fallback_list_format(self):
        """If LLM returns old flat array, _parse_json still works."""
        text = json.dumps([{"name": "foo", "title": "Foo"}])
        parsed = _parse_json(text)
        assert isinstance(parsed, list)

    def test_fenced_dict(self):
        text = '```json\n{"create": [], "update": [], "related": []}\n```'
        parsed = _parse_json(text)
        assert isinstance(parsed, dict)
        assert parsed["create"] == []
```

- [ ] **Step 2: Run test to verify it passes (these use existing `_parse_json`)**

Run: `pytest tests/test_compiler.py::TestParseConceptsPlan -v`
Expected: All 3 PASS — `_parse_json` already handles dicts. This confirms compatibility.

- [ ] **Step 3: Replace `_CONCEPTS_LIST_USER` with `_CONCEPTS_PLAN_USER`**

In `openkb/agent/compiler.py`, replace the `_CONCEPTS_LIST_USER` template (lines 53-70) with:

```python
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
```

- [ ] **Step 4: Add `_CONCEPT_UPDATE_USER` template**

Add after `_CONCEPT_PAGE_USER` (after line 82):

```python
_CONCEPT_UPDATE_USER = """\
Update the concept page for: {title}

Current content of this page:
{existing_content}

New information from document "{doc_name}" (summarized above) should be \
integrated into this page. Rewrite the full page incorporating the new \
information naturally — do not just append. Maintain existing \
[[wikilinks]] and add new ones where appropriate.

Return ONLY the Markdown content (no frontmatter, no code fences).
"""
```

- [ ] **Step 5: Run all existing tests to verify nothing breaks**

Run: `pytest tests/test_compiler.py -v`
Expected: All PASS (templates aren't tested directly, only via integration tests which we'll update later)

- [ ] **Step 6: Commit**

```bash
git add openkb/agent/compiler.py tests/test_compiler.py
git commit -m "feat: add concepts plan and update prompt templates"
```

---

### Task 3: Add `_add_related_link` and test

**Files:**
- Modify: `openkb/agent/compiler.py` (File I/O helpers section, after `_write_concept`)
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_compiler.py`:

```python
from openkb.agent.compiler import _add_related_link

class TestAddRelatedLink:
    def test_adds_see_also_link(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\n---\n\n# Attention\n\nSome content.",
            encoding="utf-8",
        )
        _add_related_link(wiki, "attention", "new-doc", "paper2.pdf")
        text = (concepts / "attention.md").read_text()
        assert "[[summaries/new-doc]]" in text
        assert "paper2.pdf" in text

    def test_skips_if_already_linked(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\n---\n\n# Attention\n\nSee also: [[summaries/new-doc]]",
            encoding="utf-8",
        )
        _add_related_link(wiki, "attention", "new-doc", "paper1.pdf")
        text = (concepts / "attention.md").read_text()
        # Should not duplicate
        assert text.count("[[summaries/new-doc]]") == 1

    def test_skips_if_file_missing(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        # Should not raise
        _add_related_link(wiki, "nonexistent", "doc", "file.pdf")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compiler.py::TestAddRelatedLink -v`
Expected: FAIL with `ImportError: cannot import name '_add_related_link'`

- [ ] **Step 3: Implement `_add_related_link`**

Add to `openkb/agent/compiler.py` after `_write_concept`:

```python
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

    # Update sources in frontmatter
    if source_file not in text:
        if text.startswith("---"):
            end = text.index("---", 3)
            fm = text[:end + 3]
            body = text[end + 3:]
            if "sources:" in fm:
                fm = fm.replace("sources: [", f"sources: [{source_file}, ")
            else:
                fm = fm.replace("---\n", f"---\nsources: [{source_file}]\n", 1)
            text = fm + body
        else:
            text = f"---\nsources: [{source_file}]\n---\n\n" + text

    text += f"\n\nSee also: {link}"
    path.write_text(text, encoding="utf-8")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_compiler.py::TestAddRelatedLink -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Update the import in test file**

Add `_add_related_link` to the import block at top of `tests/test_compiler.py`.

- [ ] **Step 6: Commit**

```bash
git add openkb/agent/compiler.py tests/test_compiler.py
git commit -m "feat: add _add_related_link for code-only cross-referencing"
```

---

### Task 4: Extract `_compile_concepts` and refactor both public functions

**Files:**
- Modify: `openkb/agent/compiler.py:290-509` (Public API section — full rewrite)
- Modify: `tests/test_compiler.py:153-267` (integration tests)

This is the core task. It extracts the shared Steps 2-4 into `_compile_concepts`, updates both public functions to call it, and switches to the new concepts plan format.

- [ ] **Step 1: Write integration test for new create/update/related flow**

Add to `tests/test_compiler.py`:

```python
class TestCompileConceptsPlan:
    """Integration tests for the new create/update/related flow."""

    @pytest.mark.asyncio
    async def test_create_and_update_flow(self, tmp_path):
        """New doc creates one concept and updates an existing one."""
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        concepts_dir = wiki / "concepts"
        concepts_dir.mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        # Pre-existing concept
        (concepts_dir / "attention.md").write_text(
            "---\nsources: [old-paper.pdf]\n---\n\n# Attention\n\nOld content about attention.",
            encoding="utf-8",
        )

        source_path = wiki / "sources" / "new-paper.md"
        source_path.write_text("# New Paper\n\nContent about flash attention and transformers.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "new-paper.pdf").write_bytes(b"fake")

        summary_resp = "This paper introduces flash attention, improving on attention mechanisms."
        plan_resp = json.dumps({
            "create": [{"name": "flash-attention", "title": "Flash Attention"}],
            "update": [{"name": "attention", "title": "Attention Mechanism"}],
            "related": [],
        })
        create_page_resp = "# Flash Attention\n\nAn efficient attention algorithm."
        update_page_resp = "# Attention\n\nUpdated content with flash attention details."

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_resp, plan_resp])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([create_page_resp, update_page_resp])
            )
            await compile_short_doc("new-paper", source_path, tmp_path, "gpt-4o-mini")

        # New concept created
        flash_path = concepts_dir / "flash-attention.md"
        assert flash_path.exists()
        assert "sources: [new-paper.pdf]" in flash_path.read_text()

        # Existing concept rewritten (not appended)
        attn_text = (concepts_dir / "attention.md").read_text()
        assert "new-paper.pdf" in attn_text
        assert "Updated content with flash attention details" in attn_text

        # Index updated for both
        index_text = (wiki / "index.md").read_text()
        assert "[[concepts/flash-attention]]" in index_text

    @pytest.mark.asyncio
    async def test_related_adds_link_no_llm(self, tmp_path):
        """Related concepts get cross-ref links without LLM calls."""
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        concepts_dir = wiki / "concepts"
        concepts_dir.mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        (concepts_dir / "transformer.md").write_text(
            "---\nsources: [old.pdf]\n---\n\n# Transformer\n\nArchitecture details.",
            encoding="utf-8",
        )

        source_path = wiki / "sources" / "doc.md"
        source_path.write_text("Content", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "doc.pdf").write_bytes(b"fake")

        summary_resp = "A short summary."
        plan_resp = json.dumps({
            "create": [],
            "update": [],
            "related": ["transformer"],
        })

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_resp, plan_resp])
            )
            # acompletion should NOT be called (no create/update)
            mock_litellm.acompletion = AsyncMock(side_effect=AssertionError("should not be called"))
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        # Related concept should have cross-ref link
        transformer_text = (concepts_dir / "transformer.md").read_text()
        assert "[[summaries/doc]]" in transformer_text

    @pytest.mark.asyncio
    async def test_fallback_list_format(self, tmp_path):
        """If LLM returns old flat array, treat all as create."""
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "doc.md"
        source_path.write_text("Content", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "doc.pdf").write_bytes(b"fake")

        summary_resp = "Summary."
        # Old format: flat array
        plan_resp = json.dumps([{"name": "foo", "title": "Foo"}])
        page_resp = "# Foo\n\nContent."

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_resp, plan_resp])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([page_resp])
            )
            await compile_short_doc("doc", source_path, tmp_path, "gpt-4o-mini")

        assert (wiki / "concepts" / "foo.md").exists()
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_compiler.py::TestCompileConceptsPlan -v`
Expected: FAIL — the current code uses old prompt format and doesn't handle dict responses

- [ ] **Step 3: Implement `_compile_concepts` and refactor public functions**

Replace the entire Public API section (from `DEFAULT_COMPILE_CONCURRENCY` to end of file) in `openkb/agent/compiler.py` with:

```python
DEFAULT_COMPILE_CONCURRENCY = 5


async def _compile_concepts(
    wiki_dir: Path,
    kb_dir: Path,
    model: str,
    system_msg: dict,
    doc_msg: dict,
    summary: str,
    doc_name: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Shared concept compilation logic: plan → create/update/related → index.

    This is the core of the compilation pipeline, shared by both
    compile_short_doc and compile_long_doc.
    """
    source_file = _find_source_filename(doc_name, kb_dir)
    concept_briefs = _read_concept_briefs(wiki_dir)

    # --- Concepts plan (A cached) ---
    plan_raw = _llm_call(model, [
        system_msg,
        doc_msg,
        {"role": "assistant", "content": summary},
        {"role": "user", "content": _CONCEPTS_PLAN_USER.format(
            concept_briefs=concept_briefs,
        )},
    ], "concepts-plan", max_tokens=1024)

    try:
        parsed = _parse_json(plan_raw)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse concepts plan: %s", exc)
        logger.debug("Raw: %s", plan_raw)
        _update_index(wiki_dir, doc_name, [])
        return

    # Fallback: if LLM returns flat array, treat all as create
    if isinstance(parsed, list):
        create_list, update_list, related_list = parsed, [], []
    else:
        create_list = parsed.get("create", [])
        update_list = parsed.get("update", [])
        related_list = parsed.get("related", [])

    if not create_list and not update_list and not related_list:
        _update_index(wiki_dir, doc_name, [])
        return

    # --- Concurrent concept generation (A cached) ---
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _gen_create(concept: dict) -> tuple[str, str, bool]:
        name = concept["name"]
        title = concept.get("title", name)
        async with semaphore:
            page_content = await _llm_call_async(model, [
                system_msg,
                doc_msg,
                {"role": "assistant", "content": summary},
                {"role": "user", "content": _CONCEPT_PAGE_USER.format(
                    title=title, doc_name=doc_name,
                    update_instruction="",
                )},
            ], f"create:{name}")
        return name, page_content, False

    async def _gen_update(concept: dict) -> tuple[str, str, bool]:
        name = concept["name"]
        title = concept.get("title", name)
        # Read existing page content for the LLM to integrate
        concept_path = wiki_dir / "concepts" / f"{name}.md"
        if concept_path.exists():
            raw_text = concept_path.read_text(encoding="utf-8")
            # Strip frontmatter for the LLM
            if raw_text.startswith("---"):
                parts = raw_text.split("---", 2)
                existing_content = parts[2].strip() if len(parts) >= 3 else raw_text
            else:
                existing_content = raw_text
        else:
            existing_content = "(page not found — create from scratch)"
        async with semaphore:
            page_content = await _llm_call_async(model, [
                system_msg,
                doc_msg,
                {"role": "assistant", "content": summary},
                {"role": "user", "content": _CONCEPT_UPDATE_USER.format(
                    title=title, doc_name=doc_name,
                    existing_content=existing_content,
                )},
            ], f"update:{name}")
        return name, page_content, True

    tasks = []
    tasks.extend(_gen_create(c) for c in create_list)
    tasks.extend(_gen_update(c) for c in update_list)

    if tasks:
        total = len(tasks)
        sys.stdout.write(f"    Generating {total} concept(s) (concurrency={max_concurrency})...\n")
        sys.stdout.flush()

        results = await asyncio.gather(*tasks, return_exceptions=True)
    else:
        results = []

    concept_names = []
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Concept generation failed: %s", r)
            continue
        name, page_content, is_update = r
        _write_concept(wiki_dir, name, page_content, source_file, is_update)
        concept_names.append(name)

    # --- Related: code-only cross-ref links ---
    for slug in related_list:
        _add_related_link(wiki_dir, slug, doc_name, source_file)

    # --- Update index ---
    _update_index(wiki_dir, doc_name, concept_names)


async def compile_short_doc(
    doc_name: str,
    source_path: Path,
    kb_dir: Path,
    model: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a short document into wiki pages.

    Step 1: Generate summary from full document text.
    Step 2: Plan + generate/update concept pages (via _compile_concepts).
    """
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_dir = kb_dir / "wiki"
    schema_md = get_agents_md(wiki_dir)
    source_file = _find_source_filename(doc_name, kb_dir)
    content = source_path.read_text(encoding="utf-8")

    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _SUMMARY_USER.format(
        doc_name=doc_name, content=content,
    )}

    # Step 1: Generate summary
    summary = _llm_call(model, [system_msg, doc_msg], "summary")
    _write_summary(wiki_dir, doc_name, source_file, summary)

    # Step 2: Compile concepts
    await _compile_concepts(
        wiki_dir, kb_dir, model, system_msg, doc_msg, summary,
        doc_name, max_concurrency,
    )


async def compile_long_doc(
    doc_name: str,
    summary_path: Path,
    doc_id: str,
    kb_dir: Path,
    model: str,
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
    """Compile a long (PageIndex) document into wiki concept pages.

    The summary page is already written by the indexer. This function
    generates an overview, then plans + generates/updates concept pages.
    """
    from openkb.config import load_config

    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_dir = kb_dir / "wiki"
    schema_md = get_agents_md(wiki_dir)
    summary_text = summary_path.read_text(encoding="utf-8")

    system_msg = {"role": "system", "content": _SYSTEM_TEMPLATE.format(
        schema_md=schema_md, language=language,
    )}
    doc_msg = {"role": "user", "content": _LONG_DOC_SUMMARY_USER.format(
        doc_name=doc_name, doc_id=doc_id, content=summary_text,
    )}

    # Step 1: Generate overview
    overview = _llm_call(model, [system_msg, doc_msg], "overview")

    # Step 2: Compile concepts
    await _compile_concepts(
        wiki_dir, kb_dir, model, system_msg, doc_msg, overview,
        doc_name, max_concurrency,
    )
```

- [ ] **Step 4: Update existing integration tests**

Update `TestCompileShortDoc.test_full_pipeline` — the concepts-list response now needs to be the new dict format:

```python
class TestCompileShortDoc:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "test-doc.md"
        source_path.write_text("# Test Doc\n\nSome content about transformers.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "test-doc.pdf").write_bytes(b"fake")

        summary_response = "# Summary\n\nThis document discusses transformers."
        plan_response = json.dumps({
            "create": [{"name": "transformer", "title": "Transformer"}],
            "update": [],
            "related": [],
        })
        concept_page_response = "# Transformer\n\nA neural network architecture."

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_response, plan_response])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_page_response])
            )
            await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        summary_path = wiki / "summaries" / "test-doc.md"
        assert summary_path.exists()
        assert "sources: [test-doc.pdf]" in summary_path.read_text()

        concept_path = wiki / "concepts" / "transformer.md"
        assert concept_path.exists()
        assert "sources: [test-doc.pdf]" in concept_path.read_text()

        index_text = (wiki / "index.md").read_text()
        assert "[[summaries/test-doc]]" in index_text
        assert "[[concepts/transformer]]" in index_text
```

Update `TestCompileShortDoc.test_handles_bad_json` — no changes needed (bad JSON still triggers fallback).

Update `TestCompileLongDoc.test_full_pipeline`:

```python
class TestCompileLongDoc:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n",
            encoding="utf-8",
        )
        summary_path = wiki / "summaries" / "big-doc.md"
        summary_path.write_text("# Big Doc\n\nPageIndex summary tree.", encoding="utf-8")
        openkb_dir = tmp_path / ".openkb"
        openkb_dir.mkdir()
        (openkb_dir / "config.yaml").write_text("model: gpt-4o-mini\n")
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "big-doc.pdf").write_bytes(b"fake")

        overview_response = "Overview of the big document."
        plan_response = json.dumps({
            "create": [{"name": "deep-learning", "title": "Deep Learning"}],
            "update": [],
            "related": [],
        })
        concept_page_response = "# Deep Learning\n\nA subfield of ML."

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([overview_response, plan_response])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_page_response])
            )
            await compile_long_doc(
                "big-doc", summary_path, "doc-123", tmp_path, "gpt-4o-mini"
            )

        concept_path = wiki / "concepts" / "deep-learning.md"
        assert concept_path.exists()
        assert "Deep Learning" in concept_path.read_text()

        index_text = (wiki / "index.md").read_text()
        assert "[[summaries/big-doc]]" in index_text
        assert "[[concepts/deep-learning]]" in index_text
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/test_compiler.py -v`
Expected: All PASS

- [ ] **Step 6: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All 149+ tests PASS

- [ ] **Step 7: Commit**

```bash
git add openkb/agent/compiler.py tests/test_compiler.py
git commit -m "feat: concept dedup with briefs, update/related paths, extract _compile_concepts"
```

---

### Task 5: Clean up old references and update module docstring

**Files:**
- Modify: `openkb/agent/compiler.py:1-9` (module docstring)

- [ ] **Step 1: Update module docstring**

Replace the docstring at the top of `openkb/agent/compiler.py`:

```python
"""Wiki compilation pipeline for OpenKB.

Pipeline leveraging LLM prompt caching:
  Step 1: Build base context A (schema + document content).
  Step 2: A → generate summary.
  Step 3: A + summary → concepts plan (create/update/related).
  Step 4: Concurrent LLM calls (A cached) → generate new + rewrite updated concepts.
  Step 5: Code adds cross-ref links to related concepts, updates index.
"""
```

- [ ] **Step 2: Verify `_CONCEPTS_LIST_USER` is fully removed**

Search for any remaining references to `_CONCEPTS_LIST_USER` in the codebase:

Run: `grep -r "_CONCEPTS_LIST_USER" openkb/ tests/`
Expected: No matches

- [ ] **Step 3: Run full test suite one final time**

Run: `pytest tests/ -q`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add openkb/agent/compiler.py
git commit -m "chore: update compiler docstring for new pipeline"
```
