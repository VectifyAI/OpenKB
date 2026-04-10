# Retrieve Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify query across long/short docs, add brief summaries to index.md and frontmatter, store long doc sources as JSON with per-page access.

**Architecture:** (1) LLM prompts return `{"brief", "content"}` JSON — briefs flow into frontmatter and index.md. (2) Indexer stores long doc pages as JSON array. (3) New `get_page_content` tool replaces `pageindex_retrieve`. (4) Query agent uses same tools for all docs.

**Tech Stack:** Python, litellm, asyncio, pytest

---

### Task 1: Add `get_page_content` tool and `parse_pages` helper

**Files:**
- Modify: `openkb/agent/tools.py`
- Modify: `tests/test_agent_tools.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_agent_tools.py`:

```python
from openkb.agent.tools import get_page_content, parse_pages

class TestParsePages:
    def test_single_page(self):
        assert parse_pages("3") == [3]

    def test_range(self):
        assert parse_pages("3-5") == [3, 4, 5]

    def test_comma_separated(self):
        assert parse_pages("1,3,5") == [1, 3, 5]

    def test_mixed(self):
        assert parse_pages("1-3,7,10-12") == [1, 2, 3, 7, 10, 11, 12]

    def test_deduplication(self):
        assert parse_pages("3,3,3") == [3]

    def test_sorted(self):
        assert parse_pages("5,1,3") == [1, 3, 5]

    def test_ignores_zero_and_negative(self):
        assert parse_pages("0,-1,3") == [3]


class TestGetPageContent:
    def test_reads_pages_from_json(self, tmp_path):
        import json
        wiki_root = str(tmp_path)
        sources = tmp_path / "sources"
        sources.mkdir()
        pages = [
            {"page": 1, "content": "Page one text."},
            {"page": 2, "content": "Page two text."},
            {"page": 3, "content": "Page three text."},
        ]
        (sources / "paper.json").write_text(json.dumps(pages), encoding="utf-8")

        result = get_page_content("paper", "1,3", wiki_root)
        assert "[Page 1]" in result
        assert "Page one text." in result
        assert "[Page 3]" in result
        assert "Page three text." in result
        assert "Page two" not in result

    def test_returns_error_for_missing_file(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        result = get_page_content("nonexistent", "1", wiki_root)
        assert "not found" in result.lower()

    def test_returns_error_for_no_matching_pages(self, tmp_path):
        import json
        wiki_root = str(tmp_path)
        sources = tmp_path / "sources"
        sources.mkdir()
        pages = [{"page": 1, "content": "Only page."}]
        (sources / "paper.json").write_text(json.dumps(pages), encoding="utf-8")

        result = get_page_content("paper", "99", wiki_root)
        assert "no content" in result.lower() or result.strip() == ""

    def test_includes_images_info(self, tmp_path):
        import json
        wiki_root = str(tmp_path)
        sources = tmp_path / "sources"
        sources.mkdir()
        pages = [
            {"page": 1, "content": "Text.", "images": [{"path": "images/p/img.png", "width": 100, "height": 80}]},
        ]
        (sources / "doc.json").write_text(json.dumps(pages), encoding="utf-8")

        result = get_page_content("doc", "1", wiki_root)
        assert "img.png" in result

    def test_path_escape_denied(self, tmp_path):
        wiki_root = str(tmp_path)
        (tmp_path / "sources").mkdir()
        result = get_page_content("../../etc/passwd", "1", wiki_root)
        assert "denied" in result.lower() or "not found" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agent_tools.py::TestParsePages tests/test_agent_tools.py::TestGetPageContent -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `parse_pages` and `get_page_content`**

Add to `openkb/agent/tools.py`:

```python
import json as _json


def parse_pages(pages: str) -> list[int]:
    """Parse a page specification like '3-5,7,10-12' into a sorted list of ints."""
    result: set[int] = set()
    for part in pages.split(","):
        part = part.strip()
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            try:
                start, end = int(start_str), int(end_str)
                result.update(range(start, end + 1))
            except ValueError:
                continue
        else:
            try:
                result.add(int(part))
            except ValueError:
                continue
    return sorted(n for n in result if n >= 1)


