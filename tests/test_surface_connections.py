"""
Tests for surface_connections (ksj-mcp 3.2, SYN companion — Phase 1 scan).

Covers the Option B decision + amendments implemented so far
(ksj2_documents/SYN_companion_decision_optionB_2026-08-03.md):
  §3.1 — precondition: SYN page must exist, no override flag
  §3.2 — blind scan: SYN content withheld from Part A, only appears in Part B
  §5.3 — cluster resolution: unambiguous auto-match, explicit entry_ids,
         decline on zero/multiple candidate clusters
  §5.2 — OCR-confidence warning on the SYN page (warn, don't block)
  Chain 2 (§2) — find_unapplied composed across the cluster, no new gap logic

Phase 2 dialogue text and Part A/B formatting are exercised as substring
checks — the actual dialogue is conducted by the calling Claude in-context,
not by this tool, so there is nothing further to unit-test there.
"""
import inspect

import ksj_mcp.server as srv
from ksj_mcp.database import get_connection, init_db, insert_capture, insert_tags


def _cap(con, template_id, raw, tags=None, confidence=1.0, days_ago=0):
    type_ = template_id.split("-")[0]
    cid = insert_capture(con, type_, template_id, {"raw": raw}, raw, raw[:60], confidence)
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


class TestPrecondition:
    def test_no_syn_page_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-999")

        assert "No Synthesis page found for SYN-999" in result
        assert "suggest_synthesis" in result

    def test_wrong_type_declines(self, tmp_path):
        """An RC template ID passed as syn_template_id must not match."""
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "hello")
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="RC-001")

        assert "No Synthesis page found" in result

    def test_no_override_flag(self):
        """§3.1: a force= escape hatch would reintroduce Option A. There must
        not be one."""
        sig = inspect.signature(srv.surface_connections)
        assert "force" not in sig.parameters


