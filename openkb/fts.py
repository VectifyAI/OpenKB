"""FTS5 hybrid retrieval for OpenKB: a fast BM25-ranked keyword pre-filter
over the whole wiki, complementing the query agent's navigation loop.

The query agent's default strategy (read index.md -> summaries -> concepts ->
sources) is thorough but costs several LLM turns before any content is seen.
This module gives it a millisecond keyword index to jump straight to
candidate pages; the agent still verifies by reading the actual pages.

Design notes:

- Storage: ``{kb_dir}/.openkb/fts.db`` (SQLite FTS5, ``bm25()`` ranking).
- CJK: FTS5's unicode61 tokenizer treats a run of CJK ideographs as ONE
  token, so "川普政府" would only match as a whole. We transform CJK runs
  into overlapping bigrams ("川普 普政 政府") before indexing and apply the
  same transform to query terms (classic CJK bigram trick, no external
  segmenter dependency).
- Long docs (``sources/*.json`` page arrays) are indexed one row per page,
  so a hit tells the agent exactly which ``pages=`` to pass to
  ``get_page_content``.
- Freshness: incremental update keyed on (mtime, size) per file, checked on
  every search; a missing db triggers a full build. ``openkb fts rebuild``
  forces a rebuild.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

# CJK Unified Ideographs (+ ext A, compat). Runs of these get bigram-ised.
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff〇]+")

_INDEX_DIRS = ("summaries", "concepts", "entities", "sources", "explorations")
_KIND = {
    "summaries": "summary",
    "concepts": "concept",
    "entities": "entity",
    "sources": "source",
    "explorations": "exploration",
}
_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_RAW_CAP = 30000  # chars of original text kept per row for snippets


def fts_db_path(kb_dir: Path) -> Path:
    return kb_dir / ".openkb" / "fts.db"


def _bigramize(text: str) -> str:
    """Replace each CJK run with space-joined overlapping bigrams."""

    def repl(m: re.Match) -> str:
        s = m.group(0)
        if len(s) == 1:
            return s
        return " ".join(s[i : i + 2] for i in range(len(s) - 1))

    return _CJK_RE.sub(repl, text)


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5("
        "content, raw UNINDEXED, kind UNINDEXED, path UNINDEXED, page UNINDEXED,"
        " tokenize='unicode61')"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS files("
        "path TEXT PRIMARY KEY, mtime REAL, size INTEGER)"
    )
    return con


def _iter_targets(wiki_root: Path):
    """Yield (rel_path, kind, page, text) for every indexable unit."""
    for sub in _INDEX_DIRS:
        d = wiki_root / sub
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            # Drop YAML frontmatter: wikilink lists there drown real mentions
            # in snippets and pollute ranking.
            text = _FRONTMATTER_RE.sub("", text)
            yield (f"{sub}/{f.name}", _KIND[sub], None, text)
    src = wiki_root / "sources"
    if src.is_dir():
        for j in sorted(src.glob("*.json")):
            try:
                data = json.loads(j.read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, list):
                continue
            for entry in data:
                if isinstance(entry, dict) and "page" in entry:
                    yield (
                        f"sources/{j.name}",
                        "page",
                        entry["page"],
                        str(entry.get("content", "")),
                    )


def _row_for(rel: str, kind: str, page, text: str):
    raw = text[:_RAW_CAP]
    return (_bigramize(raw), raw, kind, rel, page)


def _sync(con: sqlite3.Connection, wiki_root: Path) -> tuple[int, int]:
    """Incremental update: returns (files_seen, rows_written)."""
    cur = con.execute("SELECT path, mtime, size FROM files")
    known = {p: (m, s) for p, m, s in cur.fetchall()}
    seen: dict[str, list] = {}
    files_seen = 0
    rows_written = 0

    # Group rows by file so each file is replaced atomically-ish.
    for rel, kind, page, text in _iter_targets(wiki_root):
        seen.setdefault(rel, []).append((rel, kind, page, text))

    wiki_files = {rel: (wiki_root / rel) for rel in seen}
    stale = [p for p in known if p not in seen]

    for rel, rows in seen.items():
        f = wiki_files[rel]
        try:
            st = f.stat()
        except OSError:
            continue
        files_seen += 1
        if known.get(rel) == (st.st_mtime, st.st_size):
            continue
        con.execute("DELETE FROM fts WHERE path = ?", (rel,))
        con.executemany(
            "INSERT INTO fts (content, raw, kind, path, page) VALUES (?,?,?,?,?)",
            [_row_for(rel, kind, page, text) for rel, kind, page, text in rows],
        )
        con.execute(
            "INSERT OR REPLACE INTO files (path, mtime, size) VALUES (?,?,?)",
            (rel, st.st_mtime, st.st_size),
        )
        rows_written += len(rows)

    for rel in stale:
        con.execute("DELETE FROM fts WHERE path = ?", (rel,))
        con.execute("DELETE FROM files WHERE path = ?", (rel,))

    con.commit()
    return files_seen, rows_written


def ensure_index(kb_dir: Path) -> Path:
    """Build the index if missing, else incrementally refresh it."""
    wiki_root = kb_dir / "wiki"
    db_path = fts_db_path(kb_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = _connect(db_path)
    try:
        _sync(con, wiki_root)
    finally:
        con.close()
    return db_path


def rebuild_index(kb_dir: Path) -> tuple[int, int]:
    """Drop and rebuild the index. Returns (files_seen, rows_written)."""
    wiki_root = kb_dir / "wiki"
    db_path = fts_db_path(kb_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = _connect(db_path)
    try:
        con.execute("DROP TABLE IF EXISTS fts")
        con.execute("DROP TABLE IF EXISTS files")
        con.commit()
        con.close()
        con = _connect(db_path)
        stats = _sync(con, wiki_root)
    finally:
        con.close()
    return stats


def _fts_expr(query: str) -> tuple[str, str, list[str]]:
    """Build (AND_expr, OR_expr, raw_terms) from a free-text query."""
    clauses_and: list[str] = []
    clauses_or: list[str] = []
    raw_terms: list[str] = []
    for tok in query.split():
        # Drop anything that is FTS5 syntax (quotes, parens, stars, colons...)
        # so pasted punctuation can never break the MATCH expression.
        tok = re.sub(r"[^\w一-鿿豈-﫿]+", "", tok)
        if not tok:
            continue
        raw_terms.append(tok)
        subs: list[str] = []
        pos = 0
        for m in _CJK_RE.finditer(tok):
            if m.start() > pos:
                subs.append(("latin", tok[pos : m.start()]))
            subs.append(("cjk", m.group(0)))
            pos = m.end()
        if pos < len(tok):
            subs.append(("latin", tok[pos:]))

        if len(subs) > 1:
            # Mixed token (e.g. "ETA公司"): adjacency phrase, no prefix star.
            parts = []
            for kind, s in subs:
                parts.append(_bigramize(s) if kind == "cjk" else s)
            clauses_and.append('"' + " ".join(parts) + '"')
            clauses_or.append('"' + " ".join(parts) + '"')
        elif subs:
            kind, s = subs[0]
            if kind == "cjk":
                expr = '"' + _bigramize(s) + '"'
                clauses_and.append(expr)
                clauses_or.append(expr)
            else:
                clauses_and.append(s + "*")
                clauses_or.append(s + "*")
    return " AND ".join(clauses_and), " OR ".join(clauses_or), raw_terms


def _snippet(raw: str, terms: list[str], width: int = 90) -> str:
    low = raw.lower()
    best = -1
    for t in terms:
        i = low.find(t.lower())
        if i >= 0 and (best < 0 or i < best):
            best = i
    flat = re.sub(r"\s+", " ", raw)
    if best < 0:
        return flat[: width * 2] + ("…" if len(flat) > width * 2 else "")
    start = max(0, best - width)
    end = min(len(flat), best + width)
    # `best` indexes into raw; recompute on flat text approximately.
    i = flat.lower().find(terms[0].lower()) if terms else -1
    if i >= 0:
        start = max(0, i - width)
        end = min(len(flat), i + width)
    return ("…" if start > 0 else "") + flat[start:end] + ("…" if end < len(flat) else "")


def search(kb_dir: Path, query: str, limit: int = 8) -> list[dict]:
    """BM25-ranked keyword search. Returns [{kind, path, page, snippet, score}]."""
    db_path = ensure_index(kb_dir)
    and_expr, or_expr, terms = _fts_expr(query)
    if not and_expr:
        return []
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            "SELECT kind, path, page, raw, bm25(fts) FROM fts"
            " WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
            (and_expr, limit),
        ).fetchall()
        if not rows and or_expr and or_expr != and_expr:
            rows = con.execute(
                "SELECT kind, path, page, raw, bm25(fts) FROM fts"
                " WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT ?",
                (or_expr, limit),
            ).fetchall()
    finally:
        con.close()
    return [
        {
            "kind": k,
            "path": p,
            "page": pg,
            "snippet": _snippet(raw or "", terms),
            "score": round(score, 3),
        }
        for k, p, pg, raw, score in rows
    ]


def format_hits(hits: list[dict]) -> str:
    """Render hits as the text returned by the agent's `search` tool."""
    if not hits:
        return (
            "No full-text hits. Fall back to navigation: read index.md, "
            "then summaries/concepts/entities."
        )
    lines = []
    for i, h in enumerate(hits, 1):
        loc = h["path"] + (f" (page {h['page']})" if h["page"] is not None else "")
        lines.append(f"[{i}] {h['kind']} | {loc}\n    {h['snippet']}")
    lines.append(
        "\nNext: read_file() the .md hits; for 'page' hits call "
        "get_page_content(doc_name, pages) with doc_name = path minus "
        "'sources/' and '.json'."
    )
    return "\n".join(lines)