def get_page_content(doc_name: str, pages: str, wiki_root: str) -> str:
    """Get text content of specific pages from a long document.

    Reads from ``wiki/sources/{doc_name}.json`` which contains a JSON array
    of ``{"page": int, "content": str, "images": [...]}`` objects.

    Args:
        doc_name: Document name (stem, e.g. ``'attention-is-all-you-need'``).
        pages: Page specification (e.g. ``'3-5,7,10-12'``).
        wiki_root: Absolute path to the wiki root directory.

    Returns:
        Formatted text of requested pages, or error message if not found.
    """
    root = Path(wiki_root).resolve()
    json_path = (root / "sources" / f"{doc_name}.json").resolve()
    if not json_path.is_relative_to(root):
        return "Access denied: path escapes wiki root."
    if not json_path.exists():
        return f"Document not found: {doc_name}. No sources/{doc_name}.json file."

    data = _json.loads(json_path.read_text(encoding="utf-8"))
    page_nums = set(parse_pages(pages))
    matched = [p for p in data if p["page"] in page_nums]

    if not matched:
        return f"No content found for pages: {pages}"

    parts: list[str] = []
    for p in matched:
        header = f"[Page {p['page']}]"
        text = p.get("content", "")
        if "images" in p:
            img_refs = ", ".join(img["path"] for img in p["images"])
            text += f"\n[Images: {img_refs}]"
        parts.append(f"{header}\n{text}")

    return "\n\n".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agent_tools.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add openkb/agent/tools.py tests/test_agent_tools.py
git commit -m "feat: add get_page_content tool and parse_pages helper"
```

---

### Task 2: Change LLM prompts to return `{"brief", "content"}` JSON

**Files:**
- Modify: `openkb/agent/compiler.py` (prompt templates, lines 40-105)
- Modify: `tests/test_compiler.py` (TestParseConceptsPlan)

- [ ] **Step 1: Write test for brief+content JSON parsing**

Add to `tests/test_compiler.py`:

```python
class TestParseBriefContent:
    def test_dict_with_brief_and_content(self):
        text = json.dumps({"brief": "A short desc", "content": "# Full page\n\nDetails."})
        parsed = _parse_json(text)
        assert parsed["brief"] == "A short desc"
        assert "# Full page" in parsed["content"]

    def test_plain_text_fallback(self):
        """If LLM returns plain text, _parse_json raises — caller handles fallback."""
        with pytest.raises((json.JSONDecodeError, ValueError)):
            _parse_json("Just plain markdown text without JSON")
```

- [ ] **Step 2: Run test to verify it passes (existing _parse_json handles dicts)**

Run: `pytest tests/test_compiler.py::TestParseBriefContent -v`
Expected: PASS — `_parse_json` already handles dicts

- [ ] **Step 3: Update `_SUMMARY_USER` prompt**

Replace in `openkb/agent/compiler.py`:

```python
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
```

- [ ] **Step 4: Update `_CONCEPT_PAGE_USER` prompt**

Replace in `openkb/agent/compiler.py`:

```python
_CONCEPT_PAGE_USER = """\
Write the concept page for: {title}

This concept relates to the document "{doc_name}" summarized above.
{update_instruction}

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) defining this concept
- "content": The full concept page in Markdown. Include clear explanation, \
key details from the source document, and [[wikilinks]] to related concepts \
and [[summaries/{doc_name}]]

Return ONLY valid JSON, no fences.
"""
```

- [ ] **Step 5: Update `_CONCEPT_UPDATE_USER` prompt**

Replace in `openkb/agent/compiler.py`:

```python
_CONCEPT_UPDATE_USER = """\
Update the concept page for: {title}

Current content of this page:
{existing_content}

New information from document "{doc_name}" (summarized above) should be \
integrated into this page. Rewrite the full page incorporating the new \
information naturally — do not just append. Maintain existing \
[[wikilinks]] and add new ones where appropriate.

Return a JSON object with two keys:
- "brief": A single sentence (under 100 chars) defining this concept (may differ from before)
- "content": The rewritten full concept page in Markdown

Return ONLY valid JSON, no fences.
"""
```

- [ ] **Step 6: Run all tests (prompts aren't tested directly)**

Run: `pytest tests/test_compiler.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add openkb/agent/compiler.py tests/test_compiler.py
git commit -m "feat: update LLM prompts to return brief+content JSON"
```

---

### Task 3: Update `_write_summary` and `_write_concept` to store `brief` in frontmatter

**Files:**
- Modify: `openkb/agent/compiler.py` (lines 274-320, `_write_summary` and `_write_concept`)
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing tests**

Update existing and add new tests in `tests/test_compiler.py`:

```python
class TestWriteSummary:
    def test_writes_with_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(wiki, "my-doc", "my-doc.pdf", "# Summary\n\nContent here.", brief="Introduces transformers")
        path = wiki / "summaries" / "my-doc.md"
        assert path.exists()
        text = path.read_text()
        assert "sources: [my-doc.pdf]" in text
        assert "brief: Introduces transformers" in text
        assert "# Summary" in text

    def test_writes_without_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_summary(wiki, "my-doc", "my-doc.pdf", "# Summary\n\nContent here.")
        path = wiki / "summaries" / "my-doc.md"
        text = path.read_text()
        assert "sources: [my-doc.pdf]" in text
        assert "brief:" not in text