class TestClusterResolution:
    def test_auto_resolve_single_match(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "notes on systems thinking", [_tag("#", "systems", "topic")])
        _cap(con, "RC-002", "more systems notes", [_tag("#", "systems", "topic")])
        _cap(con, "RC-003", "systems again", [_tag("#", "systems", "topic")])
        _cap(con, "SYN-001", "synthesis of systems", [_tag("#", "systems", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001")

        assert "RC-001" in result and "RC-002" in result and "RC-003" in result
        assert "PART A" in result
        assert "PART B" in result

    def test_zero_match_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "SYN-001", "synthesis with no matching cluster", [_tag("#", "unique-tag", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001")

        assert "can't be resolved automatically" in result
        assert "entry_ids" in result

    def test_ambiguous_match_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "a", [_tag("#", "alpha", "topic")])
        _cap(con, "RC-002", "b", [_tag("#", "alpha", "topic")])
        _cap(con, "RC-003", "c", [_tag("#", "beta", "topic")])
        _cap(con, "RC-004", "d", [_tag("#", "beta", "topic")])
        _cap(con, "SYN-001", "synthesizes both", [_tag("#", "alpha", "topic"), _tag("#", "beta", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001")

        assert "more than one RC cluster" in result
        assert "#alpha" in result and "#beta" in result

    def test_explicit_entry_ids_used_directly(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "alpha note", [_tag("#", "alpha", "topic")])
        _cap(con, "RC-002", "beta note", [_tag("#", "beta", "topic")])
        _cap(con, "SYN-001", "synthesis", [])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001", entry_ids="RC-001,RC-002")

        assert "RC-001" in result and "RC-002" in result
        assert "explicit entry_ids" in result

    def test_missing_explicit_entry_id_errors(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "alpha note", [_tag("#", "alpha", "topic")])
        _cap(con, "SYN-001", "synthesis", [])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001", entry_ids="RC-001,RC-999")

        assert "No RC capture found for: RC-999" in result

    def test_fewer_than_two_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "alpha note", [_tag("#", "alpha", "topic")])
        _cap(con, "SYN-001", "synthesis", [])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001", entry_ids="RC-001")

        assert "needs at least two" in result


class TestDaysFilter:
    def test_days_narrows_cluster(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "recent", [_tag("#", "gamma", "topic")], days_ago=1)
        _cap(con, "RC-002", "recent too", [_tag("#", "gamma", "topic")], days_ago=2)
        _cap(con, "RC-003", "old", [_tag("#", "gamma", "topic")], days_ago=60)
        _cap(con, "SYN-001", "synthesis", [_tag("#", "gamma", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001", days=14)

        part_a = result.split("PART B", 1)[0]
        assert "RC-001" in part_a and "RC-002" in part_a
        assert "RC-003" not in part_a

    def test_days_filter_can_drop_below_two(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "recent", [_tag("#", "delta", "topic")], days_ago=1)
        _cap(con, "RC-002", "old", [_tag("#", "delta", "topic")], days_ago=60)
        _cap(con, "RC-003", "old too", [_tag("#", "delta", "topic")], days_ago=90)
        _cap(con, "SYN-001", "synthesis", [_tag("#", "delta", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001", days=7)

        assert "needs at least two" in result


class TestBlindScan:
    def test_syn_text_not_in_part_a(self, tmp_path):
        """§3.2: the SYN page's own unique content must not leak into Part A,
        only appear afterward in Part B."""
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "alpha note one", [_tag("#", "epsilon", "topic")])
        _cap(con, "RC-002", "alpha note two", [_tag("#", "epsilon", "topic")])
        _cap(con, "SYN-001", "UNIQUE_SYN_MARKER_TEXT_998877", [_tag("#", "epsilon", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001")

        part_a = result.split("PART B", 1)[0]
        assert "UNIQUE_SYN_MARKER_TEXT_998877" not in part_a
        assert "UNIQUE_SYN_MARKER_TEXT_998877" in result


class TestOcrWarning:
    def test_warns_on_uncorrected_low_confidence(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "a", [_tag("#", "zeta", "topic")])
        _cap(con, "RC-002", "b", [_tag("#", "zeta", "topic")])
        _cap(con, "SYN-001", "synthesis", [_tag("#", "zeta", "topic")], confidence=0.4)
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001")

        assert "uncorrected and low-confidence" in result

    def test_no_warning_when_confidence_high(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "a", [_tag("#", "eta", "topic")])
        _cap(con, "RC-002", "b", [_tag("#", "eta", "topic")])
        _cap(con, "SYN-001", "synthesis", [_tag("#", "eta", "topic")], confidence=0.95)
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001")

        assert "uncorrected and low-confidence" not in result

    def test_no_warning_when_corrected(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "a", [_tag("#", "theta", "topic")])
        _cap(con, "RC-002", "b", [_tag("#", "theta", "topic")])
        syn_id = _cap(con, "SYN-001", "synthesis", [_tag("#", "theta", "topic")], confidence=0.3)
        con.execute("UPDATE captures SET corrected_ocr=? WHERE id=?", ("corrected text", syn_id))
        con.commit()
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001")

        assert "uncorrected and low-confidence" not in result


class TestGapCandidates:
    def test_find_unapplied_composed_across_cluster(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        # An old, uncited $insight sharing a rare tag with the cluster —
        # exactly the propagation-failure shape find_unapplied looks for.
        _cap(con, "RC-050", "an old insight", [_tag("$", "key-finding", "insight")], days_ago=100)
        _cap(con, "RC-001", "new note", [_tag("#", "iota", "topic"), _tag("$", "key-finding", "insight")])
        _cap(con, "RC-002", "another new note", [_tag("#", "iota", "topic")])
        _cap(con, "SYN-001", "synthesis", [_tag("#", "iota", "topic")])
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001")

        assert "Gap candidates" in result
        assert "RC-050" in result


class TestDepthParam:
    def _seed(self, con):
        _cap(con, "RC-001", "a", [_tag("#", "kappa", "topic")])
        _cap(con, "RC-002", "b", [_tag("#", "kappa", "topic")])
        _cap(con, "SYN-001", "synthesis", [_tag("#", "kappa", "topic")])

    def test_invalid_depth_defaults_to_standard(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001", depth="bogus")

        assert "Depth: standard" in result

    def test_deep_depth_reflected(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        result = srv.surface_connections(syn_template_id="SYN-001", depth="deep")

        assert "Depth: deep" in result
        assert "up to three or four" in result


class TestNoDbWrites:
    def test_no_captures_or_connections_written(self, tmp_path):
        """Mirrors extract_insights: this tool only reads and prepares a
        prompt, it must never write to the database."""
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "a", [_tag("#", "lambda", "topic")])
        _cap(con, "RC-002", "b", [_tag("#", "lambda", "topic")])
        _cap(con, "SYN-001", "synthesis", [_tag("#", "lambda", "topic")])
        before_captures = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        before_connections = con.execute("SELECT COUNT(*) AS n FROM connections").fetchone()["n"]
        con.close()
        srv._DB_PATH = db_path

        srv.surface_connections(syn_template_id="SYN-001")

        con = get_connection(db_path)
        after_captures = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        after_connections = con.execute("SELECT COUNT(*) AS n FROM connections").fetchone()["n"]
        con.close()
        assert after_captures == before_captures
        assert after_connections == before_connections
