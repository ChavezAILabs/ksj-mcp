"""
Shared pytest fixtures for KSJ MCP server tests.
"""

import sqlite3
from pathlib import Path

import pytest

from ksj_mcp.database import init_db, get_connection


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """
    Provide a fresh, fully-initialized in-memory-equivalent SQLite connection.
    Uses a temp file so FTS5 (which needs WAL mode) works correctly.
    """
    db_path = tmp_path / "test_captures.db"
    init_db(db_path)
    con = get_connection(db_path)
    yield con
    con.close()
