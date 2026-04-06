# PageIndex Cloud Bug: get_document_structure returns empty

## Root Cause

Cloud API endpoint `GET /doc/{doc_id}/?type=tree&summary=true` returns the tree structure in the `result` field:

```json
{
  "doc_id": "pi-cmnn5uxd001j901p8two1awyp",
  "status": "completed",
  "retrieval_ready": true,
  "result": [
    {
      "title": "...",
      "node_id": "...",
      "page_index": "...",
      "prefix_summary": "...",
      "text": "...",
      "nodes": [...]
    }
  ]
}
```

But `cloud.py` only looks for `tree` and `structure` keys, missing `result`:

```python
# cloud.py line 172 — get_document_structure
return resp.get("tree", resp.get("structure", []))

# cloud.py line 167 — get_document
"structure": tree_resp.get("tree", tree_resp.get("structure", [])),
```

Since neither `tree` nor `structure` exists in the response, both methods return `[]`.

Additionally, the node schema differs between local and cloud:
- Local: `start_index`, `end_index`, `summary`
- Cloud: `page_index`, `prefix_summary`

## Affected Methods

- `CloudBackend.get_document_structure()` — returns `[]` instead of tree
- `CloudBackend.get_document()` — `structure` field is `[]`

## Impact

- `pageindex_retrieve` in OpenKB cannot do structure-based page selection for cloud documents
- Any consumer of `get_document_structure()` gets empty results for cloud docs

## Suggested Fix

### 1. Parse `result` field (cloud.py line 170-172)

```python
def get_document_structure(self, collection: str, doc_id: str) -> list:
    resp = self._request("GET", f"/doc/{self._enc(doc_id)}/",
                         params={"type": "tree", "summary": "true"})
    tree = resp.get("tree", resp.get("structure", resp.get("result", [])))
    return self._normalize_tree(tree)
```

### 2. Same for get_document (cloud.py line 156-168)

```python
"structure": self._normalize_tree(
    tree_resp.get("tree", tree_resp.get("structure", tree_resp.get("result", [])))
),
```

### 3. Normalize cloud node schema to match local (new helper)

```python
def _normalize_tree(self, nodes: list) -> list:
    """Normalize cloud tree nodes to match local schema."""
    result = []
    for node in nodes:
        normalized = {
            "title": node.get("title", ""),
            "node_id": node.get("node_id", ""),
            "summary": node.get("summary", node.get("prefix_summary", "")),
            "start_index": node.get("start_index", node.get("page_index", "")),
            "end_index": node.get("end_index", node.get("page_index", "")),
        }
        if "text" in node:
            normalized["text"] = node["text"]
        children = node.get("nodes", [])
        if children:
            normalized["nodes"] = self._normalize_tree(children)
        result.append(normalized)
    return result
```

This ensures cloud and local return the same node schema:
```python
{"title", "node_id", "summary", "start_index", "end_index", "text", "nodes"}
```

### 4. Also fix get_page_content (cloud.py line 174-182)

Verify the OCR endpoint response format. Current code expects `pages` or `ocr` key — confirm this matches the actual cloud API response.
