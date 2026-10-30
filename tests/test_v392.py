"""
v3.9.2 fixes, found rehearsing the launch-video demo (2026-10-30):

- back-references: a page cited (@RC-005) BEFORE it was captured links to its
  citers the moment it arrives, without rebuild_connections
- arrow tags (U4/F11): a spaced arrow inside prose is not a cause/effect tag
- tag values need a letter (U6): #768, $24 stay prose
- the upload highlight prefers a citation over tag overlap (F12)
- connection lists are numbered and show capture numbers (F15)
- the printed edition mark ("v2.0 · Rev C") is read into the page's fields
"""

from pathlib import Path

import pytest

import ksj_mcp.server as server_mod
from ksj_mcp.database import init_db, get_connection
from ksj_mcp.templates import extract_schema_tags, parse_template


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "test_v392.db"
    init_db(db_path)
    monkeypatch.setattr(server_mod, "_DB_PATH", db_path)
    monkeypatch.setattr(server_mod, "_db", lambda: get_connection(db_path))
    return db_path


def _rc(tid: str, body: str, tags: str = "#lean4") -> str:
    return f"Rapid Capture {tid}\n\nFirst Impressions:\n{body}\n\nTags: {tags}\n"


def _values(text: str, prefix: str | None = None) -> set[str]:
    return {t["value"] for t in extract_schema_tags(text)
            if prefix is None or t["prefix"] == prefix}


# ── Back-references ───────────────────────────────────────────────────────────

class TestBackReferences:
    def test_cited_page_links_to_earlier_citers_on_capture(self, isolated_db):
        server_mod.manual_capture(_rc("RC-006", "Kepler notes, continues @RC-005"), template_id="RC-006")
        server_mod.manual_capture(_rc("RC-007", "Syntax, see @RC-005 too"), template_id="RC-007")
        out = server_mod.manual_capture(_rc("RC-005", "Proof in the Code"), template_id="RC-005")

        assert "cited by RC-006" in out
        assert "cited by RC-007" in out
        # stored, not just reported: find_connections sees them with no rebuild
        con = get_connection(isolated_db)
        rc5 = con.execute("SELECT id FROM captures WHERE template_id='RC-005'").fetchone()["id"]
        con.close()
        found = server_mod.find_connections(rc5)
        assert "← cited by RC-006" in found
        assert "← cited by RC-007" in found

    def test_same_edges_as_a_full_rebuild(self, isolated_db):
        server_mod.manual_capture(_rc("RC-006", "see @RC-005"), template_id="RC-006")
        server_mod.manual_capture(_rc("RC-005", "the cited page"), template_id="RC-005")

        def refs():
            con = get_connection(isolated_db)
            rows = con.execute(
                "SELECT source_id, target_id FROM connections WHERE type='reference' "
                "ORDER BY source_id, target_id").fetchall()
            con.close()
            return [tuple(r) for r in rows]

        before = refs()
        server_mod.rebuild_connections()
        assert refs() == before and len(before) == 1

    def test_no_back_link_to_a_second_copy_of_the_id(self, isolated_db):
        # References resolve to the first RC-005; a second copy (volume 2)
        # must not collect the citers too, or capture and rebuild disagree.
        server_mod.manual_capture(_rc("RC-005", "volume 1 page"), template_id="RC-005", volume=1)
        server_mod.manual_capture(_rc("RC-006", "see @RC-005"), template_id="RC-006", volume=1)
        out = server_mod.manual_capture(_rc("RC-005", "volume 2 page"), template_id="RC-005", volume=2)
        assert "cited by RC-006" not in out

    def test_partial_id_is_not_a_citation(self, isolated_db):
        server_mod.manual_capture(_rc("RC-006", "an email like x@RC-0050.example"), template_id="RC-006")
        out = server_mod.manual_capture(_rc("RC-005", "page"), template_id="RC-005")
        assert "cited by" not in out


# ── Arrow tags (U4 / F11) ─────────────────────────────────────────────────────

