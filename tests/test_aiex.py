"""
Tests for AIEX (AI Insight Extraction) feature.

Covers:
  - migrate_add_aiex: handles new DB, already-migrated DB, old-schema DB
  - get_next_aiex_id: sequential numbering
  - commit_aiex tool: stores insights, assigns IDs, parses tags, detects connections
  - extract_insights tool: returns context block (no DB writes)
"""

import json
import sqlite3
from pathlib import Path

import pytest

from ksj_mcp.database import (
    get_connection,
    get_next_aiex_id,
    get_stats,
    init_db,
    insert_capture,
    insert_tags,
    migrate_add_aiex,
    search_fts,
)


# ── migrate_add_aiex ──────────────────────────────────────────────────────────

class TestMigrateAddAiex:
    def test_new_db_already_has_aiex(self, tmp_path):
        """init_db creates AIEX-aware schema; migration is a no-op."""
        db_path = tmp_path / "new.db"
        init_db(db_path)
        migrate_add_aiex(db_path)  # should not raise
        with get_connection(db_path) as con:
            row = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='captures'"
            ).fetchone()
        assert "AIEX" in row["sql"]

    def test_migration_idempotent(self, tmp_path):
        """Calling migrate_add_aiex twice is safe."""
        db_path = tmp_path / "idem.db"
        init_db(db_path)
        migrate_add_aiex(db_path)
        migrate_add_aiex(db_path)  # second call — should not raise

    def test_partial_migration_recovery(self, tmp_path):
        """If a previous migration left _captures_backup behind, it is cleaned up."""
        db_path = tmp_path / "partial.db"
        init_db(db_path)

        # Simulate a partial migration: new captures (AIEX-aware) exists AND
        # _captures_backup was never dropped.
        with get_connection(db_path) as con:
            con.execute(
                """CREATE TABLE _captures_backup (
                    id INTEGER PRIMARY KEY, type TEXT, template_id TEXT,
                    content_json TEXT, raw_ocr TEXT, summary TEXT DEFAULT '',
                    confidence REAL DEFAULT 0.0, image_path TEXT DEFAULT '',
                    created_at TEXT
                )"""
            )
            con.commit()

        migrate_add_aiex(db_path)  # should clean up backup without error

        with get_connection(db_path) as con:
            backup = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_captures_backup'"
            ).fetchone()
            assert backup is None  # backup was dropped

    def test_old_schema_migrated(self, tmp_path):
        """A DB with the old CHECK constraint is migrated to include AIEX."""
        db_path = tmp_path / "old.db"
        # Create old-style schema without AIEX
        with get_connection(db_path) as con:
            con.executescript("""
                CREATE TABLE captures (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    type         TEXT NOT NULL CHECK(type IN ('RC','SYN','REV','DC')),
                    template_id  TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    raw_ocr      TEXT NOT NULL,
                    summary      TEXT NOT NULL DEFAULT '',
                    confidence   REAL NOT NULL DEFAULT 0.0,
                    image_path   TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL
                );
                CREATE TABLE tags (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    prefix     TEXT NOT NULL,
                    value      TEXT NOT NULL
                );
                CREATE TABLE connections (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    target_id INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    type      TEXT NOT NULL,
                    strength  REAL NOT NULL DEFAULT 1.0,
                    method    TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE captures_fts USING fts5(
                    raw_ocr, summary,
                    content='captures', content_rowid='id'
                );
                CREATE TRIGGER captures_fts_insert AFTER INSERT ON captures BEGIN
                    INSERT INTO captures_fts(rowid, raw_ocr, summary)
                    VALUES (new.id, new.raw_ocr, new.summary);
                END;
                -- Seed one RC row to verify data survives
                INSERT INTO captures
                    (type, template_id, content_json, raw_ocr, summary, created_at)
                VALUES ('RC', 'RC-001', '{}', 'old content', 'old summary',
                        '2026-01-01T00:00:00+00:00');
            """)

        migrate_add_aiex(db_path)

        with get_connection(db_path) as con:
            # Schema now allows AIEX
            row = con.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='captures'"
            ).fetchone()
            assert "AIEX" in row["sql"]

            # Existing data preserved
            rc = con.execute("SELECT * FROM captures WHERE template_id='RC-001'").fetchone()
            assert rc is not None
            assert rc["summary"] == "old summary"

            # Can now insert AIEX rows
            con.execute(
                """INSERT INTO captures
                       (type, template_id, content_json, raw_ocr, summary, created_at)
                   VALUES ('AIEX', 'AIEX-001', '{}', 'aiex text', 'aiex summary',
                           '2026-03-01T00:00:00+00:00')"""
            )
            con.commit()
            aiex = con.execute("SELECT * FROM captures WHERE type='AIEX'").fetchone()
            assert aiex is not None


# ── get_next_aiex_id ──────────────────────────────────────────────────────────

