"""
Tests for audit_knowledge_status + commit_assessment (ksj-mcp REV companion
— the second of the SYN/REV/DC AI-companion pairs, following the pattern
established by surface_connections/commit_distillation).

Covers:
  - precondition: REV page must exist, no override flag
  - Needs Work claims are not auditable (no completeness assertion to check)
  - Solid/Mastered claims checked against get_topic_evidence_gaps
    (open questions, uncited insights) — composed from existing predicates,
    not new logic
  - OCR-confidence warning
  - commit_assessment: never mutates the REV page's own status, stores
    source='ai_extract', links via an asserted 'assesses' edge with
    asserted_by='user' (survives rebuild_connections)
  - 'assesses' is also usable via the general-purpose assert_connection()
"""
import json

import ksj_mcp.server as srv
from ksj_mcp.connections import rebuild_connections
from ksj_mcp.database import get_connection, init_db, insert_capture, insert_tags


def _cap(con, template_id, raw, tags=None, confidence=1.0, content=None):
    type_ = template_id.split("-")[0]
    c = content if content is not None else {"raw": raw}
    cid = insert_capture(con, type_, template_id, c, raw, raw[:60], confidence)
    if tags:
        insert_tags(con, cid, tags)
    con.commit()
    return cid


def _tag(prefix, value, role=None):
    return {"prefix": prefix, "value": value, "display": value, "role": role}


def _rev(con, template_id, status, topics, confidence=1.0):
    return _cap(
        con, template_id, f"review of {', '.join(topics)}",
        tags=[_tag("#", t, "topic") for t in topics],
        confidence=confidence,
        content={"knowledge_status": status},
    )


class TestPrecondition:
    def test_no_rev_page_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-999")

        assert "No Review page found for REV-999" in result
        assert "knowledge_progress" in result

    def test_wrong_type_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "hello")
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="RC-001")

        assert "No Review page found" in result

    def test_no_topics_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "REV-001", "a review", content={"knowledge_status": "Solid"})
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001")

        assert "nothing to audit" in result

    def test_no_status_declines(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "REV-001", "a review", tags=[_tag("#", "calculus", "topic")], content={"knowledge_status": ""})
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001")

        assert "no Knowledge Status" in result


