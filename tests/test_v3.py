"""
Tests for server 3.0 features: tiered ID parsing, tag normalization/roles,
bubble tags, volumes, entities, IDF connections, rebuild, and the v3 migration.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from ksj_mcp.connections import (
    build_connections,
    find_tag_connections,
    rebuild_connections,
)
from ksj_mcp.database import (
    check_duplicate,
    get_active_volumes,
    get_capture,
    get_captures_for_entity,
    get_connection,
    get_connections,
    get_current_volume,
    get_entities_for_capture,
    get_setting,
    insert_capture,
    insert_connection,
    insert_tags,
    link_capture_entity,
    migrate_v3,
    search_fts,
    set_setting,
)
from ksj_mcp.ocr import parse_template_id
from ksj_mcp.templates import assign_role, normalize_tag_value, parse_template


def _cap(con, template_id="RC-001", raw="text", type_=None, volume=1, tags=None, **kw):
    if type_ is None:
        type_ = (template_id or "RC-001").split("-")[0] if template_id else "UNKNOWN"
    cid = insert_capture(
        con, type_, template_id, {"first_impressions": raw[:40]},
        raw, raw[:40], 0.9, volume=volume, **kw,
    )
    if tags:
        insert_tags(con, cid, tags)
    con.commit()
    return cid


# ── Tiered template-ID parsing (§1.3) ─────────────────────────────────────────

class TestParseTemplateId:
    def test_strict_match(self):
        p = parse_template_id("header RC-007 rest of page")
        assert (p["template_type"], p["template_id"], p["id_confidence"]) == ("RC", "RC-007", 1.0)

    def test_strict_with_page_suffix(self):
        p = parse_template_id("RC-002p continued")
        assert p["template_id"] == "RC-002"
        assert p["page_suffix"] == "p"
        assert p["id_confidence"] == 1.0

    def test_loose_unpadded(self):
        p = parse_template_id("RC 7 some notes")
        assert p["template_id"] == "RC-007"
        assert p["id_confidence"] == 0.6

    def test_loose_ocr_confusions(self):
        p = parse_template_id("RC-OO2 header")
        assert p["template_id"] == "RC-002"
        assert p["id_confidence"] == 0.6

    def test_loose_prefix_variant(self):
        p = parse_template_id("SVN-3 breakthrough page")
        assert p["template_type"] == "SYN"
        assert p["template_id"] == "SYN-003"

    def test_volume_marker(self):
        p = parse_template_id("V2 RC-001 first page of second book")
        # strict pattern matches RC-001 first; volume comes from loose only
        # when strict fails, so test a loose-only form:
        p2 = parse_template_id("V2-RC OO1")
        assert p2["volume"] == 2
        assert p2["template_id"] == "RC-001"

    def test_no_match_inside_word(self):
        p = parse_template_id("MARC4 processor notes")
        assert p["template_type"] == "UNKNOWN"

    def test_nothing_found(self):
        p = parse_template_id("just some prose with no id")
        assert p["template_type"] == "UNKNOWN"
        assert p["id_confidence"] == 0.0


# ── Tag normalization + roles (§1.6, §1.10) ───────────────────────────────────

class TestTagNormalization:
    def test_variants_collapse(self):
        assert normalize_tag_value("DOG MAN") == "dog-man"
        assert normalize_tag_value("Dog-Man") == "dog-man"
        assert normalize_tag_value("DOG-MAN") == "dog-man"

    def test_role_topic_vs_theme(self):
        assert assign_role("#", "flying", "RC") == "topic"
        assert assign_role("#", "flying", "DC") == "theme"

    def test_role_reference_vs_entity(self):
        assert assign_role("@", "rc-012", "RC") == "reference"
        assert assign_role("@", "veronica", "RC") == "entity"
        assert assign_role("@", "the-old-house", "DC") == "entity"

    def test_role_priority_vs_motif(self):
        assert assign_role("!", "deadline", "REV") == "priority"
        assert assign_role("!", "falling", "DC") == "motif"

    def test_markdown_emphasis_is_not_sensory(self):
        parsed = parse_template("RC", "this is *emphasis* and **bold** text")
        star_tags = [t for t in parsed["tags"] if t["prefix"] == "*"]
        assert star_tags == []

    def test_real_sensory_tag_survives(self):
        parsed = parse_template("DC", "NARRATIVE\nfalling *cold-wind rushing")
        star_tags = [t for t in parsed["tags"] if t["prefix"] == "*"]
        assert len(star_tags) == 1
        assert star_tags[0]["value"] == "cold-wind"
        assert star_tags[0]["role"] == "sensory"


class TestBubbleTags:
    def test_unprefixed_bubble_content_is_tagged(self):
        text = "FIRST IMPRESSIONS\nsome notes\nTAGS\nDOG-MAN, Venice"
        parsed = parse_template("RC", text)
        values = {t["value"] for t in parsed["tags"] if t["prefix"] == "#"}
        assert "dog-man" in values
        assert "venice" in values

    def test_bubble_tags_get_template_role(self):
        text = "NARRATIVE\ndream text\nTAGS\nfalling"
        parsed = parse_template("DC", text)
        falling = [t for t in parsed["tags"] if t["value"] == "falling"]
        assert falling and falling[0]["role"] == "theme"

    def test_quick_questions_become_question_tags(self):
        text = "FIRST IMPRESSIONS\nnotes\nQUICK QUESTIONS\nwho was the professor"
        parsed = parse_template("RC", text)
        questions = [t for t in parsed["tags"] if t["prefix"] == "?"]
        assert any("professor" in t["value"] for t in questions)

    def test_prose_does_not_become_tag(self):
        text = ("FIRST IMPRESSIONS\nnotes\nTAGS\n"
                "this is a long sentence of prose that leaked into the section somehow")
        parsed = parse_template("RC", text)
        assert all(len(t["value"]) <= 45 for t in parsed["tags"])


# ── Volumes (§1.4, §1.5) ──────────────────────────────────────────────────────

class TestVolumes:
    def test_same_id_different_volume_allowed(self, db):
        _cap(db, "RC-001", volume=1)
        _cap(db, "RC-001", volume=2)  # must not raise
        rows = db.execute("SELECT volume FROM captures WHERE template_id='RC-001'").fetchall()
        assert sorted(r["volume"] for r in rows) == [1, 2]

    def test_same_id_same_volume_rejected(self, db):
        _cap(db, "RC-001", volume=1)
        with pytest.raises(sqlite3.IntegrityError):
            _cap(db, "RC-001", volume=1)

    def test_multiple_unidentified_allowed(self, db):
        _cap(db, None, type_="UNKNOWN")
        _cap(db, None, type_="UNKNOWN")  # NULL template_ids are distinct
        n = db.execute("SELECT COUNT(*) AS n FROM captures WHERE template_id IS NULL").fetchone()["n"]
        assert n == 2

    def test_check_duplicate_is_volume_scoped(self, db):
        _cap(db, "RC-001", volume=1)
        assert check_duplicate(db, "RC-001", volume=1) is not None
        assert check_duplicate(db, "RC-001", volume=2) is None
        assert check_duplicate(db, "RC-001") is not None  # any volume

    def test_settings_roundtrip(self, db):
        assert get_current_volume(db) == 1
        assert get_active_volumes(db) is None  # '*'
        set_setting(db, "current_volume", "2")
        set_setting(db, "active_volumes", "2,3")
        assert get_current_volume(db) == 2
        assert get_active_volumes(db) == [2, 3]

    def test_search_respects_volume_filter(self, db):
        _cap(db, "RC-001", raw="quantum notes book one", volume=1)
        _cap(db, "RC-001", raw="quantum notes book two", volume=2)
        assert len(search_fts(db, "quantum")) == 2
        assert len(search_fts(db, "quantum", volumes=[2])) == 1


# ── Entities (§1.9) ───────────────────────────────────────────────────────────

class TestEntities:
    def test_entity_tag_creates_entity_row(self, db):
        cid = _cap(db, "RC-001", tags=[
            {"prefix": "@", "value": "veronica", "display": "Veronica", "role": "entity"},
        ])
        ents = get_entities_for_capture(db, cid)
        assert len(ents) == 1
        assert ents[0]["name"] == "Veronica"

    def test_reference_tag_does_not_create_entity(self, db):
        cid = _cap(db, "RC-002", tags=[
            {"prefix": "@", "value": "rc-001", "display": "RC-001", "role": "reference"},
        ])
        assert get_entities_for_capture(db, cid) == []

    def test_entity_global_across_volumes(self, db):
        c1 = _cap(db, "RC-001", volume=1)
        c2 = _cap(db, "RC-001", volume=3)
        link_capture_entity(db, c1, "Veronica", kind="person", source="asserted")
        link_capture_entity(db, c2, "veronica", kind="person", source="asserted")
        db.commit()
        n = db.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"]
        assert n == 1  # casefold-normalized: one entity
        assert len(get_captures_for_entity(db, "VERONICA")) == 2


# ── Connections: IDF, typed dedup, direction, rebuild (§1.7) ──────────────────

class TestConnectionQuality:
    def test_rare_tag_outweighs_common_tag(self, db):
        common = {"prefix": "#", "value": "common"}
        rare   = {"prefix": "#", "value": "rare"}
        a = _cap(db, "RC-001", tags=[common, rare])
        b = _cap(db, "RC-002", tags=[common, rare])
        for i in range(3, 7):
            _cap(db, f"RC-{i:03d}", tags=[dict(common)])
        result = find_tag_connections(db, a)
        by_target = {r["target_id"]: r for r in result}
        assert by_target[b]["strength"] > max(
            v["strength"] for k, v in by_target.items() if k != b
        )

    def test_reference_and_overlap_edges_coexist(self, db):
        shared = {"prefix": "#", "value": "topic"}
        a = _cap(db, "RC-001", raw="base page #topic", tags=[dict(shared)])
        b = _cap(db, "RC-002", raw="builds on @RC-001 #topic", tags=[dict(shared)])
        build_connections(db, b)
        types = {r["type"] for r in db.execute(
            "SELECT type FROM connections").fetchall()}
        assert types == {"tag_overlap", "reference"}

    def test_reinsert_updates_strength_not_duplicates(self, db):
        a = _cap(db, "RC-001")
        b = _cap(db, "RC-002")
        insert_connection(db, a, b, "tag_overlap", 1.0, "tag_overlap")
        insert_connection(db, b, a, "tag_overlap", 5.0, "tag_overlap")  # canonicalized
        db.commit()
        rows = db.execute("SELECT * FROM connections").fetchall()
        assert len(rows) == 1
        assert rows[0]["strength"] == 5.0

    def test_direction_labels(self, db):
        a = _cap(db, "RC-001", raw="original")
        b = _cap(db, "RC-002", raw="see @RC-001")
        build_connections(db, b)
        conns_b = get_connections(db, b)
        conns_a = get_connections(db, a)
        assert any(c["direction"] == "cites" for c in conns_b)
        assert any(c["direction"] == "cited_by" for c in conns_a)

    def test_references_ranked_first(self, db):
        shared = {"prefix": "#", "value": "x"}
        a = _cap(db, "RC-001", raw="a #x", tags=[dict(shared)])
        b = _cap(db, "RC-002", raw="b @RC-001 #x", tags=[dict(shared)])
        build_connections(db, b)
        conns = get_connections(db, b)
        assert conns[0]["type"] == "reference"


class TestRebuildConnections:
    def test_forward_reference_resolved_on_rebuild(self, db):
        a = _cap(db, "RC-001", raw="this builds on @RC-015")
        build_connections(db, a)  # RC-015 doesn't exist yet — no edge
        assert db.execute("SELECT COUNT(*) AS n FROM connections").fetchone()["n"] == 0
        _cap(db, "RC-015", raw="the referenced page")
        stats = rebuild_connections(db)
        assert stats["references"] == 1

    def test_idempotent(self, db):
        shared = {"prefix": "#", "value": "t"}
        _cap(db, "RC-001", raw="a @RC-002 #t", tags=[dict(shared)])
        _cap(db, "RC-002", raw="b #t", tags=[dict(shared)])
        first = rebuild_connections(db)
        second = rebuild_connections(db)
        assert first == second


# ── migrate_v3 (§1.13) ────────────────────────────────────────────────────────

_V2_SCHEMA = """
    CREATE TABLE captures (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        type        TEXT NOT NULL CHECK(type IN ('RC','SYN','REV','DC','AIEX')),
        template_id TEXT NOT NULL,
        content_json TEXT NOT NULL,
        raw_ocr     TEXT NOT NULL,
        corrected_ocr TEXT,
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
"""


class TestMigrateV3:
    def _make_v2_db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "v2.db"
        con = sqlite3.connect(db_path)
        con.executescript(_V2_SCHEMA)
        rows = [
            ("RC",   "RC-001",  '{"first_impressions": "x"}', "rc text",  "rc"),
            ("DC",   "DC-001",  '{"dream_narrative": "d"}',   "dc text",  "dc"),
            ("AIEX", "AIEX-001", '{"insight": "i"}',          "ai text",  "ai"),
        ]
        for type_, tid, content, raw, summary in rows:
            con.execute(
                """INSERT INTO captures (type, template_id, content_json, raw_ocr,
                                          summary, confidence, image_path, created_at)
                   VALUES (?, ?, ?, ?, ?, 0.9, '', '2026-01-01T00:00:00')""",
                (type_, tid, content, raw, summary),
            )
            con.execute(
                "INSERT INTO captures_fts(rowid, raw_ocr, summary) "
                "SELECT id, raw_ocr, summary FROM captures WHERE template_id=?",
                (tid,),
            )
        con.execute("INSERT INTO tags (capture_id, prefix, value) VALUES (1, '#', 'ml')")
        con.execute("INSERT INTO tags (capture_id, prefix, value) VALUES (2, '@', 'the-house')")
        con.execute("INSERT INTO tags (capture_id, prefix, value) VALUES (2, '!', 'falling')")
        con.commit()
        con.close()
        return db_path

    def test_full_migration(self, tmp_path):
        db_path = self._make_v2_db(tmp_path)
        assert migrate_v3(db_path) is True

        con = get_connection(db_path)
        cols = {r[1] for r in con.execute("PRAGMA table_info(captures)").fetchall()}
        assert {"volume", "page_suffix", "source", "corrected_ocr"} <= cols

        # source back-fill: AIEX rows are ai_extract, the rest journal
        src = {
            r["template_id"]: r["source"]
            for r in con.execute("SELECT template_id, source FROM captures").fetchall()
        }
        assert src["RC-001"] == "journal"
        assert src["AIEX-001"] == "ai_extract"

        # role back-fill derived from (prefix, template type)
        roles = {
            (r["prefix"], r["value"]): r["role"]
            for r in con.execute("SELECT prefix, value, role FROM tags").fetchall()
        }
        assert roles[("#", "ml")] == "topic"
        assert roles[("@", "the-house")] == "entity"   # DC page
        assert roles[("!", "falling")] == "motif"      # DC page

        # settings created; entities empty (no legacy back-fill)
        assert get_setting(con, "active_volumes") == "*"
        assert con.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"] == 0

        # backup exists
        assert db_path.with_name(db_path.name + ".bak-v3").exists()

        # FTS still searches
        assert search_fts(con, "rc") != []
        con.close()

    def test_idempotent(self, tmp_path):
        db_path = self._make_v2_db(tmp_path)
        assert migrate_v3(db_path) is True
        assert migrate_v3(db_path) is False

    def test_missing_db_noop(self, tmp_path):
        assert migrate_v3(tmp_path / "nope.db") is False

    def test_fresh_v3_db_noop(self, db, tmp_path):
        # conftest's db fixture is a fresh init_db database — already v3
        # (the fixture path is tmp_path / "test_captures.db")
        assert migrate_v3(tmp_path / "test_captures.db") is False