```

Update `TestWriteConcept`:

```python
class TestWriteConcept:
    def test_new_concept_with_brief(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(wiki, "attention", "# Attention\n\nDetails.", "paper.pdf", False, brief="Mechanism for selective focus")
        path = wiki / "concepts" / "attention.md"
        assert path.exists()
        text = path.read_text()
        assert "sources: [paper.pdf]" in text
        assert "brief: Mechanism for selective focus" in text
        assert "# Attention" in text

    def test_new_concept(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        _write_concept(wiki, "attention", "# Attention\n\nDetails.", "paper.pdf", False)
        path = wiki / "concepts" / "attention.md"
        assert path.exists()
        text = path.read_text()
        assert "sources: [paper.pdf]" in text
        assert "# Attention" in text

    def test_update_concept_appends_source(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper1.pdf]\nbrief: Old brief\n---\n\n# Attention\n\nOld content.",
            encoding="utf-8",
        )
        _write_concept(wiki, "attention", "New info from paper2.", "paper2.pdf", True, brief="Updated brief")
        text = (concepts / "attention.md").read_text()
        assert "paper2.pdf" in text
        assert "paper1.pdf" in text
        assert "brief: Updated brief" in text
        assert "New info from paper2." in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_compiler.py::TestWriteSummary tests/test_compiler.py::TestWriteConcept -v`
Expected: FAIL — `_write_summary` and `_write_concept` don't accept `brief` parameter

- [ ] **Step 3: Update `_write_summary` to accept `brief`**

```python
def _write_summary(wiki_dir: Path, doc_name: str, source_file: str, summary: str, brief: str = "") -> None:
    """Write summary page with frontmatter."""
    summaries_dir = wiki_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    fm_lines = [f"sources: [{source_file}]"]
    if brief:
        fm_lines.append(f"brief: {brief}")
    frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
    (summaries_dir / f"{doc_name}.md").write_text(frontmatter + summary, encoding="utf-8")
```

- [ ] **Step 4: Update `_write_concept` to accept `brief`**

Add `brief: str = ""` parameter to `_write_concept`. In the new-concept branch:

```python
    else:
        fm_lines = [f"sources: [{source_file}]"]
        if brief:
            fm_lines.append(f"brief: {brief}")
        frontmatter = "---\n" + "\n".join(fm_lines) + "\n---\n\n"
        path.write_text(frontmatter + content, encoding="utf-8")
```

In the update branch, after updating sources in frontmatter, also update brief:

```python
    if is_update and path.exists():
        existing = path.read_text(encoding="utf-8")
        if source_file not in existing:
            # ... existing frontmatter update logic ...
        # Update brief in frontmatter if provided
        if brief and existing.startswith("---"):
            end = existing.find("---", 3)
            if end != -1:
                fm = existing[:end + 3]
                body = existing[end + 3:]
                if "brief:" in fm:
                    import re
                    fm = re.sub(r"brief:.*", f"brief: {brief}", fm)
                else:
                    fm = fm.replace("---\n", f"---\nbrief: {brief}\n", 1)
                existing = fm + body
        path.write_text(existing, encoding="utf-8")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_compiler.py::TestWriteSummary tests/test_compiler.py::TestWriteConcept -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add openkb/agent/compiler.py tests/test_compiler.py
git commit -m "feat: store brief in frontmatter of summary and concept pages"
```

---

### Task 4: Update `_update_index` to include briefs, and update `_read_concept_briefs` to read from frontmatter

**Files:**
- Modify: `openkb/agent/compiler.py` (lines 233-261 and 408-430)
- Modify: `tests/test_compiler.py`

- [ ] **Step 1: Write failing tests for `_update_index` with briefs**

```python
class TestUpdateIndex:
    def test_appends_entries_with_briefs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", ["attention", "transformer"],
                       doc_brief="Introduces transformers",
                       concept_briefs={"attention": "Focus mechanism", "transformer": "NN architecture"})
        text = (wiki / "index.md").read_text()
        assert "[[summaries/my-doc]] — Introduces transformers" in text
        assert "[[concepts/attention]] — Focus mechanism" in text
        assert "[[concepts/transformer]] — NN architecture" in text

    def test_no_duplicates(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n- [[summaries/my-doc]] — Old brief\n\n## Concepts\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", [], doc_brief="New brief")
        text = (wiki / "index.md").read_text()
        assert text.count("[[summaries/my-doc]]") == 1

    def test_backwards_compat_no_briefs(self, tmp_path):
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        _update_index(wiki, "my-doc", ["attention"])
        text = (wiki / "index.md").read_text()
        assert "[[summaries/my-doc]]" in text
        assert "[[concepts/attention]]" in text
```

Write test for updated `_read_concept_briefs`:

```python
class TestReadConceptBriefs:
    # ... keep existing tests ...

    def test_reads_brief_from_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "attention.md").write_text(
            "---\nsources: [paper.pdf]\nbrief: Selective focus mechanism\n---\n\n# Attention\n\nLong content...",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- attention: Selective focus mechanism" in result

    def test_falls_back_to_body_truncation(self, tmp_path):
        wiki = tmp_path / "wiki"
        concepts = wiki / "concepts"
        concepts.mkdir(parents=True)
        (concepts / "old.md").write_text(
            "---\nsources: [paper.pdf]\n---\n\nOld concept without brief field.",
            encoding="utf-8",
        )
        result = _read_concept_briefs(wiki)
        assert "- old: Old concept without brief field." in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_compiler.py::TestUpdateIndex tests/test_compiler.py::TestReadConceptBriefs -v`
Expected: FAIL — `_update_index` doesn't accept `doc_brief`/`concept_briefs` parameters

- [ ] **Step 3: Update `_update_index`**

```python
def _update_index(
    wiki_dir: Path, doc_name: str, concept_names: list[str],
    doc_brief: str = "", concept_briefs: dict[str, str] | None = None,
) -> None:
    """Append document and concept entries to index.md with optional briefs."""
    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        index_path.write_text(
            "# Knowledge Base Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )

    text = index_path.read_text(encoding="utf-8")

    doc_link = f"[[summaries/{doc_name}]]"
    if doc_link not in text:
        doc_entry = f"- {doc_link}"
        if doc_brief:
            doc_entry += f" — {doc_brief}"
        if "## Documents" in text:
            text = text.replace("## Documents\n", f"## Documents\n{doc_entry}\n", 1)

    if concept_briefs is None:
        concept_briefs = {}
    for name in concept_names:
        concept_link = f"[[concepts/{name}]]"
        if concept_link not in text:
            concept_entry = f"- {concept_link}"
            if name in concept_briefs:
                concept_entry += f" — {concept_briefs[name]}"
            if "## Concepts" in text:
                text = text.replace("## Concepts\n", f"## Concepts\n{concept_entry}\n", 1)

    index_path.write_text(text, encoding="utf-8")
```

- [ ] **Step 4: Update `_read_concept_briefs` to read from frontmatter `brief:` field**

```python
def _read_concept_briefs(wiki_dir: Path) -> str:
    """Read existing concept pages and return compact one-line summaries.

    Reads ``brief:`` from YAML frontmatter if available, otherwise falls back
    to the first 150 characters of the body text.
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
                # Try to extract brief from frontmatter
                for line in fm.split("\n"):
                    if line.startswith("brief:"):
                        brief = line[len("brief:"):].strip()
                        break
        if not brief:
            brief = body.strip().replace("\n", " ")[:150]
        if brief:
            lines.append(f"- {path.stem}: {brief}")

    return "\n".join(lines) or "(none yet)"
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_compiler.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add openkb/agent/compiler.py tests/test_compiler.py
git commit -m "feat: add briefs to index.md entries and read from frontmatter"
```

---

### Task 5: Wire briefs through `_compile_concepts` and public functions

**Files:**
- Modify: `openkb/agent/compiler.py` (lines 438-611, `_compile_concepts`, `compile_short_doc`, `compile_long_doc`)
- Modify: `tests/test_compiler.py`

This task connects the brief+content JSON parsing to the write functions and index update.

- [ ] **Step 1: Write integration test**

```python
class TestBriefIntegration:
    @pytest.mark.asyncio
    async def test_short_doc_briefs_in_index_and_frontmatter(self, tmp_path):
        wiki = tmp_path / "wiki"
        (wiki / "sources").mkdir(parents=True)
        (wiki / "summaries").mkdir(parents=True)
        (wiki / "concepts").mkdir(parents=True)
        (wiki / "index.md").write_text(
            "# Index\n\n## Documents\n\n## Concepts\n\n## Explorations\n",
            encoding="utf-8",
        )
        source_path = wiki / "sources" / "test-doc.md"
        source_path.write_text("# Test Doc\n\nContent.", encoding="utf-8")
        (tmp_path / ".openkb").mkdir()
        (tmp_path / "raw").mkdir()
        (tmp_path / "raw" / "test-doc.pdf").write_bytes(b"fake")

        summary_resp = json.dumps({
            "brief": "A paper about transformers",
            "content": "# Summary\n\nThis paper discusses transformers.",
        })
        plan_resp = json.dumps({
            "create": [{"name": "transformer", "title": "Transformer"}],
            "update": [],
            "related": [],
        })
        concept_resp = json.dumps({
            "brief": "NN architecture using self-attention",
            "content": "# Transformer\n\nA neural network architecture.",
        })

        with patch("openkb.agent.compiler.litellm") as mock_litellm:
            mock_litellm.completion = MagicMock(
                side_effect=_mock_completion([summary_resp, plan_resp])
            )
            mock_litellm.acompletion = AsyncMock(
                side_effect=_mock_acompletion([concept_resp])
            )
            await compile_short_doc("test-doc", source_path, tmp_path, "gpt-4o-mini")

        # Check summary frontmatter has brief
        summary_text = (wiki / "summaries" / "test-doc.md").read_text()
        assert "brief: A paper about transformers" in summary_text

        # Check concept frontmatter has brief
        concept_text = (wiki / "concepts" / "transformer.md").read_text()
        assert "brief: NN architecture using self-attention" in concept_text

        # Check index has briefs
        index_text = (wiki / "index.md").read_text()
        assert "[[summaries/test-doc]] — A paper about transformers" in index_text
        assert "[[concepts/transformer]] — NN architecture using self-attention" in index_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_compiler.py::TestBriefIntegration -v`
Expected: FAIL

- [ ] **Step 3: Update `compile_short_doc` to parse brief+content from summary response**

In `compile_short_doc`, replace:

```python
    # --- Step 1: Generate summary ---
    summary = _llm_call(model, [system_msg, doc_msg], "summary")
    _write_summary(wiki_dir, doc_name, source_file, summary)
```

With:

```python
    # --- Step 1: Generate summary ---
    summary_raw = _llm_call(model, [system_msg, doc_msg], "summary")
    try:
        summary_parsed = _parse_json(summary_raw)
        doc_brief = summary_parsed.get("brief", "")
        summary = summary_parsed.get("content", summary_raw)
    except (json.JSONDecodeError, ValueError):
        doc_brief = ""
        summary = summary_raw
    _write_summary(wiki_dir, doc_name, source_file, summary, brief=doc_brief)
```

- [ ] **Step 4: Update `_compile_concepts` signature and wiring**

Add `doc_brief: str = ""` parameter to `_compile_concepts`.

In `_gen_create`, parse the response:

```python
    async def _gen_create(concept: dict) -> tuple[str, str, bool, str]:
        name = concept["name"]
        title = concept.get("title", name)
        async with semaphore:
            raw = await _llm_call_async(model, [
                system_msg, doc_msg,
                {"role": "assistant", "content": summary},
                {"role": "user", "content": _CONCEPT_PAGE_USER.format(
                    title=title, doc_name=doc_name, update_instruction="",
                )},
            ], f"create:{name}")
        try:
            parsed = _parse_json(raw)
            brief = parsed.get("brief", "")
            content = parsed.get("content", raw)
        except (json.JSONDecodeError, ValueError):
            brief, content = "", raw
        return name, content, False, brief
```

Same for `_gen_update` — returns `tuple[str, str, bool, str]` (name, content, is_update, brief).

In the results processing loop:

```python
    concept_briefs_map: dict[str, str] = {}
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Concept generation failed: %s", r)
            continue
        name, page_content, is_update, brief = r
        _write_concept(wiki_dir, name, page_content, source_file, is_update, brief=brief)
        concept_names.append(name)
        if brief:
            concept_briefs_map[name] = brief
```

Pass briefs to `_update_index`:

```python
    _update_index(wiki_dir, doc_name, concept_names,
                  doc_brief=doc_brief, concept_briefs=concept_briefs_map)
```

- [ ] **Step 5: Update `compile_short_doc` to pass `doc_brief` to `_compile_concepts`**

```python
    await _compile_concepts(
        wiki_dir, kb_dir, model, system_msg, doc_msg,
        summary, doc_name, max_concurrency, doc_brief=doc_brief,
    )
```

- [ ] **Step 6: Update `compile_long_doc` to pass `doc_brief` from `IndexResult.description`**

`compile_long_doc` currently takes `doc_id` but not `description`. Add `doc_description: str = ""` parameter:

```python
async def compile_long_doc(
    doc_name: str,
    summary_path: Path,
    doc_id: str,
    kb_dir: Path,
    model: str,
    doc_description: str = "",
    max_concurrency: int = DEFAULT_COMPILE_CONCURRENCY,
) -> None:
```

The `_LONG_DOC_SUMMARY_USER` stays unchanged (returns plain text, not JSON). Pass `doc_description` as `doc_brief`:

```python
    await _compile_concepts(
        wiki_dir, kb_dir, model, system_msg, doc_msg,
        overview, doc_name, max_concurrency, doc_brief=doc_description,
    )
```

Also update the CLI call in `cli.py` line 135:

```python
asyncio.run(
    compile_long_doc(doc_name, summary_path, index_result.doc_id, kb_dir, model,
                     doc_description=index_result.description)
)
```

- [ ] **Step 7: Update existing integration tests for new JSON response format**

Update all mock LLM responses in `TestCompileShortDoc`, `TestCompileLongDoc`, and `TestCompileConceptsPlan` to return `{"brief": "...", "content": "..."}` JSON instead of plain text for summary and concept responses.

- [ ] **Step 8: Run all tests**

Run: `pytest tests/ -q`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add openkb/agent/compiler.py openkb/cli.py tests/test_compiler.py
git commit -m "feat: wire brief+content JSON through compile pipeline to index and frontmatter"
```

---

### Task 6: Indexer — long doc sources from markdown to JSON

**Files:**
- Modify: `openkb/indexer.py`
- Modify: `openkb/tree_renderer.py` (remove `render_source_md`)
- Modify: `tests/test_indexer.py`

- [ ] **Step 1: Write failing test**

Update `tests/test_indexer.py`:

```python
    def test_source_page_written_as_json(self, kb_dir, sample_tree, tmp_path):
        """Long doc source should be written as JSON, not markdown."""
        import json as json_mod
        doc_id = "abc-123"
        fake_col = self._make_fake_collection(doc_id, sample_tree)

        fake_client = MagicMock()
        fake_client.collection.return_value = fake_col
        # Mock get_page_content to return page data
        fake_col.get_page_content.return_value = [
            {"page": 1, "content": "Page one text."},
            {"page": 2, "content": "Page two text."},
        ]

        pdf_path = tmp_path / "sample.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 fake")

        with patch("openkb.indexer.PageIndexClient", return_value=fake_client):
            index_long_document(pdf_path, kb_dir)

        # Should be JSON, not MD
        json_file = kb_dir / "wiki" / "sources" / "sample.json"
        assert json_file.exists()
        assert not (kb_dir / "wiki" / "sources" / "sample.md").exists()
        data = json_mod.loads(json_file.read_text())
        assert len(data) == 2
        assert data[0]["page"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_indexer.py::TestIndexLongDocument::test_source_page_written_as_json -v`
Expected: FAIL

- [ ] **Step 3: Update `indexer.py` to write JSON sources**

Replace the source writing block (lines 103-110) with:

```python
    # Write wiki/sources/ as JSON (per-page content from PageIndex)
    sources_dir = kb_dir / "wiki" / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    dest_images_dir = sources_dir / "images" / pdf_path.stem

    # Get per-page content from PageIndex
    all_pages = col.get_page_content(doc_id, f"1-{doc.get('page_count', 9999)}")

    # Relocate image paths
    dest_images_dir.mkdir(parents=True, exist_ok=True)
    for page in all_pages:
        if "images" in page:
            for img in page["images"]:
                src_path = Path(img["path"])
                if src_path.exists():
                    filename = src_path.name
                    dest = dest_images_dir / filename
                    if not dest.exists():
                        shutil.copy2(src_path, dest)
                    img["path"] = f"images/{pdf_path.stem}/{filename}"

    import json as json_mod
    (sources_dir / f"{pdf_path.stem}.json").write_text(
        json_mod.dumps(all_pages, ensure_ascii=False, indent=2), encoding="utf-8",
    )
```

Remove the `render_source_md` import and `_relocate_images` call.

- [ ] **Step 4: Remove `render_source_md` from tree_renderer.py**

Remove the `render_source_md` function and `_render_nodes_source` helper from `openkb/tree_renderer.py`. Keep `render_summary_md` and `_render_nodes_summary`.

- [ ] **Step 5: Update existing test `test_source_page_written`**

The old test checks for `.md` — update it to check for `.json` or remove it (replaced by the new test).

- [ ] **Step 6: Run all tests**

Run: `pytest tests/ -q`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add openkb/indexer.py openkb/tree_renderer.py tests/test_indexer.py
git commit -m "feat: store long doc sources as per-page JSON, remove render_source_md"
```

---

### Task 7: Query agent — remove `pageindex_retrieve`, add `get_page_content`, update instructions

**Files:**
- Modify: `openkb/agent/query.py`
- Modify: `openkb/schema.py`
- Modify: `tests/test_query.py`

- [ ] **Step 1: Write failing tests**

Update `tests/test_query.py`:

```python
class TestBuildQueryAgent:
    def test_agent_name(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert agent.name == "wiki-query"

    def test_agent_has_three_tools(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert len(agent.tools) == 3

    def test_agent_tool_names(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        names = {t.name for t in agent.tools}
        assert "list_files" in names
        assert "read_file" in names
        assert "get_page_content" in names
        assert "pageindex_retrieve" not in names

    def test_instructions_mention_get_page_content(self, tmp_path):
        agent = build_query_agent(str(tmp_path), "gpt-4o-mini")
        assert "get_page_content" in agent.instructions
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_query.py::TestBuildQueryAgent -v`
Expected: FAIL — old signature requires `openkb_dir`

- [ ] **Step 3: Rewrite `query.py`**

Remove `_pageindex_retrieve_impl` entirely (~110 lines). Remove `PageIndexClient` import. Update `build_query_agent`:

```python
def build_query_agent(wiki_root: str, model: str, language: str = "en") -> Agent:
    """Build and return the Q&A agent."""
    schema_md = get_agents_md(Path(wiki_root))
    instructions = _QUERY_INSTRUCTIONS_TEMPLATE.format(schema_md=schema_md)
    instructions += f"\n\nIMPORTANT: Write all wiki content in {language} language."

    @function_tool
    def list_files(directory: str) -> str:
        """List all Markdown files in a wiki subdirectory."""
        return list_wiki_files(directory, wiki_root)

    @function_tool
    def read_file(path: str) -> str:
        """Read a Markdown file from the wiki."""
        return read_wiki_file(path, wiki_root)

    @function_tool
    def get_page_content_tool(doc_name: str, pages: str) -> str:
        """Get text content of specific pages from a long document.

        Args:
            doc_name: Document name (e.g. 'attention-is-all-you-need').
            pages: Page specification (e.g. '3-5,7,10-12').
        """
        from openkb.agent.tools import get_page_content
        return get_page_content(doc_name, pages, wiki_root)

    from agents.model_settings import ModelSettings

    return Agent(
        name="wiki-query",
        instructions=instructions,
        tools=[list_files, read_file, get_page_content_tool],
        model=f"litellm/{model}",
        model_settings=ModelSettings(parallel_tool_calls=False),
    )
```

Update `_QUERY_INSTRUCTIONS_TEMPLATE`:

```python
_QUERY_INSTRUCTIONS_TEMPLATE = """\
You are a knowledge-base Q&A agent. You answer questions by searching the wiki.

{schema_md}

## Search strategy
1. Read index.md to understand what documents and concepts are available.
   Each entry has a brief summary to help you judge relevance.
2. Read relevant summary pages (summaries/) for document overviews.
3. Read concept pages (concepts/) for cross-document synthesis.
4. For long documents, use get_page_content(doc_name, pages) to read
   specific pages when you need detailed content. The summary page
   shows chapter structure with page ranges to help you decide which
   pages to read.
5. Synthesise a clear, well-cited answer.

Always ground your answer in the wiki content. If you cannot find relevant
information, say so clearly.
"""
```

Update `run_query` to match new `build_query_agent` signature (remove `openkb_dir` param):

```python
async def run_query(question: str, kb_dir: Path, model: str, stream: bool = False) -> str:
    from openkb.config import load_config
    openkb_dir = kb_dir / ".openkb"
    config = load_config(openkb_dir / "config.yaml")
    language: str = config.get("language", "en")

    wiki_root = str(kb_dir / "wiki")
    agent = build_query_agent(wiki_root, model, language=language)
    # ... rest unchanged ...
```

- [ ] **Step 4: Update `openkb/schema.py` AGENTS_MD**

Add a note about `get_page_content` for long documents in the Schema:

```python
## Page Types
- **Summary Page** (summaries/): Key content of a single source document.
- **Concept Page** (concepts/): Cross-document topic synthesis with [[wikilinks]].
- **Exploration Page** (explorations/): Saved query results — analyses, comparisons, syntheses.
- **Source Page** (sources/): Full-text for short docs (.md) or per-page JSON for long docs (.json).
- **Index Page** (index.md): One-liner summary of every page in the wiki. Auto-maintained.
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/ -q`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add openkb/agent/query.py openkb/schema.py tests/test_query.py
git commit -m "feat: replace pageindex_retrieve with get_page_content, unify query for all docs"
```

---

### Task 8: Final cleanup and full verification

**Files:**
- Modify: `openkb/indexer.py` (remove unused imports)
- Verify all files

- [ ] **Step 1: Remove unused imports**

In `indexer.py`, remove `from openkb.tree_renderer import render_source_md` if still present (keep `render_summary_md`).

In `query.py`, verify `PageIndexClient` import is removed.

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Grep for dead references**

Run: `grep -r "pageindex_retrieve\|render_source_md\|_relocate_images" openkb/ tests/`
Expected: No matches

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: remove dead imports and references"
```
