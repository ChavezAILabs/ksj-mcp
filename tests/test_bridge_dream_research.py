"""
Tests for bridge_dream_research + commit_observation (ksj-mcp DC companion
— the third and final of the SYN/REV/DC AI-companion pairs).

Covers:
  - precondition: DC page must exist, no override flag
  - composes dream_correlation's get_dream_cooccurrence() rather than
    reimplementing the co-occurrence check (chain 1)
  - guardrail phrasing carried through: match count, base rate, never the
    word "correlation", no significance language
  - OCR-confidence warning
  - commit_observation: never mutates the DC page's own narrative, stores
    source='ai_extract', links via an asserted 'observes' edge with
    asserted_by='user' (survives rebuild_connections)
  - named "observation" not "inference" deliberately (chat-side review,
    2026-08-03): at journal scale the artifact is a noticing, not a
    conclusion the data could support calling an inference
  - 'observes' is also usable via the general-purpose assert_connection()
"""
import json

import ksj_mcp.server as srv
from ksj_mcp.connections import rebuild_connections
from ksj_mcp.database import get_connection, init_db, insert_capture, insert_tags


def _cap(con, template_id, raw, tags=None, confidence=1.0, content=None, days_ago=0):
    type_ = template_id.split("-")[0]
    c = content if content is not None else {"raw": raw}
    cid = insert_capture(con, type_, template_id, c, raw, raw[:60], confidence)
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


def _dc(con, template_id, themes, symbols=None, confidence=1.0, created_days_ago=0, **content_overrides):
    tags = [_tag("#", t, "theme") for t in themes]
    if symbols:
        tags += [_tag("@", s, "entity") for s in symbols]
    content = {
        "dream_narrative": "a dream",
        "symbols": ", ".join(symbols or []),
        "emotions": "",
        "current_events": "",
    }
    content.update(content_overrides)
    return _cap(con, template_id, "a dream", tags=tags, confidence=confidence, content=content,
                days_ago=created_days_ago)


class TestPrecondition:
    def test_no_dc_page_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-999")

        assert "No Dream Capture page found for DC-999" in result
        assert "dream_patterns" in result

    def test_wrong_type_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "hello")
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="RC-001")

        assert "No Dream Capture page found" in result

    def test_no_override_flag(self):
        import inspect
        sig = inspect.signature(srv.bridge_dream_research)
        assert "force" not in sig.parameters


