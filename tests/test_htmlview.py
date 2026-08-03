"""
Tests for the self-contained HTML view (§2.4a modes 1–2).
"""

import json

from ksj_mcp.database import insert_capture, insert_tags, link_capture_entity
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


class TestRenderHtml:
    def test_contains_data(self, db):
        _cap(db, "RC-007", raw="unique-marker-text")
        html = render_html(collect_view_data(db))
        assert "RC-007" in html
        assert "unique-marker-text" in html
        assert html.startswith("<!DOCTYPE html>")

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
