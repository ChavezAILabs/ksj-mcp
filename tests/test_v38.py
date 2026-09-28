"""
Tests for server 3.8: the bounded tag-overlap graph and single-pass rebuild
(F1), and the migration that re-parses hand-written captures and sheds the
old near-complete graph (F1 + F2). Findings are from the 2026-09-25 test pass.
"""

import json
import time
from pathlib import Path

import pytest

from ksj_mcp.connections import (
    RARE_TAG_FLOOR,
    TAG_LINKS_PER_CAPTURE,
    build_connections,
    find_tag_connections,
    rebuild_connections,
)
from ksj_mcp.database import (
    get_connection,
    get_setting,
    init_db,
    insert_capture,
    insert_connection,
    insert_tags,
    migrate_v38,
)
from ksj_mcp.templates import assign_role


def _cap(con, template_id, *tags, type_="RC", raw_ocr="text", created_at=None):
    cid = insert_capture(con, type_, template_id, {}, raw_ocr, "", 1.0)
    insert_tags(con, cid, [
        {"prefix": p, "value": v, "role": assign_role(p, v, type_)} for p, v in tags
    ])
    if created_at:
        con.execute("UPDATE captures SET created_at=? WHERE id=?", (created_at, cid))
    con.commit()
    return cid


def _edge_count(con, type_="tag_overlap"):
    return con.execute(
        "SELECT COUNT(*) AS n FROM connections WHERE type=?", (type_,)
    ).fetchone()["n"]


# ── Bounded tag graph ─────────────────────────────────────────────────────────

class TestBoundedTagGraph:
    def test_links_per_capture_capped(self, db):
        ids = [_cap(db, f"RC-{i:03d}", ("#", "common")) for i in range(1, 41)]
        assert len(find_tag_connections(db, ids[0])) == TAG_LINKS_PER_CAPTURE

    def test_limit_none_returns_every_candidate(self, db):
        ids = [_cap(db, f"RC-{i:03d}", ("#", "common")) for i in range(1, 41)]
        assert len(find_tag_connections(db, ids[0], limit=None)) == 39

    def test_rare_tag_ranks_above_stronger_common_overlap(self, db):
        # 40 captures share two common tags; one also shares a rare tag with
        # the subject. The rare link must rank first even though two common
        # tags can outweigh one rare tag in raw IDF strength.
        subject = _cap(db, "RC-001", ("#", "big-a"), ("#", "big-b"), ("#", "rare"))
        rare_mate = _cap(db, "RC-002", ("#", "rare"))
        for i in range(3, 43):
            _cap(db, f"RC-{i:03d}", ("#", "big-a"), ("#", "big-b"))
        top = find_tag_connections(db, subject)[0]
        assert top["target_id"] == rare_mate
        assert top["rare"] is True

    def test_common_only_links_prefer_nearest_in_time(self, db):
        subject = _cap(db, "RC-001", ("#", "thread"),
                       created_at="2026-06-15T00:00:00+00:00")
        near = _cap(db, "RC-002", ("#", "thread"),
                    created_at="2026-06-16T00:00:00+00:00")
        for i in range(3, 60):
            _cap(db, f"RC-{i:03d}", ("#", "thread"),
                 created_at=f"2025-01-{(i % 28) + 1:02d}T00:00:00+00:00")
        assert find_tag_connections(db, subject)[0]["target_id"] == near

    def test_dc_motif_still_links(self, db):
        """On DC pages '!' is a dream motif (topical), not a priority flag —
        the kind-of-note exclusion is by role, so motifs keep linking."""
        a = _cap(db, "DC-001", ("!", "falling"), type_="DC")
        b = _cap(db, "DC-002", ("!", "falling"), type_="DC")
        assert [r["target_id"] for r in find_tag_connections(db, a)] == [b]

    def test_legacy_null_role_kind_tag_does_not_link(self, db):
        a = insert_capture(db, "RC", "RC-001", {}, "t", "", 1.0)
        b = insert_capture(db, "RC", "RC-002", {}, "t", "", 1.0)
        insert_tags(db, a, [{"prefix": "$", "value": "insight"}])
        insert_tags(db, b, [{"prefix": "$", "value": "insight"}])
        db.commit()
        assert find_tag_connections(db, a) == []

    def test_small_base_tags_are_rare_by_floor(self, db):
        a = _cap(db, "RC-001", ("#", "ml"))
        _cap(db, "RC-002", ("#", "ml"))
        assert find_tag_connections(db, a)[0]["rare"] is True
        assert RARE_TAG_FLOOR >= 2


# ── Rebuild ───────────────────────────────────────────────────────────────────

