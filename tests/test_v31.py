"""
Tests for server 3.1: typed/asserted edges, bi-temporal supersession,
entity-weighted connections, the unapplied check, traversal, lint, and the
JSONL export/import round-trip.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ksj_mcp.connections import (
    build_connections,
    find_path,
    find_tag_connections,
    find_unapplied,
    neighborhood,
    rebuild_connections,
    run_lint,
)
from ksj_mcp.database import (
    export_jsonl,
    get_connection,
    get_connections,
    import_jsonl,
    init_db,
    insert_capture,
    insert_connection,
    insert_tags,
    link_capture_entity,
    migrate_v3,
    migrate_v31,
    search_fts,
)

from tests.test_v3 import _V2_SCHEMA


def _cap(con, template_id="RC-001", raw="text", type_=None, volume=1, tags=None):
    if type_ is None:
        type_ = (template_id or "RC-001").split("-")[0] if template_id else "UNKNOWN"
    cid = insert_capture(
        con, type_, template_id, {"first_impressions": raw[:40]},
        raw, raw[:40], 0.9, volume=volume,
    )
    if tags:
        insert_tags(con, cid, tags)
    con.commit()
    return cid


def _backdate(con, capture_id, days):
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con.execute("UPDATE captures SET created_at=?, valid_from=? WHERE id=?",
                (old, old, capture_id))
    con.commit()


def _assert_edge(con, source, target, relation, note=None):
    """DB-level equivalent of the assert_connection tool."""
    insert_connection(con, source, target, "asserted", 1.0, "asserted",
                      relation=relation, note=note, asserted_by="user")
    if relation == "supersedes":
        now = datetime.now(timezone.utc).isoformat()
        con.execute("UPDATE captures SET valid_until=? WHERE id=?", (now, target))
    con.commit()


# ── Typed edges + bi-temporal (§2.1) ──────────────────────────────────────────

class TestAssertedEdges:
    def test_asserted_edge_round_trips(self, db):
        a = _cap(db, "RC-001")
        b = _cap(db, "RC-002")
        _assert_edge(db, b, a, "supports", note="evidence")
        conns = get_connections(db, b)
        asserted = [c for c in conns if c["type"] == "asserted"]
        assert asserted[0]["relation"] == "supports"
        assert asserted[0]["note"] == "evidence"
        assert asserted[0]["asserted_by"] == "user"

    def test_asserted_ranked_first(self, db):
        shared = {"prefix": "#", "value": "x"}
        a = _cap(db, "RC-001", tags=[dict(shared)])
        b = _cap(db, "RC-002", tags=[dict(shared)])
        build_connections(db, b)
        _assert_edge(db, b, a, "narrows")
        conns = get_connections(db, b)
        assert conns[0]["type"] == "asserted"

    def test_supersedes_closes_out_target(self, db):
        old = _cap(db, "RC-001", raw="the old claim about zebras")
        new = _cap(db, "RC-002", raw="the corrected claim about zebras")
        assert len(search_fts(db, "zebras")) == 2
        _assert_edge(db, new, old, "supersedes")
        current = search_fts(db, "zebras")
        assert len(current) == 1
        assert current[0]["id"] == new
        # history is preserved, not deleted
        assert len(search_fts(db, "zebras", include_superseded=True)) == 2

    def test_direction_labels_on_asserted(self, db):
        a = _cap(db, "RC-001")
        b = _cap(db, "RC-002")
        _assert_edge(db, b, a, "refutes")
        assert any(c["direction"] == "cites" for c in get_connections(db, b))
        assert any(c["direction"] == "cited_by" for c in get_connections(db, a))


# ── Entity-weighted connections (§2.2c) ───────────────────────────────────────

class TestEntityConnections:
    def test_shared_entity_creates_entity_overlap_edge(self, db):
        ent = {"prefix": "@", "value": "veronica", "display": "Veronica", "role": "entity"}
        a = _cap(db, "RC-001", tags=[dict(ent)])
        b = _cap(db, "RC-002", tags=[dict(ent)])
        build_connections(db, b)
        types = {r["type"] for r in db.execute("SELECT type FROM connections").fetchall()}
        assert "entity_overlap" in types

    def test_entity_tags_excluded_from_tag_overlap(self, db):
        ent = {"prefix": "@", "value": "veronica", "display": "Veronica", "role": "entity"}
        a = _cap(db, "RC-001", tags=[dict(ent)])
        b = _cap(db, "RC-002", tags=[dict(ent)])
        assert find_tag_connections(db, b) == []  # no double counting

    def test_entity_edge_ranked_above_tag_overlap(self, db):
        ent    = {"prefix": "@", "value": "veronica", "display": "Veronica", "role": "entity"}
        shared = {"prefix": "#", "value": "screenplay"}
        a = _cap(db, "RC-001", tags=[dict(ent), dict(shared)])
        b = _cap(db, "RC-002", tags=[dict(ent), dict(shared)])
        build_connections(db, b)
        conns = get_connections(db, b)
        types_in_order = [c["type"] for c in conns]
        assert types_in_order.index("entity_overlap") < types_in_order.index("tag_overlap")


# ── Unapplied check (§2.2a) ───────────────────────────────────────────────────

class TestFindUnapplied:
    def test_uncited_insight_surfaces(self, db):
        rare = {"prefix": "#", "value": "rare-topic"}
        insight = {"prefix": "$", "value": "finding", "role": "insight"}
        old = _cap(db, "RC-001", tags=[dict(rare), dict(insight)])
        _backdate(db, old, 10)
        new = _cap(db, "RC-002", tags=[dict(rare)])
        hits = find_unapplied(db, new)
        assert [h["id"] for h in hits] == [old]
        assert "#rare-topic" in hits[0]["shared"]

    def test_cited_insight_not_surfaced(self, db):
        rare = {"prefix": "#", "value": "rare-topic"}
        insight = {"prefix": "$", "value": "finding", "role": "insight"}
        old = _cap(db, "RC-001", tags=[dict(rare), dict(insight)])
        _backdate(db, old, 10)
        citer = _cap(db, "RC-003", raw="builds on @RC-001")
        build_connections(db, citer)  # creates the inbound reference
        new = _cap(db, "RC-002", tags=[dict(rare)])
        assert find_unapplied(db, new) == []

    def test_capture_without_insight_tag_not_surfaced(self, db):
        rare = {"prefix": "#", "value": "rare-topic"}
        old = _cap(db, "RC-001", tags=[dict(rare)])
        _backdate(db, old, 10)
        new = _cap(db, "RC-002", tags=[dict(rare)])
        assert find_unapplied(db, new) == []

    def test_shared_entity_triggers(self, db):
        ent = {"prefix": "@", "value": "veronica", "display": "Veronica", "role": "entity"}
        pri = {"prefix": "!", "value": "follow-up", "role": "priority"}
        old = _cap(db, "RC-001", tags=[dict(ent), dict(pri)])
        _backdate(db, old, 5)
        new = _cap(db, "RC-002", tags=[dict(ent)])
        hits = find_unapplied(db, new)
        assert [h["id"] for h in hits] == [old]
        assert "@Veronica" in hits[0]["shared"]


# ── Traversal (§2.3) ──────────────────────────────────────────────────────────

class TestTraversal:
    def _chain(self, db):
        a = _cap(db, "RC-001", raw="start")
        b = _cap(db, "RC-002", raw="middle @RC-001")
        c = _cap(db, "RC-003", raw="end @RC-002")
        rebuild_connections(db)
        return a, b, c

    def test_find_path(self, db):
        a, b, c = self._chain(db)
        path = find_path(db, a, c)
        assert [h["id"] for h in path] == [a, b, c]
        assert path[1]["via"] == "reference"

    def test_no_path(self, db):
        a, b, c = self._chain(db)
        lone = _cap(db, "RC-010", raw="isolated")
        assert find_path(db, a, lone) is None

    def test_neighborhood_depths(self, db):
        a, b, c = self._chain(db)
        dist = neighborhood(db, a, depth=2)
        assert dist == {b: 1, c: 2}
        assert neighborhood(db, a, depth=1) == {b: 1}


# ── Lint (§2.5) ───────────────────────────────────────────────────────────────

class TestLint:
    def test_orphan_detected(self, db):
        _cap(db, "RC-001", raw="all alone")
        report = run_lint(db)
        assert len(report["orphans"]) == 1

    def test_connected_capture_not_orphan(self, db):
        shared = {"prefix": "#", "value": "x"}
        _cap(db, "RC-001", tags=[dict(shared)])
        b = _cap(db, "RC-002", tags=[dict(shared)])
        build_connections(db, b)
        assert run_lint(db)["orphans"] == []

    def test_stale_question(self, db):
        q = {"prefix": "?", "value": "why", "role": "question"}
        cid = _cap(db, "RC-001", tags=[dict(q)])
        _backdate(db, cid, 45)
        report = run_lint(db, stale_question_days=30)
        assert len(report["stale_questions"]) == 1
        assert run_lint(db, stale_question_days=60)["stale_questions"] == []

    def test_refutes_pair_flagged(self, db):
        a = _cap(db, "RC-001")
        b = _cap(db, "RC-002")
        _assert_edge(db, b, a, "refutes")
        report = run_lint(db)
        assert len(report["refutes_pairs"]) == 1

    def test_singleton_tags(self, db):
        _cap(db, "RC-001", tags=[{"prefix": "#", "value": "one-off"}])
        report = run_lint(db)
        assert any(t["value"] == "one-off" for t in report["singleton_tags"])


# ── JSONL export / import (§2.4b) ─────────────────────────────────────────────

class TestExportImportRoundTrip:
    def _build_base(self, con):
        rare = {"prefix": "#", "value": "topic", "display": "Topic", "role": "topic"}
        ent  = {"prefix": "@", "value": "veronica", "display": "Veronica", "role": "entity"}
        a = _cap(con, "RC-001", raw="first page #topic", tags=[dict(rare)])
        b = _cap(con, "RC-002", raw="second @Veronica", tags=[dict(ent)])
        link_capture_entity(con, a, "Tiny's", kind="place", source="asserted")
        _assert_edge(con, b, a, "supports", note="because")
        rebuild_connections(con)
        return a, b

    def test_round_trip_into_empty_db(self, db, tmp_path):
        self._build_base(db)
        dump = export_jsonl(db)

        target_path = tmp_path / "restore.db"
        init_db(target_path)
        con2 = get_connection(target_path)
        stats = import_jsonl(con2, dump)
        rebuild_connections(con2)

        assert stats["captures"] == 2
        assert stats["asserted_edges"] == 1
        assert search_fts(con2, "first") != []
        asserted = con2.execute(
            "SELECT relation, note, asserted_by FROM connections WHERE type='asserted'"
        ).fetchone()
        assert asserted["relation"] == "supports"
        assert asserted["note"] == "because"
        n_entities = con2.execute("SELECT COUNT(*) AS n FROM entities").fetchone()["n"]
        assert n_entities == 2  # Veronica + Tiny's
        con2.close()

    def test_import_is_additive_not_destructive(self, db):
        self._build_base(db)
        dump = export_jsonl(db)
        stats = import_jsonl(db, dump)  # import into the same base
        assert stats["captures"] == 0
        assert stats["skipped"] == 2

    def test_bad_schema_version_rejected(self, db):
        with pytest.raises(ValueError, match="schema"):
            import_jsonl(db, '{"kind": "header", "schema_version": "ksj-export-v99"}')

    def test_missing_header_rejected(self, db):
        with pytest.raises(ValueError, match="header"):
            import_jsonl(db, '{"kind": "capture", "id": 1}')


# ── migrate_v31 ───────────────────────────────────────────────────────────────

class TestMigrateV31:
    def test_additive_on_v3_db(self, tmp_path):
        db_path = tmp_path / "old.db"
        con = sqlite3.connect(db_path)
        con.executescript(_V2_SCHEMA)
        con.commit()
        con.close()
        assert migrate_v3(db_path) is True
        migrate_v31(db_path)

        con = get_connection(db_path)
        cap_cols = {r[1] for r in con.execute("PRAGMA table_info(captures)").fetchall()}
        conn_cols = {r[1] for r in con.execute("PRAGMA table_info(connections)").fetchall()}
        assert {"valid_from", "valid_until"} <= cap_cols
        assert {"relation", "note", "asserted_by"} <= conn_cols
        con.close()

    def test_idempotent(self, tmp_path):
        db_path = tmp_path / "old.db"
        con = sqlite3.connect(db_path)
        con.executescript(_V2_SCHEMA)
        con.close()
        migrate_v3(db_path)
        migrate_v31(db_path)
        migrate_v31(db_path)  # no-op

    def test_fresh_db_noop(self, db, tmp_path):
        migrate_v31(tmp_path / "test_captures.db")  # fixture DB is current