class TestArrowTags:
    @pytest.mark.parametrize("prose", [
        "Euclid 300 BCE -> infinite primes",
        "Real functional analysis -> spaces of f: R -> R",
        "the March 'Noether -> Witt -> Canonical Six' lineage",
        "Connections: Canonical Six -> E8 orbit structure",
    ])
    def test_spaced_arrow_in_prose_is_not_a_tag(self, prose):
        assert not _values(prose, "->")

    def test_compact_arrow_is_a_tag(self):
        assert _values("cause->effect demonstrated here", "->") == {"cause->effect"}
        assert _values("input→output transformation", "->") == {"input->output"}

    def test_spaced_arrow_on_a_tag_only_line_is_a_tag(self):
        assert _values("Tags: #lean4 Noether → Witt", "->") == {"noether->witt"}
        assert _values("Lean -> Mathlib", "->") == {"lean->mathlib"}

    def test_real_rc005_page_has_no_arrow_tags(self):
        text = ("Euclid 300 BCE -> infinite primes\n"
                "- Real functional analysis -> spaces of f: R -> R\n"
                "Tags: #Lean4 @book #Unfashionable")
        assert not _values(text, "->")
        assert {"lean4", "unfashionable"} <= _values(text, "#")


# ── Tags need a letter (U6) ───────────────────────────────────────────────────

class TestTagsNeedALetter:
    def test_numbers_are_not_tags(self):
        assert not _values("Playground #768 Accepted, priced at $24 or !1")

    def test_mixed_values_still_are(self):
        assert _values("#3D printing and #lean4", "#") == {"3d", "lean4"}


# ── Highlight and numbering (F12, F15) ────────────────────────────────────────

class TestHighlightAndNumbering:
    def test_highlight_prefers_a_citation_over_tag_overlap(self):
        shared = "#lean4 #kepler #rare-topic"
        server_mod.manual_capture(_rc("RC-001", "strong tag twin", shared), template_id="RC-001")
        server_mod.manual_capture(_rc("RC-002", "cited page", "#other"), template_id="RC-002")
        out = server_mod.manual_capture(
            _rc("RC-003", "builds on @RC-002", shared), template_id="RC-003")
        assert "Strongest connection: RC-002" in out

    def test_capture_output_lists_are_numbered_with_capture_numbers(self):
        server_mod.manual_capture(_rc("RC-001", "first"), template_id="RC-001")
        out = server_mod.manual_capture(_rc("RC-002", "builds on @RC-001"), template_id="RC-002")
        assert "    1. → cites RC-001 (#1)" in out

    def test_find_connections_is_numbered(self):
        server_mod.manual_capture(_rc("RC-001", "first"), template_id="RC-001")
        server_mod.manual_capture(_rc("RC-002", "builds on @RC-001"), template_id="RC-002")
        found = server_mod.find_connections(2)
        assert "  1. → cites RC-001 (#1)" in found


# ── Edition mark ──────────────────────────────────────────────────────────────

class TestEditionMark:
    @pytest.mark.parametrize("mark", [
        "v2.0 · Rev C", "V2.0 Rev C", "v2.0 - Rev. C", "v2.0 • rev c",
    ])
    def test_mark_is_read(self, mark):
        parsed = parse_template("RC", f"Rapid Capture RC-005\n{mark}\nFirst Impressions:\nnotes\n")
        assert parsed["fields"]["edition"] == "v2.0 · Rev C"

    def test_no_mark_no_field(self):
        parsed = parse_template("RC", "Rapid Capture RC-005\nFirst Impressions:\nnotes\n")
        assert "edition" not in parsed["fields"]

    def test_mark_is_not_the_summary_or_a_tag(self):
        parsed = parse_template("RC", "RC-005\nv2.0 · Rev C\nFirst Impressions:\nreal content\n")
        assert parsed["summary"].startswith("real content")
        assert not any("rev" in t["value"] for t in parsed["tags"])

    def test_capture_output_shows_the_edition(self):
        out = server_mod.manual_capture(
            "Rapid Capture RC-005\nv2.0 · Rev C\nFirst Impressions:\nnotes\nTags: #lean4\n",
            template_id="RC-005")
        assert "Edition  : v2.0 · Rev C" in out
