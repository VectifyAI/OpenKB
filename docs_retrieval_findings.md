# Retrieval-plan prototype — benchmark findings (2026-06-09)

Ground truth: each summary's `[[concepts/X]]` links = concepts that doc used.
recall@K = fraction of a doc's linked concepts present in the top-K retrieved
when querying with the doc summary. Corpus: 335 concepts, 162 summaries w/ links.

| K  | TF-IDF | Embeddings (text-embedding-3-small) | prompt size |
|----|--------|-------------------------------------|-------------|
| 20 | 0.790  | 0.666                               | 6% of full  |
| 40 | 0.897  | 0.785                               | 12% of full |

**Conclusion:** dependency-free TF-IDF is the better default here — briefs are
LLM-generated and lexically overlap the summaries heavily, so lexical ranking
wins, and it costs nothing (no embedding API/latency per doc). The embedding
ranker is kept as an option (`select_relevant_briefs_embed`) for corpora with
more paraphrase/synonym drift, or for a future hybrid. K=40 recovers ~90% of
relevant concepts at 12% of the full-inject prompt size.

Reproduce: `OPENKB_KB=/path/to/kb PYTHONPATH=. uv run python scripts/bench_retrieval.py`
