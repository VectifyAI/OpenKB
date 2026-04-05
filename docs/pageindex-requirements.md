# PageIndex SDK Requirements from OpenKB

## feat: `get_document()` should return structure with text

### Background

OpenKB uses PageIndex to index long documents and needs to generate readable markdown files from the tree structure. Currently this requires 3 separate API calls and the text is still missing.

### Current Behavior

`col.get_document(doc_id)` returns metadata only:
```json
{
  "doc_id": "xxx",
  "doc_name": "Introduction to Agents",
  "doc_description": "A comprehensive guide...",
  "doc_type": "pdf",
  "file_path": "..."
}
```

To get the full picture, 3 calls are needed:
1. `col.get_document(doc_id)` → metadata
2. `col.get_document_structure(doc_id)` → tree with summary but **no text**
3. `col.get_page_content(doc_id, pages)` → raw page text (not organized by node)

Even with `IndexConfig(if_add_node_text=True)`, the text is stripped from the structure by `remove_structure_text()` at the end of the pipeline. So there is no way to get the tree structure with node-level text through the public API.

### Expected Behavior

`col.get_document(doc_id)` should return the complete document including structure with text:

```json
{
  "doc_id": "xxx",
  "doc_name": "Introduction to Agents",
  "doc_description": "A comprehensive guide...",
  "doc_type": "pdf",
  "file_path": "...",
  "structure": [
    {
      "title": "Preface",
      "node_id": "0000",
      "start_index": 1,
      "end_index": 6,
      "summary": "Overview of the guide...",
      "text": "This guide introduces AI agents..."
    },
    {
      "title": "Core Architecture",
      "node_id": "0001",
      "start_index": 7,
      "end_index": 15,
      "summary": "Model, tools, and orchestration...",
      "text": "The core of an AI agent consists of...",
      "nodes": [...]
    }
  ]
}
```

One call returns everything: metadata + structure + summaries + text.

### Why

- OpenKB renders PageIndex trees into readable markdown wiki pages (`wiki/sources/`)
- Currently sources/ files only have titles and page ranges — no actual content
- `get_page_content` returns text by page, not by node — can't map back to tree structure
- Downstream consumers (wiki compilers, exporters) need the complete tree with text

### Suggestion

When `IndexConfig(if_add_node_text=True)` was used during indexing, `get_document()` should include the `structure` field with `text` on each node. If `if_add_node_text` was False, the `text` field can be omitted but `structure` with summaries should still be included.

---

**Status**: Done
**Priority**: High
**Resolution**: `get_document(doc_id, include_text=True)` implemented

---

## feat: support custom `images_dir` for image output

### Background

When PageIndex indexes a PDF, its PdfParser extracts images to an internal path: `.okb/files/{collection}/{doc_id}/images/`. The `text` field in the tree structure contains image references like:

```
![image](.okb/files/default/abc123/images/p1_img0.png)
```

OpenKB needs to render the tree into a wiki markdown file at `wiki/sources/{name}.md`. The images need to be accessible from Obsidian which opens `wiki/` as a vault. But the images are inside `.okb/` which is outside `wiki/`.

### Current Behavior

`local.py` hardcodes the images directory:
```python
images_dir = str(col_dir / doc_id / "images")
parsed = parser.parse(file_path, model=self._model, images_dir=images_dir)
```

There's no way for the caller to specify where images should go.

### Expected Behavior

Support a custom `images_dir` — either via `col.add()` parameter or `IndexConfig`:

```python
# Option A: parameter on col.add()
doc_id = col.add("textbook.pdf", images_dir="wiki/sources/images/textbook")

# Option B: IndexConfig field
config = IndexConfig(
    if_add_node_text=True,
    images_dir="wiki/sources/images/textbook",
)
```

When `images_dir` is set:
1. PdfParser saves images to the specified directory
2. Image paths in `text` fields use the specified directory (relative)
3. `get_document(doc_id, include_text=True)` returns text with correct paths

When `images_dir` is not set, behavior is unchanged (images go to internal `.okb/files/...`).

### Why

- OpenKB renders PageIndex trees into `wiki/sources/*.md` with image references
- Obsidian opens `wiki/` as a vault and needs images inside `wiki/`
- Currently images are trapped in `.okb/files/.../images/` — invisible to Obsidian
- Without this, OpenKB has to post-process: parse all `![image](.okb/...)` paths, copy files, rewrite paths — fragile and wasteful

### Workaround

Until this is implemented, OpenKB will post-process the text:
1. Regex-find all `![image](.okb/files/.../images/...)` paths
2. Copy image files to `wiki/sources/images/{doc_name}/`
3. Rewrite paths in the rendered markdown

---

**Status**: Pending
**Priority**: Medium — has workaround but adds complexity
**Affects**: `openkb/indexer.py`
