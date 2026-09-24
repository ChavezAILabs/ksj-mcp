"""
Tests for server 3.7: fast initialize (store opened off the handshake path),
Wild Art (WA) accept-by-default uploads + promotion, ISO pages, and the
export.html navigation / footer metadata.
"""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import ksj_mcp.server as srv
from ksj_mcp.database import (
    export_jsonl,
    get_connection,
    get_next_wa_id,
    get_stats,
    get_wa_counts,
    import_jsonl,
    init_db,
    insert_capture,
    migrate_v37,
)
from ksj_mcp.htmlview import collect_view_data, render_html
from ksj_mcp.ocr import parse_template_id


RC5 = "RC-005\nFirst Impressions:\nThe original page five\nTags: #lamps #design"


@pytest.fixture
def store(tmp_path: Path, monkeypatch):
    """Point the server at a fresh temp database for each test."""
    db_path = tmp_path / "captures.db"
    init_db(db_path)
    monkeypatch.setattr(srv, "_DB_PATH", db_path)
    monkeypatch.setattr(srv, "_IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(srv, "_db", lambda: get_connection(db_path))
    return db_path


def _row(db_path: Path, **where) -> dict:
    con = get_connection(db_path)
    k, v = next(iter(where.items()))
    r = con.execute(f"SELECT * FROM captures WHERE {k}=?", (v,)).fetchone()
    con.close()
    return dict(r) if r else None


# ── Workstream 1: nothing touches the store before the handshake ─────────────

class TestLazyStore:
    def test_import_does_not_open_the_store(self, tmp_path):
        env = dict(os.environ, KSJ_DATA_DIR=str(tmp_path))
        subprocess.run([sys.executable, "-c", "import ksj_mcp.server"], env=env, check=True)
        assert not (tmp_path / "captures.db").exists()

    def test_first_tool_call_opens_and_migrates_store(self, tmp_path):
        env = dict(os.environ, KSJ_DATA_DIR=str(tmp_path))
        out = subprocess.run(
            [sys.executable, "-c",
             "import ksj_mcp.server as s; print(s.get_stats())"],
            env=env, check=True, capture_output=True, text=True,
        ).stdout
        assert "knowledge base is empty" in out
        assert (tmp_path / "captures.db").exists()

    def test_store_error_is_reported_to_the_tool_call(self, monkeypatch, tmp_path):
        monkeypatch.setattr(srv, "_store_thread", None)
        monkeypatch.setattr(srv, "_store_error", None)
        monkeypatch.setattr(srv, "_store_ready", __import__("threading").Event())

        def boom():
            raise sqlite3.DatabaseError("file is not a database")
        monkeypatch.setattr(srv, "_init_store", boom)
        with pytest.raises(RuntimeError, match="could not be opened"):
            srv._ensure_store()

    def test_waits_out_a_locked_database(self, monkeypatch):
        monkeypatch.setattr(srv, "_store_thread", None)
        monkeypatch.setattr(srv, "_store_error", None)
        monkeypatch.setattr(srv, "_store_ready", __import__("threading").Event())
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(srv, "_init_store", flaky)
        monkeypatch.setattr(srv.time, "sleep", lambda s: None)
        srv._ensure_store()
        assert len(calls) == 3 and srv._store_error is None


# ── migrate_v37 ───────────────────────────────────────────────────────────────

_V36_CAPTURES = """
CREATE TABLE captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('RC','SYN','REV','DC','AIEX','UNKNOWN')),
    template_id TEXT, page_suffix TEXT, volume INTEGER NOT NULL DEFAULT 1,
    content_json TEXT NOT NULL, raw_ocr TEXT NOT NULL, corrected_ocr TEXT,
    summary TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0.0,
    image_path TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'journal',
    valid_from TEXT, valid_until TEXT, created_at TEXT NOT NULL
);
"""


class TestMigrateV37:
    def _v36_db(self, tmp_path: Path) -> Path:
        """A current init_db schema with the pre-3.7 captures table swapped in."""
        db_path = tmp_path / "old.db"
        init_db(db_path)
        con = sqlite3.connect(db_path)
        con.executescript("PRAGMA foreign_keys=OFF; DROP TABLE captures;" + _V36_CAPTURES)
        con.close()
        init_db(db_path)  # triggers + indexes back, as a 3.6 store has them
        con = get_connection(db_path)
        for i in range(1, 6):
            con.execute(
                """INSERT INTO captures (type, template_id, content_json, raw_ocr,
                                         summary, confidence, created_at)
                   VALUES ('RC', ?, '{}', ?, ?, 0.9, '2026-09-01T00:00:00')""",
                (f"RC-00{i}", f"RC-00{i} findme page {i}", f"p{i}"),
            )
        con.execute("INSERT INTO tags (capture_id, prefix, value) VALUES (2, '#', 'x')")
        con.execute("DELETE FROM captures WHERE id=5")  # seq (5) > max id (4)
        con.commit()
        con.close()
        return db_path

    def test_adds_types_columns_and_keeps_rows(self, tmp_path):
        db_path = self._v36_db(tmp_path)
        before = [tuple(r) for r in sqlite3.connect(db_path).execute(
            "SELECT id, type, template_id, volume, raw_ocr FROM captures ORDER BY id")]
        assert migrate_v37(db_path) is True
        assert (tmp_path / "old.db.bak-v37").exists()

        con = get_connection(db_path)
        after = [tuple(r) for r in con.execute(
            "SELECT id, type, template_id, volume, raw_ocr FROM captures ORDER BY id")]
        assert before == after
        cols = {r[1] for r in con.execute("PRAGMA table_info(captures)")}
        assert {"wa_reason", "wa_detail", "claimed_page_id", "conflicts_with",
                "promoted_from", "promoted_at"} <= cols
        # new types accepted, volume nullable, FTS still wired up
        insert_capture(con, "WA", "WA-001", {}, "napkin findme", "n", 1.0, volume=None,
                       wa_reason="loose_capture")
        insert_capture(con, "ISO", "ISO-001", {}, "iso", "i", 1.0)
        con.commit()
        hits = con.execute(
            "SELECT rowid FROM captures_fts WHERE captures_fts MATCH 'findme'").fetchall()
        assert len(hits) == 5
        # ids never reused: the deleted #5 stays burned
        assert con.execute("SELECT MIN(id) FROM captures WHERE type='WA'").fetchone()[0] == 6
        assert con.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 1
        con.close()

    def test_idempotent_and_noop_on_fresh(self, tmp_path):
        db_path = self._v36_db(tmp_path)
        assert migrate_v37(db_path) is True
        assert migrate_v37(db_path) is False
        fresh = tmp_path / "fresh.db"
        init_db(fresh)
        assert migrate_v37(fresh) is False
        assert migrate_v37(tmp_path / "missing.db") is False


# ── Workstream 3: accept-by-default ───────────────────────────────────────────

class TestAcceptByDefault:
    def test_id_conflict_goes_to_wa_and_leaves_original(self, store):
        srv.manual_capture(RC5)
        original = _row(store, template_id="RC-005")
        out = srv.manual_capture("RC-005\nFirst Impressions:\nsecond page\nTags: #x")
        assert "Wild Art WA-001" in out and "id_conflict" in out
        wa = _row(store, template_id="WA-001")
        assert wa["type"] == "WA" and wa["wa_reason"] == "id_conflict"
        assert wa["claimed_page_id"] == "RC-005" and wa["volume"] == 1
        assert wa["conflicts_with"] == original["id"]
        assert _row(store, id=original["id"]) == original

    def test_id_conflict_keeps_the_real_volume(self, store):
        srv.manual_capture(RC5, volume=2)
        srv.manual_capture(RC5 + "\nagain", volume=2)
        assert _row(store, template_id="WA-001")["volume"] == 2

    def test_force_still_replaces(self, store):
        srv.manual_capture(RC5)
        out = srv.manual_capture("RC-005\nFirst Impressions:\nreplacement", force=True)
        assert "Wild Art" not in out
        assert _row(store, template_id="RC-005")["summary"] == "replacement"

    def test_unparseable_explicit_id_is_validation_error(self, store):
        out = srv.manual_capture("some text", template_id="QQ-9")
        assert "validation_error" in out
        assert "QQ-9" in _row(store, template_id="WA-001")["wa_detail"]

    def test_loose_capture_has_no_volume(self, store):
        out = srv.manual_capture("idea on a napkin #lamps", wa_reason="loose_capture",
                                 wa_detail="napkin")
        assert "(none — loose capture)" in out
        wa = _row(store, template_id="WA-001")
        assert wa["volume"] is None and wa["wa_detail"] == "napkin"

    def test_unknown_reason_is_kept_as_other(self, store):
        srv.manual_capture("whiteboard", wa_reason="whiteboard photo")
        wa = _row(store, template_id="WA-001")
        assert wa["wa_reason"] == "other" and "whiteboard photo" in wa["wa_detail"]

    def test_manual_wa_by_template_id(self, store):
        srv.manual_capture("RC-009\nFirst Impressions:\nnot sure", template_id="WA")
        wa = _row(store, template_id="WA-001")
        assert wa["wa_reason"] == "manual" and wa["claimed_page_id"] == "RC-009"

    def test_wa_parsed_with_claimed_type(self, store):
        srv.manual_capture("DC-001\nNarrative:\nflying\nTags: !falling")
        srv.manual_capture("DC-001\nNarrative:\nflying again\nTags: !falling")
        con = get_connection(store)
        role = con.execute(
            """SELECT t.role FROM tags t JOIN captures c ON c.id=t.capture_id
               WHERE c.type='WA' AND t.prefix='!'""").fetchone()[0]
        con.close()
        assert role == "motif"  # DC meaning of '!', not RC 'priority'


def _fake_ocr(monkeypatch, **fields):
    base = {"raw_text": "", "template_type": "UNKNOWN", "template_id": "",
            "page_suffix": None, "volume": None, "id_confidence": 0.0, "confidence": 0.9}
    base.update(fields)
    monkeypatch.setattr(srv, "extract_text", lambda path: dict(base))


class TestUploadAcceptByDefault:
    @pytest.fixture
    def image(self, tmp_path):
        p = tmp_path / "page.png"
        p.write_bytes(b"not really a png")
        return str(p)

    def test_unrecognized_template(self, store, monkeypatch, image):
        _fake_ocr(monkeypatch, raw_text="a doodle")
        out = srv.upload_capture(image)
        assert "unrecognized_template" in out
        wa = _row(store, template_id="WA-001")
        assert Path(wa["image_path"]).exists() and "WA-001" in wa["image_path"]

    def test_loosely_read_id_on_low_confidence_page(self, store, monkeypatch, image):
        _fake_ocr(monkeypatch, raw_text="RC 7 blurry", template_type="RC",
                  template_id="RC-007", id_confidence=0.6, confidence=0.4)
        out = srv.upload_capture(image)
        assert "ocr_low_confidence" in out
        assert _row(store, template_id="WA-001")["claimed_page_id"] == "RC-007"

    def test_strict_id_on_low_confidence_page_is_still_filed(self, store, monkeypatch, image):
        _fake_ocr(monkeypatch, raw_text="RC-007 blurry", template_type="RC",
                  template_id="RC-007", id_confidence=1.0, confidence=0.4)
        out = srv.upload_capture(image)
        assert "Wild Art" not in out and _row(store, template_id="RC-007")

    def test_ocr_failure_on_image_keeps_photo(self, store, monkeypatch, image):
        def boom(path):
            raise RuntimeError("cannot identify image file")
        monkeypatch.setattr(srv, "extract_text", boom)
        out = srv.upload_capture(image)
        wa = _row(store, template_id="WA-001")
        assert wa["wa_reason"] == "other" and "cannot identify" in wa["wa_detail"]
        assert Path(wa["image_path"]).exists()

    def test_missing_file_is_still_an_error(self, store):
        out = srv.upload_capture("/no/such/file.png")
        assert "not found" in out.lower() and _row(store, type="WA") is None

    def test_bulk_upload_reports_wa(self, store, monkeypatch, tmp_path):
        folder = tmp_path / "imgs"
        folder.mkdir()
        for n in ("a.png", "b.png"):
            (folder / n).write_bytes(b"x")
        _fake_ocr(monkeypatch, raw_text="RC-001\nFirst Impressions:\nx", template_type="RC",
                  template_id="RC-001", id_confidence=1.0)
        out = srv.bulk_upload(str(folder))
        assert "WA    b.png" in out and "[id_conflict]" in out
        assert "2 stored (1 filed, 1 as Wild Art)" in out


# ── promote_capture ───────────────────────────────────────────────────────────

class TestPromote:
    def test_promote_to_free_id_keeps_history(self, store):
        srv.manual_capture("scribble #x", wa_reason="manual")
        wa = _row(store, template_id="WA-001")
        out = srv.promote_capture(wa["id"], "RC", "RC-014")
        assert out.startswith("Promoted WA-001")
        r = _row(store, id=wa["id"])
        assert (r["type"], r["template_id"], r["promoted_from"], r["wa_reason"]) == \
            ("RC", "RC-014", "WA-001", "manual")
        assert r["promoted_at"]

    def test_occupied_id_is_refused_unchanged(self, store):
        srv.manual_capture(RC5)
        srv.manual_capture(RC5 + " dup")
        wa = _row(store, template_id="WA-001")
        out = srv.promote_capture(wa["id"], "RC")  # defaults to claimed RC-005
        assert out.startswith("Not promoted") and "RC-005 is already taken" in out
        assert _row(store, id=wa["id"]) == wa

    def test_force_demotes_holder_instead_of_deleting(self, store):
        srv.manual_capture(RC5)
        holder = _row(store, template_id="RC-005")
        srv.manual_capture(RC5 + " dup")
        wa = _row(store, template_id="WA-001")
        out = srv.promote_capture(wa["id"], "RC", force=True)
        assert "moved to Wild Art as WA-002" in out
        h = _row(store, id=holder["id"])
        assert (h["type"], h["template_id"], h["claimed_page_id"], h["conflicts_with"]) == \
            ("WA", "WA-002", "RC-005", wa["id"])
        assert _row(store, id=wa["id"])["template_id"] == "RC-005"

    def test_needs_a_page_id(self, store):
        srv.manual_capture("napkin", wa_reason="loose_capture")
        wa = _row(store, template_id="WA-001")
        assert "has no SYN page ID" in srv.promote_capture(wa["id"], "SYN")
        assert "not a SYN page ID" in srv.promote_capture(wa["id"], "SYN", "RC-001")

    def test_promote_loose_capture_to_iso(self, store):
        srv.manual_capture("iso sketch", wa_reason="loose_capture")
        wa = _row(store, template_id="WA-001")
        srv.promote_capture(wa["id"], "ISO", "ISO-002")
        r = _row(store, id=wa["id"])
        assert r["type"] == "ISO" and r["volume"] == 1

    def test_only_wa_entries(self, store):
        srv.manual_capture(RC5)
        rc = _row(store, template_id="RC-005")
        assert "not a Wild Art entry" in srv.promote_capture(rc["id"], "SYN", "SYN-001")

    def test_identify_capture_on_wa_routes_to_promotion(self, store):
        srv.manual_capture("x", wa_reason="manual")
        wa = _row(store, template_id="WA-001")
        assert srv.identify_capture(wa["id"], "SYN-003").startswith("Promoted WA-001")
        assert _row(store, id=wa["id"])["promoted_from"] == "WA-001"

    def test_wa_numbers_never_reused(self, store):
        srv.manual_capture("a", wa_reason="manual")
        srv.manual_capture("b", wa_reason="manual")
        srv.promote_capture(_row(store, template_id="WA-002")["id"], "RC", "RC-001")
        con = get_connection(store)
        assert get_next_wa_id(con) == "WA-003"
        con.close()


# ── ISO pages and visibility ──────────────────────────────────────────────────

class TestIsoAndVisibility:
    def test_iso_id_strict_only(self):
        assert parse_template_id("ISO-004")["template_type"] == "ISO"
        assert parse_template_id("shot at ISO 400")["template_type"] == "UNKNOWN"

    def test_iso_capture_everywhere(self, store):
        out = srv.manual_capture("ISO-001\nSubject: lamp exploded view\nTags: #design",
                                 template_id="ISO-001")
        assert "Template : ISO-001" in out
        assert "ISO-001" in srv.list_by_tag(entry_type="ISO")
        assert "ISO-001" in srv.search_captures("lamp", entry_type="ISO")
        assert "ISO-001" not in srv.search_captures("lamp", entry_type="RC")
        assert "ISO: 1" in srv.get_stats()
        assert _row(store, template_id="ISO-001")["summary"] == "lamp exploded view"

    def test_wa_queue_listing_and_stats_split(self, store):
        srv.manual_capture(RC5)
        srv.manual_capture(RC5 + " dup")
        srv.manual_capture("napkin", wa_reason="loose_capture")
        assert "WA-001" in srv.list_by_tag(entry_type="WA", wa_reason="id_conflict")
        loose = srv.list_by_tag(wa_reason="loose_capture")
        assert "WA-002" in loose and "WA-001" not in loose and "no volume" in loose
        assert "WA-002" in srv.search_captures("", entry_type="WA")
        stats = srv.get_stats()
        assert "needs attention   : 1  (id_conflict 1)" in stats
        assert "loose captures    : 1" in stats
        assert "1 Wild Art entry waiting for review" in srv.journal_health()

    def test_loose_captures_visible_in_any_volume_scope(self, store):
        srv.manual_capture("napkin idea", wa_reason="loose_capture")
        srv.set_volume(active_volumes="2")
        con = get_connection(store)
        assert get_wa_counts(con, [2])["loose"] == 1
        assert get_stats(con, [2])["total_captures"] == 1
        con.close()

    def test_develops_relation_links_a_scribble(self, store):
        srv.manual_capture("napkin", wa_reason="loose_capture")
        srv.manual_capture("SYN-001\nBreakthrough:\nthe real idea")
        wa, syn = _row(store, template_id="WA-001"), _row(store, template_id="SYN-001")
        assert srv.assert_connection(syn["id"], wa["id"], "develops").startswith(
            "Asserted: SYN-001 develops WA-001")
        assert _row(store, id=wa["id"])["valid_until"] is None  # not closed out

    def test_reference_to_wa_and_iso_ids(self, store):
        srv.manual_capture("napkin", wa_reason="loose_capture")
        srv.manual_capture("ISO-001\nSubject: drawing")
        out = srv.manual_capture("RC-001\nFirst Impressions:\nsee @WA-001 and @ISO-001")
        assert "WA-001 [reference]" in out and "ISO-001 [reference]" in out

    def test_backup_roundtrip_keeps_wa_fields(self, store, tmp_path):
        srv.manual_capture(RC5)
        srv.manual_capture(RC5 + " dup")
        con = get_connection(store)
        dump = export_jsonl(con)
        con.close()
        other = tmp_path / "other.db"
        init_db(other)
        con = get_connection(other)
        import_jsonl(con, dump)
        r = dict(con.execute("SELECT * FROM captures WHERE type='WA'").fetchone())
        holder = con.execute("SELECT id FROM captures WHERE template_id='RC-005'").fetchone()[0]
        con.close()
        assert r["wa_reason"] == "id_conflict" and r["claimed_page_id"] == "RC-005"
        assert r["conflicts_with"] == holder


# ── Workstream 2: export.html ─────────────────────────────────────────────────

class TestExportHtml:
    def test_view_data_carries_wa_fields_and_versions(self, store):
        srv.manual_capture(RC5)
        srv.manual_capture(RC5 + " dup")
        con = get_connection(store)
        data = collect_view_data(con)
        con.close()
        wa = next(c for c in data["captures"] if c["type"] == "WA")
        assert wa["wa_reason"] == "id_conflict" and wa["claimed_page_id"] == "RC-005"
        assert wa["conflicts_with"] is not None
        assert set(data["versions"]) == {"ksj_mcp", "mcp", "pydantic", "python"}

    def test_page_has_history_navigation_and_footer(self, store):
        con = get_connection(store)
        html = render_html(collect_view_data(con))
        con.close()
        for needle in ("history.pushState", "addEventListener('popstate'",
                       "id=\"nav-back\"", "hashToNav(location.hash)", "id=\"footer\"",
                       "'ISO', 'WA'", "develops: 'developed by'"):
            assert needle in html, needle
        assert "graphHistory" not in html  # one history, not a graph-only stack

    def test_export_html_docstring_is_current(self):
        doc = srv.export_html.__doc__ if hasattr(srv.export_html, "__doc__") else ""
        assert "rotating globe" in doc and "no rotating globe" in doc
        assert "browser's Back button" in doc