class TestComposesDreamCorrelation:
    def test_uses_shared_cooccurrence_data(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "convergence work", tags=[_tag("#", "flying", "topic")], days_ago=1)
        _dc(con, "DC-001", ["flying"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-001")

        assert "RC-001" in result
        assert "1 matched pair at" in result

    def test_pluralizes_matched_pairs(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "note one", tags=[_tag("#", "flying", "topic")], days_ago=1)
        _cap(con, "RC-002", "note two", tags=[_tag("#", "flying", "topic")], days_ago=2)
        _dc(con, "DC-001", ["flying"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-001")

        assert "2 matched pairs at" in result

    def test_only_pairs_for_this_dc_entry_shown(self, tmp_path):
        """A different DC entry sharing the same tag must not leak into
        this one's echo list — get_dream_cooccurrence's pairs are filtered
        to dc_id == this capture's id."""
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "note", tags=[_tag("#", "flying", "topic")], days_ago=1)
        _dc(con, "DC-001", ["flying"])
        _dc(con, "DC-002", ["flying"], created_days_ago=30)  # a much older, unrelated dream sharing the tag
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-002")

        # DC-002 has no RC entry within 3 days of ITS OWN creation, even
        # though DC-001 does.
        assert "0 matched pairs at" in result


class TestGuardrailsCarried:
    def test_never_says_correlation(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "note", tags=[_tag("#", "flying", "topic")])
        _dc(con, "DC-001", ["flying"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-001")

        assert "correlation" not in result.lower()
        assert "co-occurrence" in result.lower()

    def test_no_significance_language(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "note", tags=[_tag("#", "flying", "topic")])
        _dc(con, "DC-001", ["flying"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-001")

        for forbidden in ("p-value", "p=", "significant", "statistically"):
            assert forbidden not in result.lower()

    def test_base_rate_shown_per_theme(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "note", tags=[_tag("#", "flying", "topic")])
        _cap(con, "RC-002", "unrelated", tags=[_tag("#", "unrelated", "topic")])
        _dc(con, "DC-001", ["flying"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-001")

        assert "base rate" in result
        assert "1/2" in result  # 1 of 2 RC/REV entries carry #flying

    def test_no_themes_no_symbols_still_offers_dialogue(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _dc(con, "DC-001", [])
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-001")

        assert "no #theme tags" in result.lower() or "nothing to check" in result.lower()
        assert "anything about this dream they want to reflect on" in result


class TestOcrWarning:
    def test_warns_on_uncorrected_low_confidence(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _dc(con, "DC-001", ["flying"], confidence=0.4)
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-001")

        assert "uncorrected and low-confidence" in result


class TestDepthParam:
    def test_invalid_depth_defaults_to_standard(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _dc(con, "DC-001", ["flying"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.bridge_dream_research(dc_template_id="DC-001", depth="bogus")

        assert "Depth: standard" in result


class TestNoDbWrites:
    def test_no_writes(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _dc(con, "DC-001", ["flying"])
        before = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        con.close()
        srv._DB_PATH = db_path

        srv.bridge_dream_research(dc_template_id="DC-001")

        con = get_connection(db_path)
        after = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        con.close()
        assert after == before


# ── commit_observation ──────────────────────────────────────────────────────

def _payload(**overrides):
    data = {
        "dc_template_id": "DC-001",
        "date": "2026-08-03",
        "observation": "The flying dream lined up with the convergence-proof push, and the user "
                        "confirmed the skyscraper symbol tracks with feeling overwhelmed by scale.",
        "confirmed_echoes": ["RC-001: the convergence-proof timing felt real, not coincidental"],
        "symbol_notes": ["skyscraper: represents feeling overwhelmed by the scope of the work"],
        "tags": ["#flying"],
    }
    data.update(overrides)
    return json.dumps(data)


class TestCommitValidation:
    def test_invalid_json(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        assert "Invalid JSON" in srv.commit_observation("not valid json {{")

    def test_missing_dc_template_id(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_observation(json.dumps({"observation": "text"}))
        assert "dc_template_id" in result

    def test_missing_observation_text(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_observation(json.dumps({"dc_template_id": "DC-001"}))
        assert "observation" in result.lower()

    def test_no_dc_page_declines_and_writes_nothing(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_observation(_payload(dc_template_id="DC-999"))

        assert "No Dream Capture page found for DC-999" in result
        with get_connection(db_path) as con:
            n = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        assert n == 0


class TestCommit:
    def _seed(self, con):
        return _dc(con, "DC-001", ["flying"])

    def test_basic_commit(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        result = srv.commit_observation(_payload())

        assert "AIEX-001" in result
        assert "Observes : DC-001" in result

    def test_does_not_mutate_dream_narrative(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        dc_id = self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_observation(_payload())

        with get_connection(db_path) as con:
            row = con.execute("SELECT content_json FROM captures WHERE id=?", (dc_id,)).fetchone()
        assert json.loads(row["content_json"])["dream_narrative"] == "a dream"

    def test_source_is_ai_extract(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_observation(_payload())

        with get_connection(db_path) as con:
            row = con.execute("SELECT source FROM captures WHERE template_id='AIEX-001'").fetchone()
        assert row["source"] == "ai_extract"

    def test_never_creates_a_dc_template_id(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_observation(_payload())

        with get_connection(db_path) as con:
            rows = con.execute("SELECT type, template_id FROM captures").fetchall()
        types = {r["template_id"]: r["type"] for r in rows}
        assert types["AIEX-001"] == "AIEX"
        assert "DC-002" not in types

    def test_content_uses_observation_field_name(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_observation(_payload())

        with get_connection(db_path) as con:
            row = con.execute("SELECT content_json FROM captures WHERE template_id='AIEX-001'").fetchone()
        content = json.loads(row["content_json"])
        assert "observation" in content
        assert "inference" not in content


class TestObservesEdge:
    def test_edge_created_with_correct_shape(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        dc_id = _dc(con, "DC-001", ["flying"])
        con.close()
        srv._DB_PATH = db_path

        srv.commit_observation(_payload())

        with get_connection(db_path) as con:
            aiex_id = con.execute("SELECT id FROM captures WHERE template_id='AIEX-001'").fetchone()["id"]
            edge = con.execute(
                "SELECT * FROM connections WHERE type='asserted' AND relation='observes'"
            ).fetchone()
        assert edge is not None
        assert edge["source_id"] == aiex_id
        assert edge["target_id"] == dc_id
        assert edge["asserted_by"] == "user"

    def test_survives_rebuild_connections(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _dc(con, "DC-001", ["flying"])
        con.close()
        srv._DB_PATH = db_path

        srv.commit_observation(_payload())

        with get_connection(db_path) as con:
            rebuild_connections(con)
            edge = con.execute(
                "SELECT * FROM connections WHERE type='asserted' AND relation='observes'"
            ).fetchone()
        assert edge is not None, "observes edge was wiped by rebuild_connections"


class TestAssertConnectionAcceptsObserves:
    def test_manual_observes_assertion(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        a = _cap(con, "AIEX-001", "an observation entry")
        b = _cap(con, "DC-001", "a dream page")
        con.close()
        srv._DB_PATH = db_path

        result = srv.assert_connection(source_id=a, target_id=b, relation="observes")

        assert "observes" in result
        with get_connection(db_path) as con:
            edge = con.execute("SELECT * FROM connections WHERE relation='observes'").fetchone()
        assert edge["source_id"] == a and edge["target_id"] == b
