"""
Tests for the self-contained HTML view (§2.4a modes 1-4).
"""

import json

from ksj_mcp.connections import build_connections
from ksj_mcp.database import insert_capture, insert_tags, link_capture_entity, insert_connection
from ksj_mcp.htmlview import collect_view_data, render_html


def _cap(con, template_id="RC-001", raw="text", corrected=None, tags=None, superseded=False):
    type_ = template_id.split("-")[0] if template_id else "UNKNOWN"
    cid = insert_capture(
        con, type_, template_id,
        {"first_impressions": "note", "tags_raw": "should be dropped"},
        raw, raw[:40], 0.9,
    )
    if corrected:
        con.execute("UPDATE captures SET corrected_ocr=? WHERE id=?", (corrected, cid))
    if superseded:
        con.execute("UPDATE captures SET valid_until='2026-08-01T00:00:00' WHERE id=?", (cid,))
    if tags:
        insert_tags(con, cid, tags)
    con.commit()
    return cid


class TestCollectViewData:
    def test_shape_and_fields(self, db):
        _cap(db, "RC-001", raw="hello world",
             tags=[{"prefix": "#", "value": "ml", "display": "ML", "role": "topic"}])
        data = collect_view_data(db)
        assert len(data["captures"]) == 1
        c = data["captures"][0]
        assert c["template_id"] == "RC-001"
        assert c["text"] == "hello world"
        assert c["tags"][0]["role"] == "topic"
        assert "tags_raw" not in c["fields"]

    def test_corrected_text_preferred(self, db):
        _cap(db, "RC-001", raw="garbled", corrected="clean text")
        c = collect_view_data(db)["captures"][0]
        assert c["text"] == "clean text"
        assert c["corrected"] is True

    def test_superseded_flag(self, db):
        _cap(db, "RC-001", superseded=True)
        assert collect_view_data(db)["captures"][0]["superseded"] is True

    def test_entities_with_capture_ids(self, db):
        cid = _cap(db, "RC-001")
        link_capture_entity(db, cid, "Veronica", kind="person")
        db.commit()
        ents = collect_view_data(db)["entities"]
        assert ents[0]["name"] == "Veronica"
        assert ents[0]["capture_ids"] == [cid]

    def test_edges_shape(self, db):
        """One flat edge list — modes 3/4 both derive per-capture or graph
        views from this client-side rather than getting duplicated data."""
        a = _cap(db, "RC-001", raw="see @RC-002")
        b = _cap(db, "RC-002", raw="referenced page")
        build_connections(db, a)
        edges = collect_view_data(db)["edges"]
        assert len(edges) == 1
        e = edges[0]
        assert e["source"] == a
        assert e["target"] == b
        assert e["type"] == "reference"
        assert e["strength"] == 1.0

    def test_asserted_edge_carries_relation_and_note(self, db):
        a = _cap(db, "RC-001")
        b = _cap(db, "RC-002")
        insert_connection(db, a, b, "asserted", 1.0, "asserted",
                          relation="supports", note="because", asserted_by="user")
        db.commit()
        edges = collect_view_data(db)["edges"]
        assert edges[0]["relation"] == "supports"
        assert edges[0]["note"] == "because"

    def test_no_edges_when_none_exist(self, db):
        _cap(db, "RC-001")
        assert collect_view_data(db)["edges"] == []


class TestRenderHtml:
    def test_contains_data(self, db):
        _cap(db, "RC-007", raw="unique-marker-text")
        html = render_html(collect_view_data(db))
        assert "RC-007" in html
        assert "unique-marker-text" in html
        assert html.startswith("<!DOCTYPE html>")

    def test_graph_caption_present(self, db):
        _cap(db, "RC-001")
        html = render_html(collect_view_data(db))
        assert 'class="graph-caption"' in html
        assert "How to read this" in html

    def test_script_injection_escaped(self, db):
        _cap(db, "RC-001", raw="evil </script><script>alert(1)</script> content")
        html = render_html(collect_view_data(db))
        # exactly the page's own two script blocks — page text can never
        # close the JSON block early
        assert html.count("</script>") == 2

    def test_payload_is_valid_json(self, db):
        _cap(db, "RC-001", raw='text with "quotes" and </closers>')
        html = render_html(collect_view_data(db))
        start = html.index('type="application/json">') + len('type="application/json">')
        end = html.index("</script>", start)
        payload = html[start:end].replace("<\\/", "</")
        data = json.loads(payload)
        assert data["captures"][0]["template_id"] == "RC-001"

    def test_graph_tab_and_containers_present(self, db):
        _cap(db, "RC-001")
        html = render_html(collect_view_data(db))
        assert 'id="tab-graph"' in html
        assert 'id="graph-svg"' in html
        assert 'id="view-graph"' in html

    def test_connections_section_present_in_card_markup(self, db):
        _cap(db, "RC-001")
        html = render_html(collect_view_data(db))
        # the connectionsSectionHtml() call site, not per-capture output
        # (that's rendered client-side) — just confirm the hook is wired in
        assert "connectionsSectionHtml" in html
        assert 'class="connections"' in html
