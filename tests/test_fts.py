"""Unit tests for openkb.fts (FTS5 hybrid retrieval pre-filter).

All offline: builds tiny temporary wikis and exercises the index directly,
no LLM involved.
"""

import json

from openkb.fts import (
    _bigramize,
    _fts_expr,
    ensure_index,
    fts_db_path,
    format_hits,
    rebuild_index,
    search,
)


def _make_kb(tmp_path):
    kb_dir = tmp_path / "kb"
    for sub in ("summaries", "concepts", "entities", "sources"):
        (kb_dir / "wiki" / sub).mkdir(parents=True, exist_ok=True)
    return kb_dir


class TestBigramize:
    def test_cjk_run_becomes_overlapping_bigrams(self):
        assert _bigramize("川普政府") == "川普 普政 政府"

    def test_single_cjk_char_kept(self):
        assert _bigramize("中") == "中"

    def test_latin_untouched(self):
        assert _bigramize("DeepSeek v3") == "DeepSeek v3"

    def test_mixed_transforms_cjk_run_in_place(self):
        assert _bigramize("ETA公司") == "ETA公司"


class TestFtsExpr:
    def test_cjk_terms_become_phrases(self):
        and_expr, or_expr, terms = _fts_expr("川普 政府")
        assert '"川普"' in and_expr and '"政府"' in and_expr
        assert " AND " in and_expr and " OR " in or_expr
        assert terms == ["川普", "政府"]

    def test_latin_gets_prefix_star(self):
        and_expr, _, _ = _fts_expr("transformer")
        assert and_expr == "transformer*"

    def test_mixed_token_is_adjacency_phrase(self):
        and_expr, _, _ = _fts_expr("ETA公司")
        assert and_expr == '"ETA 公司"'

    def test_special_chars_stripped(self):
        and_expr, _, _ = _fts_expr('"豁免"(2025)')
        assert and_expr == '"豁免 2025"'


class TestIndexAndSearch:
    def _seed(self, kb_dir):
        (kb_dir / "wiki" / "entities" / "feras.md").write_text(
            "---\nsee also: [[concepts/eta]]\n---\n"
            "# Feras Khatib\n费拉斯·哈提布是 ETA 董事长，曾在美国海军情报部门服役。\n",
            encoding="utf-8",
        )
        (kb_dir / "wiki" / "summaries" / "deepseek-loi.md").write_text(
            "# DeepSeek 意向书\nETA 谋求 DeepSeek 技术在北美与欧盟的独家许可授权。\n",
            encoding="utf-8",
        )
        (kb_dir / "wiki" / "sources" / "long-doc.json").write_text(
            json.dumps(
                [
                    {"page": 1, "content": "封面与目录。"},
                    {"page": 7, "content": "卢特尼克提出芯片豁免的五项要求。"},
                ]
            ),
            encoding="utf-8",
        )

    def test_rebuild_and_cjk_search(self, tmp_path):
        kb_dir = _make_kb(tmp_path)
        self._seed(kb_dir)
        files, rows = rebuild_index(kb_dir)
        assert files == 3 and rows == 4  # 2 md rows + 2 page rows

        hits = search(kb_dir, "董事长")
        assert hits and hits[0]["path"] == "entities/feras.md"
        assert hits[0]["kind"] == "entity"
        assert "费拉斯" in hits[0]["snippet"]

    def test_frontmatter_stripped_from_snippets(self, tmp_path):
        kb_dir = _make_kb(tmp_path)
        self._seed(kb_dir)
        rebuild_index(kb_dir)
        hits = search(kb_dir, "eta")
        assert hits
        assert "see also" not in hits[0]["snippet"]

    def test_long_doc_page_hit(self, tmp_path):
        kb_dir = _make_kb(tmp_path)
        self._seed(kb_dir)
        rebuild_index(kb_dir)
        hits = search(kb_dir, "豁免 五项")
        assert hits
        page_hit = next(h for h in hits if h["kind"] == "page")
        assert page_hit["path"] == "sources/long-doc.json" and page_hit["page"] == 7

    def test_multi_term_and_with_or_fallback(self, tmp_path):
        kb_dir = _make_kb(tmp_path)
        self._seed(kb_dir)
        rebuild_index(kb_dir)
        # AND hits: both terms in one doc
        assert search(kb_dir, "DeepSeek 独家许可")[0]["path"] == "summaries/deepseek-loi.md"
        # No AND hit anywhere -> OR fallback still returns something
        hits = search(kb_dir, "独家许可 卢特尼克")
        assert len(hits) >= 2

    def test_no_hit_message(self, tmp_path):
        kb_dir = _make_kb(tmp_path)
        self._seed(kb_dir)
        rebuild_index(kb_dir)
        out = format_hits(search(kb_dir, "zqxwv 火星鲱鱼"))
        assert "No full-text hits" in out

    def test_incremental_update_picks_up_new_file(self, tmp_path):
        kb_dir = _make_kb(tmp_path)
        self._seed(kb_dir)
        rebuild_index(kb_dir)
        (kb_dir / "wiki" / "entities" / "jester.md").write_text(
            "# Norman Jester\n环球交换电信负责人，在墨西哥创建全国性电信运营商。\n",
            encoding="utf-8",
        )
        # ensure_index (not rebuild) must notice the new file
        hits = search(kb_dir, "墨西哥")
        assert any(h["path"] == "entities/jester.md" for h in hits)

    def test_ensure_index_creates_db(self, tmp_path):
        kb_dir = _make_kb(tmp_path)
        self._seed(kb_dir)
        db = ensure_index(kb_dir)
        assert db == fts_db_path(kb_dir) and db.exists()
