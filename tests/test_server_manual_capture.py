"""
Tests for the manual_capture tool in ksj_mcp.server.
"""

import pytest
from pathlib import Path

import ksj_mcp.server as server_mod
from ksj_mcp.database import init_db, get_connection


RC_TEXT = """RC-001
Date: 2/27/2026
Source: Claude, CAILculator
Subject: E8 & The Canonical Six
First Impressions: E8 is active — Weyl group = symmetry group of a root system.
Key Points: Canonical Six are G2-invariants. Viazovska connection.
Action Items: Lean 4 proof of Master Theorem
Connections: Canonical Six -> E8 orbit structure
Tags: #E8 #geometry @RC-002 !high $canonical-six
"""


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch):
    """Point the server module at a fresh temp database for each test."""
    db_path = tmp_path / "test_server.db"
    init_db(db_path)
    monkeypatch.setattr(server_mod, "_DB_PATH", db_path)

    # Patch _db() to use the temp path
    def _test_db():
        return get_connection(db_path)

    monkeypatch.setattr(server_mod, "_db", _test_db)


# ── Basic success ─────────────────────────────────────────────────────────────

class TestManualCaptureBasic:
    def test_stores_capture_and_returns_summary(self):
        result = server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        assert "Stored capture #" in result
        assert "RC-001" in result

    def test_detects_template_id_from_text(self):
        result = server_mod.manual_capture(RC_TEXT)
        assert "RC-001" in result
        assert "Stored capture #" in result

    def test_tags_extracted(self):
        result = server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        assert "#E8" in result or "e8" in result.lower()

    def test_confidence_shown_as_100_percent(self):
        result = server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        assert "100%" in result

    def test_returns_string(self):
        result = server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        assert isinstance(result, str)


# ── template_id detection ─────────────────────────────────────────────────────

class TestTemplateIdDetection:
    def test_explicit_id_overrides_text(self):
        # Text says RC-001 but caller passes RC-999 — explicit wins
        result = server_mod.manual_capture(RC_TEXT, template_id="RC-999")
        assert "RC-999" in result

    def test_unknown_template_id_returns_error(self):
        result = server_mod.manual_capture(RC_TEXT, template_id="XX-001")
        assert "Could not parse" in result

    def test_no_template_id_in_text_returns_error(self):
        result = server_mod.manual_capture("No template marker here at all.")
        assert "Could not detect" in result

    def test_syn_template_detected(self):
        syn_text = "SYN-003\nBreakthrough: connections confirmed.\n$synthesis #patterns"
        result = server_mod.manual_capture(syn_text)
        assert "SYN-003" in result

    def test_dc_template_detected(self):
        dc_text = "DC-002\nNarrative: flying over a city.\n#flying @ocean *visual"
        result = server_mod.manual_capture(dc_text)
        assert "DC-002" in result


# ── Duplicate detection ───────────────────────────────────────────────────────

class TestDuplicateDetection:
    def test_duplicate_blocked_by_default(self):
        server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        result = server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        assert "already exists" in result
        assert "force=True" in result

    def test_force_overwrites_duplicate(self):
        server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        result = server_mod.manual_capture(RC_TEXT, template_id="RC-001", force=True)
        assert "Stored capture #" in result

    def test_second_capture_different_id_ok(self):
        server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        rc2 = RC_TEXT.replace("RC-001", "RC-002")
        result = server_mod.manual_capture(rc2, template_id="RC-002")
        assert "Stored capture #" in result


# ── Connection detection ──────────────────────────────────────────────────────

class TestConnections:
    def test_no_connections_on_first_capture(self):
        result = server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        assert "No connections" in result

    def test_connection_detected_on_second_capture_with_shared_tag(self):
        server_mod.manual_capture(RC_TEXT, template_id="RC-001")
        rc2 = RC_TEXT.replace("RC-001", "RC-002")
        result = server_mod.manual_capture(rc2, template_id="RC-002")
        # Both have #E8 — should detect connection
        assert "connection" in result.lower()
        assert "RC-001" in result
