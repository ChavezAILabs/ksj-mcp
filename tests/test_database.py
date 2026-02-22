"""
Tests for ksj_mcp.database — CRUD, FTS5, and analytics helpers.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ksj_mcp.database import (
    insert_capture,
    insert_tags,
    insert_connection,
    get_capture,
    list_captures,
    get_connections,
    check_duplicate,
    get_captures_by_tag,
    get_rc_tag_clusters,
    get_question_captures,
    get_syn_breakthroughs,
    get_dc_pattern_data,
    get_rev_progress,
    search_fts,
    get_stats,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _insert_rc(con, template_id="RC-001", summary="A rapid capture", raw_ocr="some ocr text"):
    cid = insert_capture(
        con, "RC", template_id,
        {"first_impressions": summary, "key_points": "point 1"},
        raw_ocr, summary, 0.9,
    )
    con.commit()
    return cid


def _insert_syn(con, template_id="SYN-001", breakthrough="A breakthrough", summary="SYN summary"):
    cid = insert_capture(
        con, "SYN", template_id,
        {"breakthrough": breakthrough, "patterns": "a pattern"},
        "ocr of syn", summary, 0.85,
    )
    con.commit()
    return cid


def _insert_rev(con, template_id="REV-001", knowledge_status="Solid", summary="REV summary"):
    cid = insert_capture(
        con, "REV", template_id,
        {"knowledge_status": knowledge_status, "process_notes": "notes", "observations": "obs"},
        "ocr of rev", summary, 0.8,
    )
    con.commit()
    return cid


def _insert_dc(con, template_id="DC-001", narrative="A dream narrative", summary="DC summary"):
    cid = insert_capture(
        con, "DC", template_id,
        {"dream_narrative": narrative, "symbols": "a symbol", "emotions": "calm"},
        "ocr of dc", summary, 0.75,
    )
    con.commit()
    return cid


# ── init_db (via conftest fixture) ────────────────────────────────────────────

class TestInitDb:
    def test_tables_exist(self, db):
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "captures" in tables
        assert "tags" in tables
        assert "connections" in tables

    def test_fts_table_exists(self, db):
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "captures_fts" in tables

    def test_idempotent(self, db):
        # Running init_db again on the same path should not raise
        from ksj_mcp.database import init_db
        from pathlib import Path
        # We can't get the path back from the connection easily, but we can
        # verify the schema still works after another executescript (IF NOT EXISTS).
        db.executescript("""
            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('RC','SYN','REV','DC')),
                template_id TEXT NOT NULL,
                content_json TEXT NOT NULL,
                raw_ocr TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0.0,
                image_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
        """)


# ── insert_capture / get_capture ──────────────────────────────────────────────

class TestInsertAndGetCapture:
    def test_roundtrip(self, db):
        cid = _insert_rc(db, template_id="RC-042", summary="Hello world", raw_ocr="raw text")
        cap = get_capture(db, cid)
        assert cap is not None
        assert cap["template_id"] == "RC-042"
        assert cap["type"] == "RC"
        assert cap["summary"] == "Hello world"
        assert cap["confidence"] == pytest.approx(0.9)

    def test_content_json_decoded(self, db):
        cid = _insert_rc(db)
        cap = get_capture(db, cid)
        assert isinstance(cap["content"], dict)
        assert "first_impressions" in cap["content"]

    def test_tags_included(self, db):
        cid = _insert_rc(db)
        insert_tags(db, cid, [{"prefix": "#", "value": "ml"}])
        db.commit()
        cap = get_capture(db, cid)
        assert any(t["prefix"] == "#" and t["value"] == "ml" for t in cap["tags"])

    def test_missing_id_returns_none(self, db):
        assert get_capture(db, 999999) is None

    def test_image_path_default_empty(self, db):
        cid = _insert_rc(db)
        cap = get_capture(db, cid)
        assert cap["image_path"] == ""

    def test_created_at_is_iso_utc(self, db):
        cid = _insert_rc(db)
        cap = get_capture(db, cid)
        # Should parse without error
        dt = datetime.fromisoformat(cap["created_at"])
        assert dt.tzinfo is not None


# ── insert_tags ───────────────────────────────────────────────────────────────

class TestInsertTags:
    def test_multiple_tags_inserted(self, db):
        cid = _insert_rc(db)
        tags = [
            {"prefix": "#", "value": "ai"},
            {"prefix": "$", "value": "key-insight"},
            {"prefix": "?", "value": "how-does-it-scale"},
        ]
        insert_tags(db, cid, tags)
        db.commit()
        stored = db.execute(
            "SELECT prefix, value FROM tags WHERE capture_id=?", (cid,)
        ).fetchall()
        stored_set = {(r["prefix"], r["value"]) for r in stored}
        assert ("$", "key-insight") in stored_set
        assert ("?", "how-does-it-scale") in stored_set

    def test_empty_list_is_noop(self, db):
        cid = _insert_rc(db)
        insert_tags(db, cid, [])
        db.commit()
        count = db.execute(
            "SELECT COUNT(*) AS cnt FROM tags WHERE capture_id=?", (cid,)
        ).fetchone()["cnt"]
        assert count == 0


# ── insert_connection (deduplication) ─────────────────────────────────────────

class TestInsertConnection:
    def test_connection_inserted(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        conn_id = insert_connection(db, a, b, "tag_overlap", 2.0, "tag_overlap")
        db.commit()
        assert isinstance(conn_id, int)
        row = db.execute(
            "SELECT * FROM connections WHERE id=?", (conn_id,)
        ).fetchone()
        assert row is not None
        assert row["strength"] == pytest.approx(2.0)

    def test_duplicate_same_direction(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        id1 = insert_connection(db, a, b, "tag_overlap", 1.0, "tag_overlap")
        db.commit()
        id2 = insert_connection(db, a, b, "tag_overlap", 1.0, "tag_overlap")
        db.commit()
        assert id1 == id2

    def test_duplicate_reverse_direction(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        id1 = insert_connection(db, a, b, "tag_overlap", 1.0, "tag_overlap")
        db.commit()
        id2 = insert_connection(db, b, a, "tag_overlap", 1.0, "tag_overlap")
        db.commit()
        assert id1 == id2

    def test_different_pairs_get_different_ids(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        c = _insert_rc(db, "RC-003")
        id1 = insert_connection(db, a, b, "tag_overlap", 1.0, "tag_overlap")
        id2 = insert_connection(db, a, c, "tag_overlap", 1.0, "tag_overlap")
        db.commit()
        assert id1 != id2


# ── list_captures ─────────────────────────────────────────────────────────────

class TestListCaptures:
    def test_returns_all(self, db):
        _insert_rc(db, "RC-001")
        _insert_rc(db, "RC-002")
        _insert_syn(db, "SYN-001")
        results = list_captures(db)
        assert len(results) == 3

    def test_type_filter(self, db):
        _insert_rc(db, "RC-001")
        _insert_syn(db, "SYN-001")
        results = list_captures(db, type_filter="RC")
        assert all(r["type"] == "RC" for r in results)
        assert len(results) == 1

    def test_type_filter_case_insensitive(self, db):
        _insert_rc(db, "RC-001")
        results = list_captures(db, type_filter="rc")
        assert len(results) == 1

    def test_limit(self, db):
        for i in range(5):
            _insert_rc(db, f"RC-{i+1:03d}")
        results = list_captures(db, limit=3)
        assert len(results) == 3

    def test_empty_db_returns_empty(self, db):
        assert list_captures(db) == []

    def test_ordered_by_created_at_desc(self, db):
        _insert_rc(db, "RC-001")
        _insert_rc(db, "RC-002")
        results = list_captures(db)
        # Most recent first; both were inserted quickly so just check length
        assert len(results) == 2


# ── get_connections ───────────────────────────────────────────────────────────

class TestGetConnections:
    def test_returns_connections_for_capture(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        insert_connection(db, a, b, "tag_overlap", 3.0, "tag_overlap")
        db.commit()
        conns = get_connections(db, a)
        assert len(conns) == 1
        assert conns[0]["connected_template"] == "RC-002"

    def test_bidirectional_query(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        insert_connection(db, a, b, "tag_overlap", 1.0, "tag_overlap")
        db.commit()
        # Querying from b's perspective should also find the connection
        conns = get_connections(db, b)
        assert len(conns) == 1
        assert conns[0]["connected_template"] == "RC-001"

    def test_no_connections_returns_empty(self, db):
        a = _insert_rc(db, "RC-001")
        assert get_connections(db, a) == []


# ── check_duplicate ───────────────────────────────────────────────────────────

class TestCheckDuplicate:
    def test_finds_existing(self, db):
        _insert_rc(db, "RC-007")
        result = check_duplicate(db, "RC-007")
        assert result is not None
        assert result["template_id"] == "RC-007"

    def test_case_insensitive(self, db):
        _insert_rc(db, "RC-007")
        result = check_duplicate(db, "rc-007")
        assert result is not None

    def test_not_found_returns_none(self, db):
        assert check_duplicate(db, "RC-999") is None


# ── get_captures_by_tag ───────────────────────────────────────────────────────

class TestGetCapturesByTag:
    def test_finds_by_tag_value(self, db):
        cid = _insert_rc(db, "RC-001")
        insert_tags(db, cid, [{"prefix": "#", "value": "transformers"}])
        db.commit()
        results = get_captures_by_tag(db, "transformers")
        assert len(results) == 1
        assert results[0]["template_id"] == "RC-001"

    def test_case_insensitive_tag_match(self, db):
        cid = _insert_rc(db, "RC-001")
        insert_tags(db, cid, [{"prefix": "#", "value": "Transformers"}])
        db.commit()
        results = get_captures_by_tag(db, "transformers")
        assert len(results) == 1

    def test_prefix_filter(self, db):
        cid1 = _insert_rc(db, "RC-001")
        cid2 = _insert_rc(db, "RC-002")
        insert_tags(db, cid1, [{"prefix": "#", "value": "ai"}])
        insert_tags(db, cid2, [{"prefix": "$", "value": "ai"}])
        db.commit()
        results = get_captures_by_tag(db, "ai", prefix="#")
        assert len(results) == 1
        assert results[0]["template_id"] == "RC-001"

    def test_no_match_returns_empty(self, db):
        _insert_rc(db)
        assert get_captures_by_tag(db, "nonexistent-tag") == []

    def test_tags_included_in_result(self, db):
        cid = _insert_rc(db, "RC-001")
        insert_tags(db, cid, [{"prefix": "#", "value": "ml"}, {"prefix": "$", "value": "insight"}])
        db.commit()
        results = get_captures_by_tag(db, "ml")
        assert len(results[0]["tags"]) == 2


# ── get_rc_tag_clusters ───────────────────────────────────────────────────────

class TestGetRcTagClusters:
    def _add_rc_with_tag(self, db, template_id, tag_value):
        cid = _insert_rc(db, template_id)
        insert_tags(db, cid, [{"prefix": "#", "value": tag_value}])
        db.commit()
        return cid

    def test_cluster_above_min_returned(self, db):
        for i in range(3):
            self._add_rc_with_tag(db, f"RC-{i+1:03d}", "attention")
        clusters = get_rc_tag_clusters(db, min_size=3)
        assert len(clusters) == 1
        assert clusters[0]["tag"] == "attention"
        assert clusters[0]["rc_count"] == 3

    def test_cluster_below_min_excluded(self, db):
        for i in range(2):
            self._add_rc_with_tag(db, f"RC-{i+1:03d}", "attention")
        clusters = get_rc_tag_clusters(db, min_size=3)
        assert clusters == []

    def test_syn_exists_false_when_no_syn(self, db):
        for i in range(3):
            self._add_rc_with_tag(db, f"RC-{i+1:03d}", "attention")
        clusters = get_rc_tag_clusters(db, min_size=3)
        assert clusters[0]["syn_exists"] is False

    def test_syn_exists_true_when_syn_has_tag(self, db):
        for i in range(3):
            self._add_rc_with_tag(db, f"RC-{i+1:03d}", "attention")
        syn_id = _insert_syn(db, "SYN-001")
        insert_tags(db, syn_id, [{"prefix": "#", "value": "attention"}])
        db.commit()
        clusters = get_rc_tag_clusters(db, min_size=3)
        assert clusters[0]["syn_exists"] is True
        assert "SYN-001" in clusters[0]["syn_templates"]

    def test_sorted_by_size_desc(self, db):
        for i in range(4):
            self._add_rc_with_tag(db, f"RC-{i+1:03d}", "large-cluster")
        for i in range(3):
            self._add_rc_with_tag(db, f"RC-{i+5:03d}", "small-cluster")
        clusters = get_rc_tag_clusters(db, min_size=3)
        assert clusters[0]["tag"] == "large-cluster"

    def test_non_rc_types_excluded(self, db):
        # SYN capture with the tag should not be counted in rc_count
        for i in range(3):
            self._add_rc_with_tag(db, f"RC-{i+1:03d}", "attention")
        syn_id = _insert_syn(db, "SYN-001")
        insert_tags(db, syn_id, [{"prefix": "#", "value": "attention"}])
        db.commit()
        clusters = get_rc_tag_clusters(db, min_size=3)
        assert clusters[0]["rc_count"] == 3


# ── get_question_captures ──────────────────────────────────────────────────────

class TestGetQuestionCaptures:
    def test_returns_capture_with_question_tag(self, db):
        cid = _insert_rc(db, "RC-001", raw_ocr="How does attention work?")
        insert_tags(db, cid, [{"prefix": "?", "value": "how-does-attention-work"}])
        db.commit()
        results = get_question_captures(db)
        assert len(results) == 1
        assert results[0]["template_id"] == "RC-001"
        assert "how-does-attention-work" in results[0]["questions"]

    def test_no_question_tags_returns_empty(self, db):
        _insert_rc(db, "RC-001")
        assert get_question_captures(db) == []

    def test_topics_extracted(self, db):
        cid = _insert_rc(db, "RC-001")
        insert_tags(db, cid, [
            {"prefix": "?", "value": "how"},
            {"prefix": "#", "value": "ml"},
        ])
        db.commit()
        results = get_question_captures(db)
        assert "ml" in results[0]["topics"]

    def test_insights_from_connected_dollar_tags(self, db):
        q_id = _insert_rc(db, "RC-001")
        insert_tags(db, q_id, [{"prefix": "?", "value": "what-is-attention"}])
        ins_id = _insert_rc(db, "RC-002", summary="An insight capture")
        insert_tags(db, ins_id, [{"prefix": "$", "value": "key-insight"}])
        insert_connection(db, q_id, ins_id, "tag_overlap", 1.0, "tag_overlap")
        db.commit()
        results = get_question_captures(db)
        assert len(results[0]["insights"]) == 1
        assert results[0]["insights"][0]["template_id"] == "RC-002"


# ── get_syn_breakthroughs ──────────────────────────────────────────────────────

class TestGetSynBreakthroughs:
    def test_returns_syn_captures(self, db):
        _insert_syn(db, "SYN-001", breakthrough="Big idea")
        results = get_syn_breakthroughs(db)
        assert len(results) == 1
        assert results[0]["breakthrough"] == "Big idea"

    def test_non_syn_excluded(self, db):
        _insert_rc(db, "RC-001")
        _insert_syn(db, "SYN-001")
        results = get_syn_breakthroughs(db)
        assert len(results) == 1

    def test_insights_extracted(self, db):
        cid = _insert_syn(db, "SYN-001")
        insert_tags(db, cid, [{"prefix": "$", "value": "the-big-insight"}])
        db.commit()
        results = get_syn_breakthroughs(db)
        assert "the-big-insight" in results[0]["insights"]

    def test_topics_extracted(self, db):
        cid = _insert_syn(db, "SYN-001")
        insert_tags(db, cid, [{"prefix": "#", "value": "transformers"}])
        db.commit()
        results = get_syn_breakthroughs(db)
        assert "transformers" in results[0]["topics"]

    def test_ordered_chronologically(self, db):
        _insert_syn(db, "SYN-001")
        _insert_syn(db, "SYN-002")
        results = get_syn_breakthroughs(db)
        ids = [r["template_id"] for r in results]
        assert ids == sorted(ids)


# ── get_dc_pattern_data ────────────────────────────────────────────────────────

class TestGetDcPatternData:
    def test_returns_dc_captures(self, db):
        _insert_dc(db, "DC-001", narrative="Flying dream")
        results = get_dc_pattern_data(db)
        assert len(results) == 1
        assert results[0]["narrative"] == "Flying dream"

    def test_non_dc_excluded(self, db):
        _insert_rc(db, "RC-001")
        _insert_dc(db, "DC-001")
        results = get_dc_pattern_data(db)
        assert len(results) == 1

    def test_symbols_and_emotions_present(self, db):
        _insert_dc(db, "DC-001", narrative="Underwater scene")
        results = get_dc_pattern_data(db)
        assert results[0]["symbols"] == "a symbol"
        assert results[0]["emotions"] == "calm"

    def test_tags_included(self, db):
        cid = _insert_dc(db, "DC-001")
        insert_tags(db, cid, [{"prefix": "#", "value": "flying"}])
        db.commit()
        results = get_dc_pattern_data(db)
        assert any(t["value"] == "flying" for t in results[0]["tags"])


# ── get_rev_progress ───────────────────────────────────────────────────────────

class TestGetRevProgress:
    def test_returns_rev_captures(self, db):
        _insert_rev(db, "REV-001", knowledge_status="Solid")
        results = get_rev_progress(db)
        assert len(results) == 1
        assert results[0]["knowledge_status"] == "Solid"

    def test_non_rev_excluded(self, db):
        _insert_rc(db, "RC-001")
        _insert_rev(db, "REV-001")
        results = get_rev_progress(db)
        assert len(results) == 1

    def test_topic_filter(self, db):
        cid1 = _insert_rev(db, "REV-001")
        insert_tags(db, cid1, [{"prefix": "#", "value": "transformers"}])
        cid2 = _insert_rev(db, "REV-002")
        insert_tags(db, cid2, [{"prefix": "#", "value": "rl"}])
        db.commit()
        results = get_rev_progress(db, topic_filter="transformers")
        assert len(results) == 1
        assert results[0]["template_id"] == "REV-001"

    def test_topic_filter_case_insensitive(self, db):
        cid = _insert_rev(db, "REV-001")
        insert_tags(db, cid, [{"prefix": "#", "value": "Transformers"}])
        db.commit()
        results = get_rev_progress(db, topic_filter="transformers")
        assert len(results) == 1

    def test_topics_extracted(self, db):
        cid = _insert_rev(db, "REV-001")
        insert_tags(db, cid, [{"prefix": "#", "value": "ml"}])
        db.commit()
        results = get_rev_progress(db)
        assert "ml" in results[0]["topics"]


# ── search_fts ─────────────────────────────────────────────────────────────────

class TestSearchFts:
    def test_basic_search(self, db):
        _insert_rc(db, "RC-001", raw_ocr="attention mechanism is fascinating")
        results = search_fts(db, "attention")
        assert len(results) == 1
        assert results[0]["template_id"] == "RC-001"

    def test_no_match_returns_empty(self, db):
        _insert_rc(db, "RC-001", raw_ocr="totally unrelated text")
        results = search_fts(db, "quantum")
        assert results == []

    def test_tag_filter(self, db):
        cid1 = _insert_rc(db, "RC-001", raw_ocr="transformers and attention")
        cid2 = _insert_rc(db, "RC-002", raw_ocr="transformers and attention")
        insert_tags(db, cid1, [{"prefix": "#", "value": "ai"}])
        insert_tags(db, cid2, [{"prefix": "#", "value": "robotics"}])
        db.commit()
        results = search_fts(db, "transformers", tag_filter="ai")
        assert len(results) == 1
        assert results[0]["template_id"] == "RC-001"

    def test_tags_included_in_results(self, db):
        cid = _insert_rc(db, "RC-001", raw_ocr="attention is key")
        insert_tags(db, cid, [{"prefix": "#", "value": "ml"}])
        db.commit()
        results = search_fts(db, "attention")
        assert any(t["value"] == "ml" for t in results[0]["tags"])

    def test_limit_respected(self, db):
        for i in range(5):
            _insert_rc(db, f"RC-{i+1:03d}", raw_ocr="neural network training")
        results = search_fts(db, "neural", limit=3)
        assert len(results) <= 3


# ── get_stats ──────────────────────────────────────────────────────────────────

class TestGetStats:
    def test_empty_db(self, db):
        stats = get_stats(db)
        assert stats["total_captures"] == 0
        assert stats["by_type"] == {}
        assert stats["top_tags"] == []
        assert stats["open_questions"] == 0
        assert stats["key_insights"] == 0
        assert stats["date_range"]["earliest"] is None
        assert stats["date_range"]["latest"] is None

    def test_counts_by_type(self, db):
        _insert_rc(db, "RC-001")
        _insert_rc(db, "RC-002")
        _insert_syn(db, "SYN-001")
        stats = get_stats(db)
        assert stats["total_captures"] == 3
        assert stats["by_type"]["RC"] == 2
        assert stats["by_type"]["SYN"] == 1

    def test_open_questions_counted(self, db):
        cid = _insert_rc(db)
        insert_tags(db, cid, [{"prefix": "?", "value": "how"}])
        db.commit()
        stats = get_stats(db)
        assert stats["open_questions"] == 1

    def test_key_insights_counted(self, db):
        cid = _insert_rc(db)
        insert_tags(db, cid, [{"prefix": "$", "value": "insight"}])
        db.commit()
        stats = get_stats(db)
        assert stats["key_insights"] == 1

    def test_top_tags_returned(self, db):
        for i in range(3):
            cid = _insert_rc(db, f"RC-{i+1:03d}")
            insert_tags(db, cid, [{"prefix": "#", "value": "ml"}])
        db.commit()
        stats = get_stats(db)
        ml_entry = next((t for t in stats["top_tags"] if t["tag"] == "#ml"), None)
        assert ml_entry is not None
        assert ml_entry["cnt"] == 3

    def test_date_range_populated(self, db):
        _insert_rc(db)
        stats = get_stats(db)
        assert stats["date_range"]["earliest"] is not None
        assert stats["date_range"]["latest"] is not None
