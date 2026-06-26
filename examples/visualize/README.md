# Visualize

`openkb visualize` renders your wiki's `[[wikilink]]` graph as a self-contained,
interactive HTML page — a fast way to *see* how knowledge connects across the
documents you've ingested.

> **Real artifact in this folder:** [`graph.html`](graph.html) — the graph
> generated from one paper (13 nodes, 96 edges). Open it in a browser and switch
> between the **3D / mind-map / radial** modes; click a node to inspect it.

```bash
openkb visualize
```

```text
Graph written to /Users/you/my-kb/output/visualize/graph.html  (13 nodes, 96 edges)
# opens in your default browser
```

By default it opens the page in your browser after generating. For headless
environments (CI, a remote box), skip the launch:

```bash
openkb visualize --no-open
```

> **Prerequisite:** you need a compiled wiki. With nothing ingested yet, it tells
> you to run `openkb add` first. The [`graph.html`](graph.html) here was built from
> the wiki in [`../commands/sample-wiki/`](../commands/sample-wiki/).

---

## What's in the graph

- **Nodes** — every page under `wiki/summaries/`, `wiki/concepts/`, and
  `wiki/entities/`. Each carries its label, type, description, sources, and
  in/out degree (used to size it).
- **Edges** — directed `[[wikilink]]` references between pages. Self-loops are
  dropped and duplicates collapsed.
- **Types** — taken from a page's frontmatter or its directory, and used to color
  nodes and drive the legend filter.

The single HTML file embeds the graph data and all rendering code, so you can
share it or commit it without any external dependencies.

---

## Three view modes

Switch between these from buttons in the page:

| Mode | Layout |
| --- | --- |
| **3D** *(default)* | Force-directed "nebula" in 3D — orbit, zoom, and drag-to-pin nodes. |
| **mind-map** | Horizontal tree: OpenKB → documents (summaries) → concepts. |
| **radial** | OpenKB at the hub, documents on spokes, concepts radiating out; zoom/pan. |

The page also has search (filter by label), a type legend you can toggle, a
spacing slider, and a node inspector — click a node to see its description,
sources, and links.

---

## Where it fits

`visualize` is read-only and re-runs cheaply, so it pairs well with the rest of
the loop:

```bash
openkb add ~/papers/        # ingest a batch
openkb lint --fix           # repair any dangling links so the graph is clean
openkb visualize            # see the shape of what you've built
```

A sparse graph with many orphans usually means documents aren't sharing concepts
yet — add more in the same domain and watch the concept hubs grow as knowledge
compounds.
