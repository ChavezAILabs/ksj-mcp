"""
Tests for commit_distillation (ksj-mcp 3.2, SYN companion — the DB-writing
half of surface_connections).

Covers §3.4/§5.1 of
ksj2_documents/SYN_companion_decision_optionB_2026-08-03.md:
  - never creates a SYN template ID
  - stores with source='ai_extract' (excluded from journal_health KPIs)
  - links to the SYN page with an asserted relation='distills' edge
  - asserted_by='user' on that edge specifically, so rebuild_connections
    (which deletes every edge WHERE asserted_by != 'user') never silently
    drops it
  - 'distills' is also usable via the general-purpose assert_connection()
"""
import json

import ksj_mcp.server as srv
from ksj_mcp.connections import rebuild_connections
from ksj_mcp.database import get_connection, init_db, insert_capture, insert_tags


def _cap(con, template_id, raw, tags=None):
    type_ = template_id.split("-")[0]
    cid = insert_capture(con, type_, template_id, {"raw": raw}, raw, raw[:60], 1.0)
    if tags:
        insert_tags(con, cid, tags)
    con.commit()
    return cid


def _tag(prefix, value, role=None):
    return {"prefix": prefix, "value": value, "display": value, "role": role}


def _payload(**overrides):
    data = {
        "syn_template_id": "SYN-001",
        "date": "2026-08-03",
        "distillation": "The scan and the page converge on the same resilience pattern, "
                         "but the page also names a specific outage the scan couldn't see.",
        "confirmed": ["RC-001 <-> RC-002: shared resilience pattern"],
        "retired": [],
        "deferred": ["RC-003's retry-storm angle needs another pass"],
        "missed": ["RC-001 <-> RC-003: never explicitly cross-referenced"],
        "beyond_scan": ["the March outage story — not derivable from tags"],
        "tags": ["#resilience"],
    }
    data.update(overrides)
    return json.dumps(data)


class TestValidation:
    def test_invalid_json(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_distillation("not valid json {{")

        assert "Invalid JSON" in result

    def test_missing_syn_template_id(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_distillation(json.dumps({"distillation": "text"}))

        assert "syn_template_id" in result

    def test_missing_distillation_text(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_distillation(json.dumps({"syn_template_id": "SYN-001"}))

        assert "distillation" in result.lower()

    def test_no_syn_page_declines_and_writes_nothing(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        srv._DB_PATH = db_path

        result = srv.commit_distillation(_payload(syn_template_id="SYN-999"))

        assert "No Synthesis page found for SYN-999" in result
        with get_connection(db_path) as con:
            n = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
        assert n == 0


class TestCommit:
    def _seed(self, con):
        _cap(con, "SYN-001", "synthesis of resilience patterns", [_tag("#", "resilience", "topic")])

    def test_basic_commit(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        result = srv.commit_distillation(_payload())

        assert "AIEX-001" in result
        assert "Distills : SYN-001" in result

    def test_never_creates_a_syn_template_id(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_distillation(_payload())

        with get_connection(db_path) as con:
            rows = con.execute("SELECT type, template_id FROM captures").fetchall()
        types = {r["template_id"]: r["type"] for r in rows}
        assert types["AIEX-001"] == "AIEX"
        assert "SYN-002" not in types  # no second SYN page was minted

    def test_source_is_ai_extract(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_distillation(_payload())

        with get_connection(db_path) as con:
            row = con.execute(
                "SELECT source FROM captures WHERE template_id='AIEX-001'"
            ).fetchone()
        assert row["source"] == "ai_extract"

    def test_content_fields_stored(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_distillation(_payload())

        with get_connection(db_path) as con:
            row = con.execute(
                "SELECT content_json FROM captures WHERE template_id='AIEX-001'"
            ).fetchone()
        content = json.loads(row["content_json"])
        assert content["syn_template_id"] == "SYN-001"
        assert content["confirmed"] == ["RC-001 <-> RC-002: shared resilience pattern"]
        assert content["missed"] == ["RC-001 <-> RC-003: never explicitly cross-referenced"]
        assert "March outage" in content["beyond_scan"][0]

    def test_tags_from_list_get_role(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_distillation(_payload())

        with get_connection(db_path) as con:
            cid = con.execute(
                "SELECT id FROM captures WHERE template_id='AIEX-001'"
            ).fetchone()["id"]
            tags = con.execute(
                "SELECT prefix, value, role FROM tags WHERE capture_id=?", (cid,)
            ).fetchall()
        assert any(t["prefix"] == "#" and t["value"] == "resilience" and t["role"] for t in tags)

    def test_default_date_when_omitted(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        result = srv.commit_distillation(_payload(date=None))

        import re
        assert re.search(r"Date\s*:\s*\d{4}-\d{2}-\d{2}", result)


class TestDistillsEdge:
    def _seed(self, con):
        return _cap(con, "SYN-001", "synthesis", [_tag("#", "resilience", "topic")])

    def test_edge_created_with_correct_shape(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        syn_id = self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_distillation(_payload())

        with get_connection(db_path) as con:
            aiex_id = con.execute(
                "SELECT id FROM captures WHERE template_id='AIEX-001'"
            ).fetchone()["id"]
            edge = con.execute(
                "SELECT * FROM connections WHERE type='asserted' AND relation='distills'"
            ).fetchone()
        assert edge is not None
        assert edge["source_id"] == aiex_id
        assert edge["target_id"] == syn_id
        assert edge["asserted_by"] == "user"

    def test_survives_rebuild_connections(self, tmp_path):
        """
        The whole point of asserted_by='user' here: rebuild_connections()
        deletes every edge WHERE asserted_by != 'user', and nothing else
        re-derives a distills edge from tags/text. If this were stored as
        'derived' (or anything else), it would silently vanish on the next
        rebuild.
        """
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_distillation(_payload())

        with get_connection(db_path) as con:
            rebuild_connections(con)
            edge = con.execute(
                "SELECT * FROM connections WHERE type='asserted' AND relation='distills'"
            ).fetchone()
        assert edge is not None, "distills edge was wiped by rebuild_connections"

    def test_multiple_distillations_of_same_syn_page_both_kept(self, tmp_path):
        """Re-running surface_connections later and committing again should
        add history, not collide with or overwrite the first distillation."""
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        self._seed(con)
        con.close()
        srv._DB_PATH = db_path

        srv.commit_distillation(_payload(distillation="First pass distillation."))
        srv.commit_distillation(_payload(distillation="Second pass distillation, cluster grew."))

        with get_connection(db_path) as con:
            aiex_rows = con.execute("SELECT template_id FROM captures WHERE type='AIEX'").fetchall()
            edges = con.execute(
                "SELECT * FROM connections WHERE type='asserted' AND relation='distills'"
            ).fetchall()
        assert {r["template_id"] for r in aiex_rows} == {"AIEX-001", "AIEX-002"}
        assert len(edges) == 2


class TestAssertConnectionAcceptsDistills:
    def test_manual_distills_assertion(self, tmp_path):
        db_path = tmp_path / "t.db"
        init_db(db_path)
        con = get_connection(db_path)
        a = _cap(con, "AIEX-001", "a distillation entry")
        b = _cap(con, "SYN-001", "a synthesis page")
        con.close()
        srv._DB_PATH = db_path

        result = srv.assert_connection(source_id=a, target_id=b, relation="distills")

        assert "distills" in result
        with get_connection(db_path) as con:
            edge = con.execute(
                "SELECT * FROM connections WHERE relation='distills'"
            ).fetchone()
        assert edge["source_id"] == a and edge["target_id"] == b
