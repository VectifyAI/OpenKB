# PageIndex Cloud workflows

OpenKB scales to long documents with [PageIndex](https://github.com/VectifyAI/PageIndex)'s
tree-based, vectorless retrieval. This guide covers the three ways long documents
flow through OpenKB: **local** indexing, **cloud** OCR/indexing, and **importing**
a document already indexed in PageIndex Cloud.

> **Try it with** [`../docs/deepseek-r1.pdf`](../docs/deepseek-r1.pdf) (~22 pages,
> just over the threshold → long-doc path) or
> [`../docs/Bishop-Pattern-Recognition-and-Machine-Learning-2006.pdf`](../docs/Bishop-Pattern-Recognition-and-Machine-Learning-2006.pdf)
> (700+ pages → exercises cloud OCR + page windowing).

---

## Short vs. long: the threshold

When you `openkb add` a PDF, its page count decides the path:

| Page count | Path | Engine |
| --- | --- | --- |
| `< pageindex_threshold` (default 20) | short-doc | markitdown → LLM reads full text |
| `≥ pageindex_threshold` | long-doc | PageIndex tree index |

For the long-doc path, whether it runs **locally** or in the **cloud** depends only
on one environment variable:

| `PAGEINDEX_API_KEY` | Long-doc engine |
| --- | --- |
| unset | local (pymupdf text + image extraction) |
| set | PageIndex Cloud OCR (markdown + figures), with local fallback if the cloud call fails |

```bash
# Local long-doc indexing — no key, no network
openkb add ../docs/Bishop-Pattern-Recognition-and-Machine-Learning-2006.pdf

# Cloud OCR for the same long PDF — just set the key first
export PAGEINDEX_API_KEY="pi-..."
openkb add ../docs/Bishop-Pattern-Recognition-and-Machine-Learning-2006.pdf
```

Either way the result is the same wiki artifacts (`wiki/sources/<doc>.json` +
`wiki/summaries/<doc>.md` + concept/entity pages) and the document shows up as
type `pageindex` in `openkb list`.

---

## Importing an already-indexed cloud document

If a document is **already indexed in PageIndex Cloud**, you don't need the local
PDF at all — import it by `doc_id`:

```bash
export PAGEINDEX_API_KEY="pi-..."
openkb add --from-pageindex-cloud <DOC_ID>
```

This fetches the document tree (structure + description) and OCR'd page content
from the cloud, then compiles concepts and entities locally — exactly like a local
long PDF, but with no file on disk. It is registered with type `pageindex_cloud`.

What it does **not** do:

- It never modifies the cloud corpus — import is read-only.
- It's **idempotent** — re-importing the same `doc_id` is skipped.
- `openkb remove` on an imported doc cleans only your **local** wiki artifacts;
  the document in PageIndex Cloud is left untouched.

> **Page windowing:** PageIndex caps a single page-content request at 1000 pages.
> OpenKB fetches in 1000-page windows and stops when a window comes back short, so
> documents of any length (including 700+ page books) import completely.

### Finding a `doc_id`

List what's in your cloud collection with the PageIndex client:

```python
import os
from pageindex import PageIndexClient

client = PageIndexClient(api_key=os.environ["PAGEINDEX_API_KEY"])
col = client.collection()

for doc in col.list_documents():
    print(doc["doc_id"], "—", doc.get("doc_name"))
```

```text
pi-cmn3k8...  — attention-is-all-you-need.pdf
pi-x7f0aa...  — deepseek-r1.pdf
```

Then:

```bash
openkb add --from-pageindex-cloud pi-cmn3k8...
```

---

## An import, end to end

```bash
$ export PAGEINDEX_API_KEY="pi-..."
$ openkb add --from-pageindex-cloud pi-cmn3k8...
Importing from PageIndex Cloud: pi-cmn3k8...
  Fetching structure + OCR pages...
  Compiling concepts and entities...
  [OK] attention-is-all-you-need imported from PageIndex Cloud.

$ openkb add --from-pageindex-cloud pi-cmn3k8...
  [SKIP] Already imported from PageIndex Cloud: pi-cmn3k8...
```

The registry entry it writes (`.openkb/hashes.json`) — note there's no `raw_path`,
because there's no local file:

```json
{
  "name": "attention-is-all-you-need.pdf",
  "doc_name": "attention-is-all-you-need-abc12345",
  "type": "pageindex_cloud",
  "path": "pageindex-cloud:pi-cmn3k8...",
  "source_path": "wiki/sources/attention-is-all-you-need-abc12345.json",
  "doc_id": "pi-cmn3k8..."
}
```

After import, the imported doc behaves like any other document — `query`, `chat`,
`recompile`, `visualize`, and `skill new` all see it.