class TestGetNextAiexId:
    def test_first_id_is_001(self, db):
        assert get_next_aiex_id(db) == "AIEX-001"

    def test_increments_after_existing(self, db):
        db.execute(
            """INSERT INTO captures
                   (type, template_id, content_json, raw_ocr, summary, created_at)
               VALUES ('AIEX', 'AIEX-001', '{}', 'text', 'summary',
                       '2026-03-01T00:00:00+00:00')"""
        )
        db.commit()
        assert get_next_aiex_id(db) == "AIEX-002"

    def test_handles_gap(self, db):
        """Picks up from the MAX id even if there are gaps."""
        for tid in ("AIEX-001", "AIEX-005"):
            db.execute(
                """INSERT INTO captures
                       (type, template_id, content_json, raw_ocr, summary, created_at)
                   VALUES ('AIEX', ?, '{}', 'x', 's', '2026-03-01T00:00:00+00:00')""",
                (tid,),
            )
        db.commit()
        assert get_next_aiex_id(db) == "AIEX-006"

    def test_zero_pads_to_three_digits(self, db):
        for i in range(1, 10):
            db.execute(
                """INSERT INTO captures
                       (type, template_id, content_json, raw_ocr, summary, created_at)
                   VALUES ('AIEX', ?, '{}', 'x', 's', '2026-03-01T00:00:00+00:00')""",
                (f"AIEX-{i:03d}",),
            )
        db.commit()
        assert get_next_aiex_id(db) == "AIEX-010"


# ── commit_aiex tool ──────────────────────────────────────────────────────────

def _make_server_with_db(db_path: Path):
    """
    Import server tools with the DB path patched to our test DB.
    Returns (commit_aiex_fn, extract_insights_fn).
    """
    import importlib
    import ksj_mcp.server as srv

    # Patch the private _DB_PATH and reinitialize
    original_db = srv._DB_PATH
    srv._DB_PATH = db_path
    try:
        yield srv
    finally:
        srv._DB_PATH = original_db