class TestRebuild:
    def test_edge_count_bounded(self, db):
        for i in range(1, 81):
            _cap(db, f"RC-{i:03d}", ("#", "everything"), ("$", "insight"))
        stats = rebuild_connections(db)
        # Each capture contributes at most K edges; pairs are stored once.
        assert stats["tag_overlap"] <= 80 * TAG_LINKS_PER_CAPTURE
        assert stats["tag_overlap"] < 80 * 79 // 2

    def test_idempotent(self, db):
        for i in range(1, 31):
            _cap(db, f"RC-{i:03d}", ("#", "t"), ("#", f"g{i % 3}"))
        first = rebuild_connections(db)
        edges1 = db.execute(
            "SELECT source_id, target_id, type, strength FROM connections ORDER BY 1, 2, 3"
        ).fetchall()
        second = rebuild_connections(db)
        edges2 = db.execute(
            "SELECT source_id, target_id, type, strength FROM connections ORDER BY 1, 2, 3"
        ).fetchall()
        assert first == second
        assert [tuple(r) for r in edges1] == [tuple(r) for r in edges2]

    def test_user_asserted_edges_survive(self, db):
        a = _cap(db, "RC-001", ("#", "x"))
        b = _cap(db, "RC-002", ("#", "y"))
        insert_connection(db, a, b, "asserted", 1.0, "asserted",
                          relation="supports", asserted_by="user")
        db.commit()
        stats = rebuild_connections(db)
        assert stats["asserted"] == 1
        row = db.execute(
            "SELECT relation FROM connections WHERE type='asserted'"
        ).fetchone()
        assert row["relation"] == "supports"

    def test_stats_break_down_by_type(self, db):
        a = _cap(db, "RC-001", ("#", "x"), raw_ocr="see @RC-002")
        _cap(db, "RC-002", ("#", "x"))
        stats = rebuild_connections(db)
        assert stats["tag_overlap"] == 1
        assert stats["references"] == 1
        assert stats["edges"] == (stats["tag_overlap"] + stats["entity_overlap"]
                                  + stats["references"] + stats["asserted"])

    def test_pairs_stored_once_in_canonical_direction(self, db):
        a = _cap(db, "RC-001", ("#", "x"))
        b = _cap(db, "RC-002", ("#", "x"))
        rebuild_connections(db)
        rows = db.execute(
            "SELECT source_id, target_id FROM connections WHERE type='tag_overlap'"
        ).fetchall()
        assert [tuple(r) for r in rows] == [(min(a, b), max(a, b))]

    def test_performance_guard(self, db):
        """1,000 captures sharing one universal tag plus a few rare ones — the
        shape that took 410 s on the live journal — rebuilds in seconds."""
        rows = []
        for i in range(1, 1001):
            cid = insert_capture(db, "AIEX", f"AIEX-{i:04d}", {}, "t", "", 1.0)
            rows.append({"cid": cid, "i": i})
        for r in rows:
            insert_tags(db, r["cid"], [
                {"prefix": "#", "value": "rh-investigation", "role": "topic"},
                {"prefix": "$", "value": "insight", "role": "insight"},
                {"prefix": "#", "value": f"phase-{r['i'] // 5}", "role": "topic"},
            ])
        db.commit()
        t = time.monotonic()
        stats = rebuild_connections(db)
        elapsed = time.monotonic() - t
        assert elapsed < 20, f"rebuild took {elapsed:.1f}s"
        assert stats["tag_overlap"] <= 1000 * TAG_LINKS_PER_CAPTURE

    def test_build_connections_uses_same_cap(self, db):
        ids = [_cap(db, f"RC-{i:03d}", ("#", "common")) for i in range(1, 41)]
        built = build_connections(db, ids[0])
        assert len([c for c in built if c["type"] == "tag_overlap"]) == TAG_LINKS_PER_CAPTURE


# ── migrate_v38 ───────────────────────────────────────────────────────────────

class TestMigrateV38:
    def _old_db(self, tmp_path: Path) -> Path:
        """A database as the 3.7 parser left it: inline-label page with an
        empty summary and no tags from its Tags line."""
        db_path = tmp_path / "captures.db"
        init_db(db_path)
        con = get_connection(db_path)
        text = "RC-900\nFirst Impressions: stored by the old parser\nTags: #kept"
        cid = insert_capture(con, "RC", "RC-900", {"first_impressions": ""}, text, "", 1.0)
        aiex = insert_capture(con, "AIEX", "AIEX-001", {"text": "x"}, "AIEX body", "orig", 1.0)
        con.commit()
        con.close()
        self.cid, self.aiex = cid, aiex
        return db_path

    def test_reparses_handwritten_and_records_flag(self, tmp_path):
        db_path = self._old_db(tmp_path)
        assert migrate_v38(db_path) is True
        con = get_connection(db_path)
        row = con.execute(
            "SELECT summary, content_json FROM captures WHERE id=?", (self.cid,)
        ).fetchone()
        assert row["summary"] == "stored by the old parser"
        assert json.loads(row["content_json"])["first_impressions"] == "stored by the old parser"
        tags = {(r["prefix"], r["value"]) for r in con.execute(
            "SELECT prefix, value FROM tags WHERE capture_id=?", (self.cid,))}
        assert ("#", "kept") in tags
        assert get_setting(con, "migrated_v38")
        con.close()

    def test_aiex_untouched(self, tmp_path):
        db_path = self._old_db(tmp_path)
        migrate_v38(db_path)
        con = get_connection(db_path)
        row = con.execute("SELECT summary FROM captures WHERE id=?", (self.aiex,)).fetchone()
        assert row["summary"] == "orig"
        con.close()

    def test_backup_written(self, tmp_path):
        db_path = self._old_db(tmp_path)
        migrate_v38(db_path)
        assert (tmp_path / "captures.db.bak-v38").exists()

    def test_idempotent(self, tmp_path):
        db_path = self._old_db(tmp_path)
        assert migrate_v38(db_path) is True
        assert migrate_v38(db_path) is False

    def test_fresh_and_missing_db_are_noops(self, tmp_path):
        fresh = tmp_path / "fresh.db"
        init_db(fresh)
        assert migrate_v38(fresh) is False
        assert not (tmp_path / "fresh.db.bak-v38").exists()
        assert migrate_v38(tmp_path / "missing.db") is False
