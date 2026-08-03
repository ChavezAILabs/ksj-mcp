"""
Tests for the OCR correction path: corrected_ocr column, update_capture_correction,
reference resolution preferring corrected text, and the additive migration.
"""

import json
import sqlite3
from pathlib import Path

from ksj_mcp.connections import build_connections, find_reference_connections
from ksj_mcp.database import (
    get_capture,
    get_connection,
    insert_capture,
    insert_tags,
    migrate_add_corrected_ocr,
    migrate_v3,
    migrate_v31,
    search_fts,
    update_capture_correction,
)
from ksj_mcp.templates import parse_template


def _insert(con, template_id="RC-001", raw_ocr="some ocr text", type_="RC", tags=None):
    cid = insert_capture(
        con, type_, template_id,
        {"first_impressions": raw_ocr[:50]},
        raw_ocr, raw_ocr[:50], 0.9,
    )
    if tags:
        insert_tags(con, cid, tags)
    con.commit()
    return cid


def _correct(con, capture_id, text):
    """Mirror the correct_ocr tool: parse, apply, rebuild connections."""
    cap = get_capture(con, capture_id)
    parsed = parse_template(cap["type"], text)
    ok = update_capture_correction(
        con, capture_id,
        corrected_text=text,
        content=parsed["fields"],
        summary=parsed["summary"],
        tags=parsed["tags"],
    )
    if ok:
        build_connections(con, capture_id)
    return ok


class TestUpdateCaptureCorrection:
    def test_raw_ocr_preserved_and_corrected_stored(self, db):
        cid = _insert(db, raw_ocr="garbled tesseract noise")
        _correct(db, cid, "First Impressions\nclean corrected text #topic")
        row = db.execute(
            "SELECT raw_ocr, corrected_ocr FROM captures WHERE id=?", (cid,)
        ).fetchone()
        assert row["raw_ocr"] == "garbled tesseract noise"
        assert "clean corrected text" in row["corrected_ocr"]

    def test_tags_replaced(self, db):
        cid = _insert(db, raw_ocr="text #oldtag", tags=[{"prefix": "#", "value": "oldtag"}])
        _correct(db, cid, "text #newtag")
        tags = db.execute(
            "SELECT prefix, value FROM tags WHERE capture_id=?", (cid,)
        ).fetchall()
        values = {t["value"] for t in tags}
        assert "newtag" in values
        assert "oldtag" not in values

    def test_missing_capture_returns_false(self, db):
        assert update_capture_correction(db, 9999, "x", {}, "x", []) is False

    def test_fts_searches_corrected_not_raw(self, db):
        cid = _insert(db, raw_ocr="zebra elephant giraffe")
        _correct(db, cid, "sunrise mountain river")
        assert search_fts(db, "sunrise") != []
        assert search_fts(db, "zebra") == []

    def test_stale_tag_overlap_edges_removed(self, db):
        a = _insert(db, "RC-001", raw_ocr="a #shared", tags=[{"prefix": "#", "value": "shared"}])
        b = _insert(db, "RC-002", raw_ocr="b #shared", tags=[{"prefix": "#", "value": "shared"}])
        build_connections(db, b)  # creates the tag_overlap edge
        _correct(db, a, "totally different now #solo")
        edges = db.execute(
            "SELECT * FROM connections WHERE type='tag_overlap'"
        ).fetchall()
        assert edges == []

    def test_inbound_reference_preserved(self, db):
        a = _insert(db, "RC-001", raw_ocr="original page text")
        c = _insert(db, "RC-003", raw_ocr="see @RC-001 for details")
        build_connections(db, c)  # c → a reference
        _correct(db, a, "corrected page text, no refs")
        refs = db.execute(
            "SELECT source_id, target_id FROM connections WHERE type='reference'"
        ).fetchall()
        assert [(r["source_id"], r["target_id"]) for r in refs] == [(c, a)]


