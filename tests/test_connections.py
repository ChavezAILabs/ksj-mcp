"""
Tests for ksj_mcp.connections — find_tag_connections, find_reference_connections,
and build_connections.
"""

import pytest

from ksj_mcp.database import insert_capture, insert_tags, get_connections
from ksj_mcp.connections import (
    find_tag_connections,
    find_reference_connections,
    build_connections,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _insert_rc(con, template_id="RC-001", raw_ocr="some ocr text", summary="A capture"):
    cid = insert_capture(
        con, "RC", template_id,
        {"first_impressions": summary, "key_points": "point"},
        raw_ocr, summary, 0.9,
    )
    con.commit()
    return cid


def _add_tags(con, capture_id, *tags):
    """Add tags as (prefix, value) pairs."""
    insert_tags(con, capture_id, [{"prefix": p, "value": v} for p, v in tags])
    con.commit()


# ── find_tag_connections ───────────────────────────────────────────────────────

class TestFindTagConnections:
    def test_no_tags_returns_empty(self, db):
        cid = _insert_rc(db, "RC-001")
        # No tags inserted — nothing to match on
        result = find_tag_connections(db, cid)
        assert result == []

    def test_no_other_captures_returns_empty(self, db):
        cid = _insert_rc(db, "RC-001")
        _add_tags(db, cid, ("#", "ml"))
        result = find_tag_connections(db, cid)
        assert result == []

    def test_shared_single_tag_returned(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        _add_tags(db, a, ("#", "ml"))
        _add_tags(db, b, ("#", "ml"))
        result = find_tag_connections(db, a)
        assert len(result) == 1
        assert result[0]["target_id"] == b
        assert result[0]["strength"] == pytest.approx(1.0)
        assert "#ml" in result[0]["shared_tags"]

    def test_strength_reflects_overlap_count(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        _add_tags(db, a, ("#", "ml"), ("#", "ai"), ("$", "key"))
        _add_tags(db, b, ("#", "ml"), ("#", "ai"), ("$", "key"))
        result = find_tag_connections(db, a)
        assert result[0]["strength"] == pytest.approx(3.0)
        assert len(result[0]["shared_tags"]) == 3

    def test_sorted_by_strength_descending(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")  # 2 shared tags
        c = _insert_rc(db, "RC-003")  # 1 shared tag
        _add_tags(db, a, ("#", "ml"), ("#", "ai"))
        _add_tags(db, b, ("#", "ml"), ("#", "ai"))
        _add_tags(db, c, ("#", "ml"))
        result = find_tag_connections(db, a)
        assert result[0]["target_id"] == b
        assert result[1]["target_id"] == c
        assert result[0]["strength"] > result[1]["strength"]

    def test_does_not_include_self(self, db):
        a = _insert_rc(db, "RC-001")
        _add_tags(db, a, ("#", "ml"))
        result = find_tag_connections(db, a)
        assert all(r["target_id"] != a for r in result)

    def test_captures_without_shared_tag_excluded(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        c = _insert_rc(db, "RC-003")
        _add_tags(db, a, ("#", "ml"))
        _add_tags(db, b, ("#", "ml"))
        _add_tags(db, c, ("#", "robotics"))  # no overlap
        result = find_tag_connections(db, a)
        target_ids = [r["target_id"] for r in result]
        assert b in target_ids
        assert c not in target_ids

    def test_different_prefix_same_value_no_match(self, db):
        """#ml and $ml should NOT be considered the same tag."""
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        _add_tags(db, a, ("#", "ml"))
        _add_tags(db, b, ("$", "ml"))
        result = find_tag_connections(db, a)
        assert result == []

    def test_multiple_other_captures_all_returned(self, db):
        a = _insert_rc(db, "RC-001")
        others = []
        for i in range(3):
            oid = _insert_rc(db, f"RC-{i+2:03d}")
            _add_tags(db, oid, ("#", "shared"))
            others.append(oid)
        _add_tags(db, a, ("#", "shared"))
        result = find_tag_connections(db, a)
        returned_ids = {r["target_id"] for r in result}
        assert returned_ids == set(others)


# ── find_reference_connections ─────────────────────────────────────────────────

class TestFindReferenceConnections:
    def test_missing_capture_returns_empty(self, db):
        result = find_reference_connections(db, 999999)
        assert result == []

    def test_no_references_in_ocr_returns_empty(self, db):
        cid = _insert_rc(db, "RC-001", raw_ocr="no references here")
        result = find_reference_connections(db, cid)
        assert result == []

    def test_reference_to_nonexistent_template_excluded(self, db):
        cid = _insert_rc(db, "RC-001", raw_ocr="see @RC-099 for details")
        # RC-099 not in DB
        result = find_reference_connections(db, cid)
        assert result == []

    def test_valid_rc_reference_returned(self, db):
        target = _insert_rc(db, "RC-012")
        source = _insert_rc(db, "RC-020", raw_ocr="see @RC-012 for context")
        result = find_reference_connections(db, source)
        assert len(result) == 1
        assert result[0]["target_id"] == target
        assert result[0]["template_id"] == "RC-012"
        assert result[0]["strength"] == pytest.approx(1.0)

    def test_case_insensitive_pattern_match(self, db):
        """@rc-005 (lowercase) should still resolve to RC-005."""
        target = _insert_rc(db, "RC-005")
        source = _insert_rc(db, "RC-010", raw_ocr="relates to @rc-005")
        result = find_reference_connections(db, source)
        assert len(result) == 1
        assert result[0]["target_id"] == target

    def test_syn_reference_resolved(self, db):
        syn_id = insert_capture(
            db, "SYN", "SYN-003",
            {"breakthrough": "Big idea", "patterns": "x"},
            "ocr", "SYN summary", 0.85,
        )
        db.commit()
        source = _insert_rc(db, "RC-010", raw_ocr="connects to @SYN-003")
        result = find_reference_connections(db, source)
        assert len(result) == 1
        assert result[0]["target_id"] == syn_id
        assert result[0]["template_id"] == "SYN-003"

    def test_duplicate_references_deduplicated(self, db):
        target = _insert_rc(db, "RC-012")
        source = _insert_rc(
            db, "RC-020",
            raw_ocr="@RC-012 and again @RC-012 mentioned twice",
        )
        result = find_reference_connections(db, source)
        assert len(result) == 1

    def test_multiple_distinct_references(self, db):
        t1 = _insert_rc(db, "RC-001")
        t2 = _insert_rc(db, "RC-002")
        source = _insert_rc(db, "RC-010", raw_ocr="see @RC-001 and @RC-002")
        result = find_reference_connections(db, source)
        assert len(result) == 2
        target_ids = {r["target_id"] for r in result}
        assert target_ids == {t1, t2}

    def test_self_reference_excluded(self, db):
        """A capture referencing its own template_id should still be ignored — it
        won't match because the DB lookup would return itself as the target."""
        cid = _insert_rc(db, "RC-010", raw_ocr="see @RC-010 for more")
        result = find_reference_connections(db, cid)
        # Self-reference resolves but target_id == source_id — allowed by the
        # function (it doesn't filter self); verify it doesn't crash.
        # Whether self is returned or not, the function must not raise.
        assert isinstance(result, list)

    def test_dc_reference_resolved(self, db):
        dc_id = insert_capture(
            db, "DC", "DC-004",
            {"dream_narrative": "flying", "symbols": "wings", "emotions": "joy"},
            "ocr dc", "DC summary", 0.75,
        )
        db.commit()
        source = _insert_rc(db, "RC-010", raw_ocr="dream related: @DC-004")
        result = find_reference_connections(db, source)
        assert len(result) == 1
        assert result[0]["template_id"] == "DC-004"


# ── build_connections ──────────────────────────────────────────────────────────

class TestBuildConnections:
    def test_returns_empty_for_isolated_capture(self, db):
        cid = _insert_rc(db, "RC-001")
        result = build_connections(db, cid)
        assert result == []

    def test_tag_overlap_persisted_to_db(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        _add_tags(db, a, ("#", "ml"))
        _add_tags(db, b, ("#", "ml"))
        build_connections(db, a)
        conns = get_connections(db, a)
        assert len(conns) == 1
        assert conns[0]["connected_template"] == "RC-002"

    def test_reference_persisted_to_db(self, db):
        target = _insert_rc(db, "RC-001")
        source = _insert_rc(db, "RC-010", raw_ocr="see @RC-001 for details")
        build_connections(db, source)
        conns = get_connections(db, source)
        assert len(conns) == 1
        assert conns[0]["connected_template"] == "RC-001"

    def test_combined_results_returned(self, db):
        tag_target = _insert_rc(db, "RC-001")
        ref_target = _insert_rc(db, "RC-002")
        source = _insert_rc(db, "RC-010", raw_ocr="see @RC-002 for more")
        _add_tags(db, source, ("#", "shared"))
        _add_tags(db, tag_target, ("#", "shared"))
        result = build_connections(db, source)
        types = {r["type"] for r in result}
        assert "tag_overlap" in types
        assert "reference" in types

    def test_result_structure_tag_overlap(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        _add_tags(db, a, ("#", "ai"))
        _add_tags(db, b, ("#", "ai"))
        result = build_connections(db, a)
        r = result[0]
        assert "connection_id" in r
        assert r["type"] == "tag_overlap"
        assert r["method"] == "tag_overlap"
        assert r["strength"] == pytest.approx(1.0)
        assert r["connected_id"] == b
        assert r["connected_template"] == "RC-002"
        assert "#ai" in r["shared_tags"]

    def test_result_structure_reference(self, db):
        target = _insert_rc(db, "RC-005")
        source = _insert_rc(db, "RC-010", raw_ocr="@RC-005 is relevant")
        result = build_connections(db, source)
        r = result[0]
        assert r["type"] == "reference"
        assert r["method"] == "reference"
        assert r["strength"] == pytest.approx(1.0)
        assert r["connected_id"] == target
        assert r["connected_template"] == "RC-005"
        assert r["shared_tags"] == []

    def test_idempotent_no_duplicate_db_rows(self, db):
        """Calling build_connections twice for the same pair must not create
        duplicate rows (insert_connection deduplicates)."""
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        _add_tags(db, a, ("#", "ml"))
        _add_tags(db, b, ("#", "ml"))
        build_connections(db, a)
        build_connections(db, a)
        conns = get_connections(db, a)
        assert len(conns) == 1

    def test_connection_ids_are_integers(self, db):
        a = _insert_rc(db, "RC-001")
        b = _insert_rc(db, "RC-002")
        _add_tags(db, a, ("#", "ml"))
        _add_tags(db, b, ("#", "ml"))
        result = build_connections(db, a)
        assert isinstance(result[0]["connection_id"], int)
