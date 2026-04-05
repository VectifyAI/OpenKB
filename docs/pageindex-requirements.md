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

**Status**: Pending PageIndex implementation
**Priority**: High — blocks OpenKB long-document sources/ generation
**Affects**: `openkb/indexer.py`, `openkb/tree_renderer.py`
