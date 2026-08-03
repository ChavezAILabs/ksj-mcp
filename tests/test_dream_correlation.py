"""
Tests for dream_correlation (ksj-mcp 3.x spec §1.10a(c) — the deferred
"build, but with discipline" item, now a chain-1 prerequisite for the DC
companion pair).

Covers the spec's non-negotiable guardrails:
  - output text never says "correlation" (co-occurrence only)
  - window_days and match count always reported
  - base rate always reported for both populations
  - no p-values / significance language
plus the underlying get_dream_cooccurrence() data shape (window matching,
direction, both-population base rates).
"""
import ksj_mcp.server as srv
from ksj_mcp.database import get_connection, get_dream_cooccurrence, init_db, insert_capture, insert_tags


def _cap(con, template_id, raw, tags=None, days_ago=0):
    type_ = template_id.split("-")[0]
    cid = insert_capture(con, type_, template_id, {"raw": raw}, raw, raw[:60], 1.0)
    if days_ago:
        from datetime import datetime, timedelta, timezone
        ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        con.execute("UPDATE captures SET created_at=? WHERE id=?", (ts, cid))
    if tags:
        insert_tags(con, cid, tags)
    con.commit()
    return cid


def _tag(prefix, value, role=None):
    return {"prefix": prefix, "value": value, "display": value, "role": role}


class TestValidation:
    def test_empty_tag(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        assert "Please provide a tag" in srv.dream_correlation(tag="")

    def test_negative_window(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        assert "window_days" in srv.dream_correlation(tag="anxiety", window_days=-1)


class TestNoOutputWordCorrelation:
    def test_output_never_says_correlation(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "DC-001", "a dream", [_tag("#", "anxiety", "theme")])
        _cap(con, "RC-001", "a note", [_tag("#", "anxiety", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.dream_correlation(tag="anxiety")

        assert "correlation" not in result.lower()
        assert "co-occurrence" in result.lower()

    def test_no_significance_language(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "DC-001", "a dream", [_tag("#", "anxiety", "theme")])
        _cap(con, "RC-001", "a note", [_tag("#", "anxiety", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.dream_correlation(tag="anxiety")

        for forbidden in ("p-value", "p=", "significant", "statistically"):
            assert forbidden not in result.lower()


class TestGuardrailReporting:
    def test_window_days_and_match_count_always_shown(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "DC-001", "a dream", [_tag("#", "anxiety", "theme")])
        _cap(con, "RC-001", "a note", [_tag("#", "anxiety", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.dream_correlation(tag="anxiety", window_days=5)

        assert "window = 5 day(s)" in result
        assert "n = " in result

    def test_base_rate_reported_for_both_populations(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "DC-001", "a dream", [_tag("#", "anxiety", "theme")])
        _cap(con, "DC-002", "an unrelated dream", [_tag("#", "flying", "theme")])
        _cap(con, "RC-001", "a note", [_tag("#", "anxiety", "topic")])
        _cap(con, "RC-002", "another note", [_tag("#", "unrelated", "topic")])
        _cap(con, "RC-003", "a third note", [_tag("#", "unrelated", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.dream_correlation(tag="anxiety")

        assert "1/2 (50%) DC entries" in result
        assert "1/3 (33%) RC/REV entries" in result


class TestNoMatches:
    def test_no_dc_entries_with_tag(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "a note", [_tag("#", "anxiety", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.dream_correlation(tag="anxiety")

        assert "No DC entries carry #anxiety" in result

    def test_no_other_entries_with_tag(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "DC-001", "a dream", [_tag("#", "anxiety", "theme")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.dream_correlation(tag="anxiety")

        assert "No RC/REV entries carry #anxiety" in result


class TestGetDreamCooccurrence:
    def test_pair_within_window_matched(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "DC-001", "dream", [_tag("#", "anxiety", "theme")], days_ago=0)
        _cap(con, "RC-001", "note", [_tag("#", "anxiety", "topic")], days_ago=2)

        data = get_dream_cooccurrence(con, "anxiety", window_days=3)

        assert len(data["pairs"]) == 1
        assert data["pairs"][0]["day_gap"] == 2
        assert data["pairs"][0]["direction"] == "before"  # RC-001 (2 days ago) is before the dream

    def test_pair_outside_window_excluded(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "DC-001", "dream", [_tag("#", "anxiety", "theme")], days_ago=0)
        _cap(con, "RC-001", "note", [_tag("#", "anxiety", "topic")], days_ago=10)

        data = get_dream_cooccurrence(con, "anxiety", window_days=3)

        assert len(data["pairs"]) == 0

    def test_direction_after(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "DC-001", "dream", [_tag("#", "anxiety", "theme")], days_ago=5)
        _cap(con, "RC-001", "note", [_tag("#", "anxiety", "topic")], days_ago=3)

        data = get_dream_cooccurrence(con, "anxiety", window_days=3)

        assert data["pairs"][0]["direction"] == "after"  # RC-001 (3 days ago) is AFTER the dream (5 days ago)

    def test_case_insensitive_tag_match(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "DC-001", "dream", [_tag("#", "Anxiety", "theme")])
        _cap(con, "RC-001", "note", [_tag("#", "anxiety", "topic")])

        data = get_dream_cooccurrence(con, "ANXIETY", window_days=3)

        assert len(data["dc_matches"]) == 1
        assert len(data["other_matches"]) == 1