class TestReferenceScanPrefersCorrected:
    def test_reference_found_in_corrected_text(self, db):
        _insert(db, "RC-002", raw_ocr="target page")
        a = _insert(db, "RC-001", raw_ocr="no references here")
        _correct(db, a, "actually this builds on @RC-002")
        refs = find_reference_connections(db, a)
        assert len(refs) == 1
        assert refs[0]["template_id"] == "RC-002"

    def test_reference_dropped_by_correction(self, db):
        _insert(db, "RC-002", raw_ocr="target page")
        a = _insert(db, "RC-001", raw_ocr="mentions @RC-002 wrongly")
        _correct(db, a, "the OCR hallucinated that reference")
        assert find_reference_connections(db, a) == []


_OLD_SCHEMA = """
    CREATE TABLE captures (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        type        TEXT NOT NULL CHECK(type IN ('RC','SYN','REV','DC','AIEX')),
        template_id TEXT NOT NULL,
        content_json TEXT NOT NULL,
        raw_ocr     TEXT NOT NULL,
        summary     TEXT NOT NULL DEFAULT '',
        confidence  REAL NOT NULL DEFAULT 0.0,
        image_path  TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL
    );
    CREATE TABLE tags (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        capture_id  INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
        prefix      TEXT NOT NULL,
        value       TEXT NOT NULL
    );
    CREATE TABLE connections (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id   INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
        target_id   INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
        type        TEXT NOT NULL,
        strength    REAL NOT NULL DEFAULT 1.0,
        method      TEXT NOT NULL
    );
    CREATE VIRTUAL TABLE captures_fts USING fts5(
        raw_ocr, summary, content='captures', content_rowid='id'
    );
    CREATE TRIGGER captures_fts_insert AFTER INSERT ON captures BEGIN
        INSERT INTO captures_fts(rowid, raw_ocr, summary)
        VALUES (new.id, new.raw_ocr, new.summary);
    END;
    CREATE TRIGGER captures_fts_delete AFTER DELETE ON captures BEGIN
        INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
        VALUES ('delete', old.id, old.raw_ocr, old.summary);
    END;
    CREATE TRIGGER captures_fts_update AFTER UPDATE ON captures BEGIN
        INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
        VALUES ('delete', old.id, old.raw_ocr, old.summary);
        INSERT INTO captures_fts(rowid, raw_ocr, summary)
        VALUES (new.id, new.raw_ocr, new.summary);
    END;
"""


class TestMigrateAddCorrectedOcr:
    def _make_old_db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "old.db"
        con = sqlite3.connect(db_path)
        con.executescript(_OLD_SCHEMA)
        con.execute(
            """INSERT INTO captures
               (type, template_id, content_json, raw_ocr, summary, confidence, image_path, created_at)
               VALUES ('RC', 'RC-001', ?, 'legacy raw text', 'legacy', 0.8, '', '2026-01-01T00:00:00')""",
            (json.dumps({"first_impressions": "legacy"}),),
        )
        con.commit()
        con.close()
        return db_path

    def test_adds_column_and_updates_triggers(self, tmp_path):
        db_path = self._make_old_db(tmp_path)
        migrate_add_corrected_ocr(db_path)

        con = get_connection(db_path)
        cols = [r[1] for r in con.execute("PRAGMA table_info(captures)").fetchall()]
        assert "corrected_ocr" in cols
        trig = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='captures_fts_update'"
        ).fetchone()
        assert "corrected_ocr" in trig["sql"]
        con.close()

    def test_idempotent(self, tmp_path):
        db_path = self._make_old_db(tmp_path)
        migrate_add_corrected_ocr(db_path)
        migrate_add_corrected_ocr(db_path)  # second run is a no-op

    def test_correction_works_after_migration(self, tmp_path):
        # Full startup chain: corrected_ocr migration, then the v3 rebuild
        # (correction needs v3's connection unique index to rebuild edges).
        db_path = self._make_old_db(tmp_path)
        migrate_add_corrected_ocr(db_path)
        assert migrate_v3(db_path) is True
        migrate_v31(db_path)

        con = get_connection(db_path)
        assert search_fts(con, "legacy") != []
        ok = _correct(con, 1, "migrated and corrected content")
        assert ok
        assert search_fts(con, "corrected") != []
        assert search_fts(con, "legacy") == []
        con.close()

    def test_missing_db_is_noop(self, tmp_path):
        migrate_add_corrected_ocr(tmp_path / "does_not_exist.db")