class TestAuditLogic:
    def test_needs_work_not_auditable(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _rev(con, "REV-001", "Needs Work", ["calculus"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001")

        assert "not auditable" in result
        assert "No topics were flagged" in result

    def test_mastered_with_open_questions_is_flagged(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        # Open questions tagged #calculus, never connected to a $ insight.
        _cap(con, "RC-001", "still not sure about this",
             tags=[_tag("#", "calculus", "topic"), _tag("?", "why-does-this-converge", "question")])
        _cap(con, "RC-002", "another open thread",
             tags=[_tag("#", "calculus", "topic"), _tag("?", "what-about-edge-cases", "question")])
        _rev(con, "REV-001", "Mastered", ["calculus"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001")

        assert "Verdict: FLAGGED" in result
        assert "why-does-this-converge" in result
        assert "what-about-edge-cases" in result
        assert "#calculus" in result

    def test_mastered_with_uncited_insight_is_flagged(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "a key finding never referenced elsewhere",
             tags=[_tag("#", "calculus", "topic"), _tag("$", "key-finding", "insight")])
        _rev(con, "REV-001", "Mastered", ["calculus"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001")

        assert "Verdict: FLAGGED" in result
        assert "Uncited insights" in result
        assert "key-finding" in result

    def test_mastered_with_no_gaps_is_consistent(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _rev(con, "REV-001", "Mastered", ["calculus"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001")

        assert "Verdict: CONSISTENT" in result
        assert "No topics were flagged" in result

    def test_multi_topic_mixed_verdicts(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "open question",
             tags=[_tag("#", "calculus", "topic"), _tag("?", "unresolved-thread", "question")])
        _rev(con, "REV-001", "Mastered", ["calculus", "linear-algebra"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001")

        assert "#calculus" in result and "Verdict: FLAGGED" in result
        assert "#linear-algebra" in result and "Verdict: CONSISTENT" in result

    def test_open_question_answered_elsewhere_not_flagged(self, tmp_path):
        """A ? question WITH a connected $ insight is resolved evidence, not a gap."""
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        q_id = _cap(con, "RC-001", "an open question, but see @RC-002",
                    tags=[_tag("#", "calculus", "topic"), _tag("?", "resolved-thread", "question")])
        _cap(con, "RC-002", "the answer", tags=[_tag("$", "the-answer", "insight")])
        from ksj_mcp.connections import build_connections
        build_connections(con, q_id)
        _rev(con, "REV-001", "Mastered", ["calculus"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001")

        assert "Verdict: CONSISTENT" in result


class TestOcrWarning:
    def test_warns_on_uncorrected_low_confidence(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _rev(con, "REV-001", "Mastered", ["calculus"], confidence=0.4)
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001")

        assert "uncorrected and low-confidence" in result


class TestDepthParam:
    def test_invalid_depth_defaults_to_standard(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _cap(con, "RC-001", "q", tags=[_tag("#", "calculus", "topic"), _tag("?", "x", "question")])
        _rev(con, "REV-001", "Mastered", ["calculus"])
        con.close()
        srv._DB_PATH = db_path

        result = srv.audit_knowledge_status(rev_template_id="REV-001", depth="bogus")

        assert "Depth: standard" in result


class TestNoDbWrites:
    def test_no_writes(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _rev(con, "REV-001", "Mastered", ["calculus"])
        before = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        con.close()
        srv._DB_PATH = db_path

        srv.audit_knowledge_status(rev_template_id="REV-001")

        con = get_connection(db_path)
        after = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        con.close()
        assert after == before


# ── commit_assessment ───────────────────────────────────────────────────────

def _payload(**overrides):
    data = {
        "rev_template_id": "REV-001",
        "date": "2026-08-03",
        "assessment": "The Mastered claim on #calculus still holds — the open questions "
                      "turned out to be exploratory tangents, not gaps in the core material.",
        "reaffirmed": ["calculus"],
        "revised": [],
        "tags": ["#calculus"],
    }
    data.update(overrides)
    return json.dumps(data)


class TestCommitValidation:
    def test_invalid_json(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        assert "Invalid JSON" in srv.commit_assessment("not valid json {{")

    def test_missing_rev_template_id(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_assessment(json.dumps({"assessment": "text"}))
        assert "rev_template_id" in result

    def test_missing_assessment_text(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_assessment(json.dumps({"rev_template_id": "REV-001"}))
        assert "assessment" in result.lower()

    def test_no_rev_page_declines_and_writes_nothing(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_assessment(_payload(rev_template_id="REV-999"))

        assert "No Review page found for REV-999" in result
        with get_connection(db_path) as con:
            n = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        assert n == 0


class TestCommit:
    def _seed(self, con):
        return _rev(con, "REV-001", "Mastered", ["calculus"])

    def test_basic_commit(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        result = srv.commit_assessment(_payload())

        assert "AIEX-001" in result
        assert "Assesses : REV-001" in result

    def test_does_not_mutate_rev_status(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        rev_id = self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_assessment(_payload(revised=["calculus should probably be Solid, not Mastered"]))

        with get_connection(db_path) as con:
            row = con.execute("SELECT content_json FROM captures WHERE id=?", (rev_id,)).fetchone()
        assert json.loads(row["content_json"])["knowledge_status"] == "Mastered"

    def test_source_is_ai_extract(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_assessment(_payload())

        with get_connection(db_path) as con:
            row = con.execute("SELECT source FROM captures WHERE template_id='AIEX-001'").fetchone()
        assert row["source"] == "ai_extract"

    def test_never_creates_a_rev_template_id(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_assessment(_payload())

        with get_connection(db_path) as con:
            rows = con.execute("SELECT type, template_id FROM captures").fetchall()
        types = {r["template_id"]: r["type"] for r in rows}
        assert types["AIEX-001"] == "AIEX"
        assert "REV-002" not in types


class TestAssessesEdge:
    def test_edge_created_with_correct_shape(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        rev_id = _rev(con, "REV-001", "Mastered", ["calculus"])
        con.close()
        srv._DB_PATH = db_path

        srv.commit_assessment(_payload())

        with get_connection(db_path) as con:
            aiex_id = con.execute("SELECT id FROM captures WHERE template_id='AIEX-001'").fetchone()["id"]
            edge = con.execute(
                "SELECT * FROM connections WHERE type='asserted' AND relation='assesses'"
            ).fetchone()
        assert edge is not None
        assert edge["source_id"] == aiex_id
        assert edge["target_id"] == rev_id
        assert edge["asserted_by"] == "user"

    def test_survives_rebuild_connections(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        _rev(con, "REV-001", "Mastered", ["calculus"])
        con.close()
        srv._DB_PATH = db_path

        srv.commit_assessment(_payload())

        with get_connection(db_path) as con:
            rebuild_connections(con)
            edge = con.execute(
                "SELECT * FROM connections WHERE type='asserted' AND relation='assesses'"
            ).fetchone()
        assert edge is not None, "assesses edge was wiped by rebuild_connections"


class TestAssertConnectionAcceptsAssesses:
    def test_manual_assesses_assertion(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        a = _cap(con, "AIEX-001", "an assessment entry")
        b = _cap(con, "REV-001", "a review page")
        con.close()
        srv._DB_PATH = db_path

        result = srv.assert_connection(source_id=a, target_id=b, relation="assesses")

        assert "assesses" in result
        with get_connection(db_path) as con:
            edge = con.execute("SELECT * FROM connections WHERE relation='assesses'").fetchone()
        assert edge["source_id"] == a and edge["target_id"] == b
