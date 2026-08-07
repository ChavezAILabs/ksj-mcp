"""
Tests for the get_version tool in ksj_mcp.server.
"""

import re

import ksj_mcp.server as server_mod


def test_get_version_reports_all_fields():
    result = server_mod.get_version()

    assert result.startswith("ksj-mcp ")
    assert "mcp      :" in result
    assert "pydantic :" in result
    assert "python   :" in result


def test_get_version_fields_look_like_versions():
    result = server_mod.get_version()
    lines = result.splitlines()

    # ksj-mcp 3.4.0
    assert re.match(r"^ksj-mcp \S+$", lines[0])

    fields = {}
    for line in lines[1:]:
        key, _, value = line.strip().partition(":")
        fields[key.strip()] = value.strip()

    for name in ("mcp", "pydantic", "python"):
        assert fields[name] != "", f"{name} field was empty"