class TestCommitAiex:
    def _session_json(self, **overrides) -> str:
        data = {
            "entry_type": "AIEX-001",
            "date": "2026-03-01",
            "source_platform": "Claude Mobile",
            "session_focus": "Sedenion bilateral zero divisors",
            "insights": [
                {
                    "text": "Bilateral zero divisors may encode Riemann zeros.",
                    "confidence_tier": "Seed",
                    "tags": ["#sedenion", "#RiemannHypothesis"],
                    "connections": ["Canonical Six → Riemann zeta zeros"],
                },
                {
                    "text": "E8 lattice first shell contains all P-vector images of Canonical Six.",
                    "confidence_tier": "Strong",
                    "tags": ["#E8", "#sedenion", "$insight"],
                    "connections": [],
                },
            ],
            "open_questions": ["Does OR map to bilateral annihilation?"],
            "action_items": [{"text": "Read Connes spectral paper", "priority": "!", "status": "open"}],
        }
        data.update(overrides)
        return json.dumps(data)

    def test_basic_commit(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.commit_aiex(self._session_json())

        assert "AIEX-001" in result
        assert "AIEX-002" in result
        assert "Seed" in result
        assert "Strong" in result

    def test_rows_stored_in_db(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        srv.commit_aiex(self._session_json())

        with get_connection(db_path) as con:
            rows = con.execute("SELECT * FROM captures WHERE type='AIEX'").fetchall()
        assert len(rows) == 2
        assert rows[0]["template_id"] == "AIEX-001"
        assert rows[1]["template_id"] == "AIEX-002"

    def test_tags_stored(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        srv.commit_aiex(self._session_json())

        with get_connection(db_path) as con:
            tags = con.execute(
                "SELECT prefix, value FROM tags WHERE capture_id=1"
            ).fetchall()
        tag_set = {(t["prefix"], t["value"]) for t in tags}
        assert ("#", "sedenion") in tag_set
        assert ("#", "riemannhypothesis") in tag_set

    def test_dollar_sign_tag_in_list(self, tmp_path):
        """Tags with $ prefix in the list are stored as $ tags."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        srv.commit_aiex(self._session_json())

        with get_connection(db_path) as con:
            tags = con.execute(
                "SELECT prefix, value FROM tags WHERE capture_id=2"
            ).fetchall()
        tag_set = {(t["prefix"], t["value"]) for t in tags}
        assert ("$", "insight") in tag_set

    def test_content_json_fields(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        srv.commit_aiex(self._session_json())

        with get_connection(db_path) as con:
            row = con.execute("SELECT content_json FROM captures WHERE id=1").fetchone()
        content = json.loads(row["content_json"])
        assert content["confidence_tier"] == "Seed"
        assert content["session_focus"] == "Sedenion bilateral zero divisors"
        assert content["source_platform"] == "Claude Mobile"
        assert content["date"] == "2026-03-01"
        assert len(content["open_questions"]) == 1
        assert len(content["action_items"]) == 1

    def test_invalid_json_returns_error(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.commit_aiex("not valid json {{")
        assert "Invalid JSON" in result

    def test_empty_insights_returns_error(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.commit_aiex(json.dumps({"insights": []}))
        assert "No insights" in result

    def test_insight_with_blank_text_skipped(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        data = json.loads(self._session_json())
        data["insights"][0]["text"] = "   "  # blank
        result = srv.commit_aiex(json.dumps(data))

        with get_connection(db_path) as con:
            count = con.execute("SELECT COUNT(*) AS c FROM captures WHERE type='AIEX'").fetchone()["c"]
        assert count == 1  # only second insight stored

    def test_invalid_tier_defaults_to_seed(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        data = json.loads(self._session_json())
        data["insights"][0]["confidence_tier"] = "NotATier"
        srv.commit_aiex(json.dumps(data))

        with get_connection(db_path) as con:
            row = con.execute("SELECT content_json FROM captures WHERE id=1").fetchone()
        content = json.loads(row["content_json"])
        assert content["confidence_tier"] == "Seed"

    def test_fts_searchable(self, tmp_path):
        """AIEX entries are searchable via FTS."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        srv.commit_aiex(self._session_json())

        with get_connection(db_path) as con:
            results = search_fts(con, query="bilateral zero divisors")
        assert len(results) >= 1
        assert results[0]["type"] == "AIEX"

    def test_sequential_ids_across_calls(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        srv.commit_aiex(self._session_json())
        # Second call should pick up from AIEX-003
        data = json.loads(self._session_json())
        data["insights"] = [{"text": "Third insight.", "confidence_tier": "Developing",
                              "tags": ["#test"], "connections": []}]
        result = srv.commit_aiex(json.dumps(data))
        assert "AIEX-003" in result

    def test_bare_word_tags_get_hash_prefix(self, tmp_path):
        """Tags without a prefix character are stored as # tags."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        data = json.loads(self._session_json())
        data["insights"][0]["tags"] = ["bare-word"]
        srv.commit_aiex(json.dumps(data))

        with get_connection(db_path) as con:
            tags = con.execute(
                "SELECT prefix, value FROM tags WHERE capture_id=1"
            ).fetchall()
        tag_set = {(t["prefix"], t["value"]) for t in tags}
        assert ("#", "bare-word") in tag_set

    def test_action_items_in_output(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.commit_aiex(self._session_json())
        assert "Action items" in result
        assert "Read Connes spectral paper" in result

    def test_open_questions_in_output(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.commit_aiex(self._session_json())
        assert "Open questions" in result
        assert "Does OR map to bilateral annihilation?" in result


# ── extract_insights tool ─────────────────────────────────────────────────────

class TestExtractInsights:
    def test_empty_text_returns_error(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.extract_insights("   ")
        assert "Please provide session_text" in result

    def test_returns_context_block(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.extract_insights("We discussed sedenion zero divisors today.")
        assert "KSJ — AI Insight Extraction" in result
        assert "Extraction Instructions" in result
        assert "commit_aiex" in result

    def test_includes_session_text(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.extract_insights("Unique marker phrase XYZ123 in session.")
        assert "Unique marker phrase XYZ123" in result

    def test_includes_platform(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.extract_insights("session text here", source_platform="Claude Mobile")
        assert "Claude Mobile" in result

    def test_no_db_writes(self, tmp_path):
        """extract_insights must not write any rows to the DB."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        srv.extract_insights("Some interesting session about prime numbers.")
        with get_connection(db_path) as con:
            count = con.execute("SELECT COUNT(*) AS c FROM captures").fetchone()["c"]
        assert count == 0

    def test_shows_kb_stats(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.extract_insights("session text")
        assert "Knowledge base:" in result
        assert "RC:" in result

    def test_truncates_long_session(self, tmp_path):
        db_path = tmp_path / "test.db"
        init_db(db_path)
        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        long_text = "word " * 2000  # well over 8000 chars
        result = srv.extract_insights(long_text)
        assert "truncated" in result

    def test_related_entries_shown(self, tmp_path):
        """When the DB has captures, related ones appear in context."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        # Seed a capture that should match the session text
        with get_connection(db_path) as con:
            con.execute(
                """INSERT INTO captures
                       (type, template_id, content_json, raw_ocr, summary, created_at)
                   VALUES ('RC', 'RC-001', '{}',
                           'sedenion bilateral zero divisors Canonical Six',
                           'Sedenion zero divisors', '2026-01-01T00:00:00+00:00')"""
            )
            con.commit()

        import ksj_mcp.server as srv
        srv._DB_PATH = db_path

        result = srv.extract_insights("sedenion bilateral zero divisors and Canonical Six")
        assert "RC-001" in result
