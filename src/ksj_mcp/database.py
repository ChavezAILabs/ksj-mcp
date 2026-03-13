"""
SQLite + FTS5 database layer for KSJ MCP server.

Tables:
  captures    — one row per journal page photo processed
  tags        — normalized tag rows linked to a capture
  connections — detected relationships between captures
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Runtime data directory — respects KSJ_DATA_DIR env var, falls back to ~/.ksj-mcp/
_DEFAULT_DB = (
    Path(os.environ["KSJ_DATA_DIR"]) / "captures.db"
    if "KSJ_DATA_DIR" in os.environ
    else Path.home() / ".ksj-mcp" / "captures.db"
)


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or _DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db(db_path: Path | None = None) -> None:
    with get_connection(db_path) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS captures (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                type        TEXT NOT NULL CHECK(type IN ('RC','SYN','REV','DC','AIEX')),
                template_id TEXT NOT NULL,          -- e.g. RC-001
                content_json TEXT NOT NULL,          -- parsed fields as JSON
                raw_ocr     TEXT NOT NULL,
                summary     TEXT NOT NULL DEFAULT '',
                confidence  REAL NOT NULL DEFAULT 0.0,
                image_path  TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tags (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id  INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                prefix      TEXT NOT NULL,           -- # @ ! ? $ or ->
                value       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS connections (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id   INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                target_id   INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                type        TEXT NOT NULL,           -- tag_overlap | reference
                strength    REAL NOT NULL DEFAULT 1.0,
                method      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tags_capture ON tags(capture_id);
            CREATE INDEX IF NOT EXISTS idx_tags_value   ON tags(prefix, value);
            CREATE INDEX IF NOT EXISTS idx_conn_source  ON connections(source_id);
            CREATE INDEX IF NOT EXISTS idx_conn_target  ON connections(target_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS captures_fts
            USING fts5(
                raw_ocr,
                summary,
                content='captures',
                content_rowid='id'
            );

            -- Keep FTS in sync via triggers
            CREATE TRIGGER IF NOT EXISTS captures_fts_insert
            AFTER INSERT ON captures BEGIN
                INSERT INTO captures_fts(rowid, raw_ocr, summary)
                VALUES (new.id, new.raw_ocr, new.summary);
            END;

            CREATE TRIGGER IF NOT EXISTS captures_fts_delete
            AFTER DELETE ON captures BEGIN
                INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                VALUES ('delete', old.id, old.raw_ocr, old.summary);
            END;

            CREATE TRIGGER IF NOT EXISTS captures_fts_update
            AFTER UPDATE ON captures BEGIN
                INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                VALUES ('delete', old.id, old.raw_ocr, old.summary);
                INSERT INTO captures_fts(rowid, raw_ocr, summary)
                VALUES (new.id, new.raw_ocr, new.summary);
            END;
        """)


# ── CRUD ───────────────────────────────────────────────────────────────────────

def insert_capture(
    con: sqlite3.Connection,
    type_: str,
    template_id: str,
    content: dict[str, Any],
    raw_ocr: str,
    summary: str,
    confidence: float,
    image_path: str = "",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = con.execute(
        """INSERT INTO captures
               (type, template_id, content_json, raw_ocr, summary, confidence, image_path, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (type_, template_id, json.dumps(content), raw_ocr, summary, confidence, image_path, now),
    )
    return cur.lastrowid


def insert_tags(con: sqlite3.Connection, capture_id: int, tags: list[dict]) -> None:
    con.executemany(
        "INSERT INTO tags (capture_id, prefix, value) VALUES (?, ?, ?)",
        [(capture_id, t["prefix"], t["value"]) for t in tags],
    )


def insert_connection(
    con: sqlite3.Connection,
    source_id: int,
    target_id: int,
    type_: str,
    strength: float,
    method: str,
) -> int:
    # Avoid duplicate connections in either direction
    existing = con.execute(
        """SELECT id FROM connections
           WHERE (source_id=? AND target_id=?) OR (source_id=? AND target_id=?)""",
        (source_id, target_id, target_id, source_id),
    ).fetchone()
    if existing:
        return existing["id"]
    cur = con.execute(
        """INSERT INTO connections (source_id, target_id, type, strength, method)
           VALUES (?, ?, ?, ?, ?)""",
        (source_id, target_id, type_, strength, method),
    )
    return cur.lastrowid


def get_capture(con: sqlite3.Connection, capture_id: int) -> dict | None:
    row = con.execute(
        "SELECT * FROM captures WHERE id=?", (capture_id,)
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["content"] = json.loads(result.pop("content_json"))
    result["tags"] = [
        dict(t) for t in
        con.execute("SELECT prefix, value FROM tags WHERE capture_id=?", (capture_id,)).fetchall()
    ]
    return result


def list_captures(
    con: sqlite3.Connection,
    type_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 100,
) -> list[dict]:
    clauses, params = [], []
    if type_filter:
        clauses.append("type=?")
        params.append(type_filter.upper())
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = con.execute(
        f"SELECT id, type, template_id, summary, confidence, created_at FROM captures {where} "
        f"ORDER BY created_at DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    return [dict(r) for r in rows]


def get_connections(con: sqlite3.Connection, capture_id: int) -> list[dict]:
    rows = con.execute(
        """SELECT c.id, c.source_id, c.target_id, c.type, c.strength, c.method,
                  cap.template_id AS connected_template,
                  cap.summary     AS connected_summary
           FROM connections c
           JOIN captures cap ON cap.id = CASE
               WHEN c.source_id=? THEN c.target_id
               ELSE c.source_id
           END
           WHERE c.source_id=? OR c.target_id=?
           ORDER BY c.strength DESC""",
        (capture_id, capture_id, capture_id),
    ).fetchall()
    return [dict(r) for r in rows]


def get_rc_tag_clusters(con: sqlite3.Connection, min_size: int = 3) -> list[dict]:
    """
    Group RC captures by shared # tags and return clusters meeting *min_size*.

    For each cluster also checks whether a SYN capture already carries that tag,
    so the caller can distinguish "ready to synthesize" from "already synthesizing".

    Returns list of dicts sorted by cluster size (largest first):
      {
        "tag":          str,          # e.g. "machine-learning"
        "rc_count":     int,
        "rc_ids":       list[int],
        "rc_templates": list[str],    # e.g. ["RC-001", "RC-007", ...]
        "syn_exists":   bool,         # True if a SYN entry already covers this tag
        "syn_templates":list[str],    # SYN template IDs that already have this tag
      }
    """
    # RC captures grouped by # tag
    rows = con.execute(
        """SELECT t.value, c.id AS capture_id, c.template_id
           FROM tags t
           JOIN captures c ON c.id = t.capture_id
           WHERE t.prefix = '#' AND c.type = 'RC'
           ORDER BY t.value""",
    ).fetchall()

    # Build clusters
    clusters: dict[str, dict] = {}
    for row in rows:
        tag = row["value"]
        entry = clusters.setdefault(tag, {"rc_ids": [], "rc_templates": []})
        entry["rc_ids"].append(row["capture_id"])
        entry["rc_templates"].append(row["template_id"])

    # Check which tags already have SYN coverage
    syn_rows = con.execute(
        """SELECT t.value, c.template_id
           FROM tags t
           JOIN captures c ON c.id = t.capture_id
           WHERE t.prefix = '#' AND c.type = 'SYN'""",
    ).fetchall()
    syn_by_tag: dict[str, list[str]] = {}
    for row in syn_rows:
        syn_by_tag.setdefault(row["value"], []).append(row["template_id"])

    result = []
    for tag, data in clusters.items():
        if len(data["rc_ids"]) < min_size:
            continue
        syn_templates = syn_by_tag.get(tag, [])
        result.append({
            "tag":           tag,
            "rc_count":      len(data["rc_ids"]),
            "rc_ids":        data["rc_ids"],
            "rc_templates":  data["rc_templates"],
            "syn_exists":    bool(syn_templates),
            "syn_templates": syn_templates,
        })

    result.sort(key=lambda x: -x["rc_count"])
    return result


def get_question_captures(con: sqlite3.Connection) -> list[dict]:
    """
    Return all captures that have at least one '?' tag, along with any
    connected '$' insight captures for Anki back-card generation.
    """
    question_caps = con.execute(
        """SELECT DISTINCT c.id, c.type, c.template_id, c.summary, c.created_at
           FROM captures c
           JOIN tags t ON t.capture_id = c.id
           WHERE t.prefix = '?'
           ORDER BY c.created_at""",
    ).fetchall()

    results = []
    for cap in question_caps:
        cid = cap["id"]

        # All ? tags on this capture
        questions = [
            row["value"]
            for row in con.execute(
                "SELECT value FROM tags WHERE capture_id=? AND prefix='?'", (cid,)
            ).fetchall()
        ]

        # # topic tags
        topics = [
            row["value"]
            for row in con.execute(
                "SELECT value FROM tags WHERE capture_id=? AND prefix='#'", (cid,)
            ).fetchall()
        ]

        # Connected captures that carry $ insight tags
        connected = con.execute(
            """SELECT DISTINCT cap2.id, cap2.summary, cap2.template_id
               FROM connections conn
               JOIN captures cap2 ON cap2.id = CASE
                   WHEN conn.source_id=? THEN conn.target_id
                   ELSE conn.source_id
               END
               JOIN tags t ON t.capture_id = cap2.id AND t.prefix = '$'
               WHERE conn.source_id=? OR conn.target_id=?""",
            (cid, cid, cid),
        ).fetchall()

        results.append({
            "id":          cid,
            "template_id": cap["template_id"],
            "summary":     cap["summary"],
            "created_at":  cap["created_at"],
            "questions":   questions,
            "topics":      topics,
            "insights":    [dict(r) for r in connected],
        })

    return results


def check_duplicate(con: sqlite3.Connection, template_id: str) -> dict | None:
    """
    Return the existing capture dict if *template_id* is already in the DB,
    otherwise None.
    """
    row = con.execute(
        "SELECT id, template_id, summary, created_at FROM captures WHERE template_id=? COLLATE NOCASE",
        (template_id,),
    ).fetchone()
    return dict(row) if row else None


def get_journal_kpis(con: sqlite3.Connection) -> dict:
    """
    Compute KPIs for the journal_health tool.

    Returns a dict with:
      total, by_type, capture_velocity (captures/week last 4 weeks),
      insight_velocity ($/week last 4 weeks), days_since_last_rev,
      unanswered_questions (? tags with no connected $ capture),
      unanswered_age_days (age of oldest unanswered question),
      synthesis_ratio (RC captures per SYN entry),
      template_balance (which types have zero entries),
    """
    now = datetime.now(timezone.utc)
    four_weeks_ago = (now - timedelta(weeks=4)).isoformat()
    one_week_ago   = (now - timedelta(weeks=1)).isoformat()

    # Totals by type
    type_counts = {
        row["type"]: row["cnt"]
        for row in con.execute(
            "SELECT type, COUNT(*) AS cnt FROM captures GROUP BY type"
        ).fetchall()
    }
    total = sum(type_counts.values())

    # Capture velocity: per week over last 4 weeks
    recent = con.execute(
        "SELECT COUNT(*) AS cnt FROM captures WHERE created_at >= ?",
        (four_weeks_ago,),
    ).fetchone()["cnt"]
    capture_velocity = round(recent / 4, 1)

    # Insight velocity: $ tags per week over last 4 weeks
    insights_recent = con.execute(
        """SELECT COUNT(*) AS cnt FROM tags t
           JOIN captures c ON c.id = t.capture_id
           WHERE t.prefix='$' AND c.created_at >= ?""",
        (four_weeks_ago,),
    ).fetchone()["cnt"]
    insight_velocity = round(insights_recent / 4, 1)

    # Days since last REV
    last_rev = con.execute(
        "SELECT MAX(created_at) AS ts FROM captures WHERE type='REV'"
    ).fetchone()["ts"]
    if last_rev:
        rev_dt = datetime.fromisoformat(last_rev)
        days_since_rev = (now - rev_dt).days
    else:
        days_since_rev = None

    # Unanswered questions: ? captures with no connected $ insight
    question_caps = con.execute(
        """SELECT DISTINCT c.id, c.created_at
           FROM captures c
           JOIN tags t ON t.capture_id = c.id AND t.prefix = '?'""",
    ).fetchall()

    unanswered = []
    for cap in question_caps:
        cid = cap["id"]
        has_insight = con.execute(
            """SELECT 1 FROM connections conn
               JOIN captures cap2 ON cap2.id = CASE
                   WHEN conn.source_id=? THEN conn.target_id ELSE conn.source_id END
               JOIN tags t ON t.capture_id = cap2.id AND t.prefix = '$'
               WHERE conn.source_id=? OR conn.target_id=?
               LIMIT 1""",
            (cid, cid, cid),
        ).fetchone()
        if not has_insight:
            unanswered.append(cap["created_at"])

    oldest_unanswered_days = None
    if unanswered:
        oldest = min(unanswered)
        oldest_dt = datetime.fromisoformat(oldest)
        oldest_unanswered_days = (now - oldest_dt).days

    # Synthesis ratio: RC per SYN (target ~4:1 per journal design)
    rc_count  = type_counts.get("RC",  0)
    syn_count = type_counts.get("SYN", 0)
    synthesis_ratio = round(rc_count / syn_count, 1) if syn_count else None

    # Template balance: which types have zero captures
    unused = [t for t in ("RC", "SYN", "REV", "DC") if type_counts.get(t, 0) == 0]

    return {
        "total":                   total,
        "by_type":                 type_counts,
        "capture_velocity":        capture_velocity,   # captures/week
        "insight_velocity":        insight_velocity,   # insights/week
        "days_since_last_rev":     days_since_rev,
        "unanswered_questions":    len(unanswered),
        "oldest_unanswered_days":  oldest_unanswered_days,
        "synthesis_ratio":         synthesis_ratio,    # RC per SYN
        "unused_templates":        unused,
    }


def get_captures_by_tag(
    con: sqlite3.Connection,
    tag_value: str,
    prefix: str = "",
    limit: int = 200,
) -> list[dict]:
    """
    Return all captures that carry a tag matching *tag_value* (case-insensitive).
    Optionally filter by *prefix* (e.g. '#', '@', '?', '$', '!', '->').
    Results sorted by created_at descending.
    """
    clauses = ["LOWER(t.value) = LOWER(?)"]
    params: list[Any] = [tag_value]

    if prefix:
        clauses.append("t.prefix = ?")
        params.append(prefix)

    where = " AND ".join(clauses)
    rows = con.execute(
        f"""SELECT DISTINCT c.id, c.type, c.template_id, c.summary,
                   c.confidence, c.created_at
            FROM captures c
            JOIN tags t ON t.capture_id = c.id
            WHERE {where}
            ORDER BY c.created_at DESC
            LIMIT ?""",
        params + [limit],
    ).fetchall()

    results = []
    for row in rows:
        r = dict(row)
        r["tags"] = [
            dict(t) for t in con.execute(
                "SELECT prefix, value FROM tags WHERE capture_id=?", (r["id"],)
            ).fetchall()
        ]
        results.append(r)
    return results


def get_syn_breakthroughs(con: sqlite3.Connection) -> list[dict]:
    """
    Return all SYN captures in chronological order, enriched with their
    $ insight tags and the breakthrough field from content_json.
    """
    rows = con.execute(
        """SELECT id, template_id, content_json, summary, confidence, created_at
           FROM captures WHERE type='SYN'
           ORDER BY created_at ASC""",
    ).fetchall()

    results = []
    for row in rows:
        content = json.loads(row["content_json"])
        insights = [
            r["value"] for r in con.execute(
                "SELECT value FROM tags WHERE capture_id=? AND prefix='$'", (row["id"],)
            ).fetchall()
        ]
        topics = [
            r["value"] for r in con.execute(
                "SELECT value FROM tags WHERE capture_id=? AND prefix='#'", (row["id"],)
            ).fetchall()
        ]
        results.append({
            "id":           row["id"],
            "template_id":  row["template_id"],
            "breakthrough": content.get("breakthrough", ""),
            "patterns":     content.get("patterns", ""),
            "summary":      row["summary"],
            "insights":     insights,
            "topics":       topics,
            "created_at":   row["created_at"],
        })
    return results


def get_dc_pattern_data(con: sqlite3.Connection) -> list[dict]:
    """
    Return all DC captures with parsed symbols, emotions, and tags for
    dream pattern aggregation.
    """
    rows = con.execute(
        """SELECT id, template_id, content_json, summary, created_at
           FROM captures WHERE type='DC'
           ORDER BY created_at ASC""",
    ).fetchall()

    results = []
    for row in rows:
        content = json.loads(row["content_json"])
        tags = [
            dict(t) for t in con.execute(
                "SELECT prefix, value FROM tags WHERE capture_id=?", (row["id"],)
            ).fetchall()
        ]
        results.append({
            "id":          row["id"],
            "template_id": row["template_id"],
            "narrative":   content.get("dream_narrative", ""),
            "symbols":     content.get("symbols", ""),
            "emotions":    content.get("emotions", ""),
            "summary":     row["summary"],
            "tags":        tags,
            "created_at":  row["created_at"],
        })
    return results


def get_rev_progress(
    con: sqlite3.Connection,
    topic_filter: str = "",
) -> list[dict]:
    """
    Return all REV captures in chronological order with their knowledge_status
    field and topic tags, for progress tracking.
    Optionally filter by a topic tag value.
    """
    if topic_filter:
        rows = con.execute(
            """SELECT DISTINCT c.id FROM captures c
               JOIN tags t ON t.capture_id = c.id
               WHERE c.type='REV' AND t.prefix='#'
                 AND LOWER(t.value) = LOWER(?)
               ORDER BY c.created_at ASC""",
            (topic_filter,),
        ).fetchall()
        ids = [r["id"] for r in rows]
    else:
        ids = [
            r["id"] for r in con.execute(
                "SELECT id FROM captures WHERE type='REV' ORDER BY created_at ASC"
            ).fetchall()
        ]

    results = []
    for cid in ids:
        row = con.execute(
            "SELECT id, template_id, content_json, summary, created_at FROM captures WHERE id=?",
            (cid,),
        ).fetchone()
        if not row:
            continue
        content = json.loads(row["content_json"])
        topics = [
            r["value"] for r in con.execute(
                "SELECT value FROM tags WHERE capture_id=? AND prefix='#'", (cid,)
            ).fetchall()
        ]
        results.append({
            "id":               cid,
            "template_id":      row["template_id"],
            "knowledge_status": content.get("knowledge_status", ""),
            "process_notes":    content.get("process_notes", ""),
            "observations":     content.get("observations", ""),
            "topics":           topics,
            "summary":          row["summary"],
            "created_at":       row["created_at"],
        })
    return results


# ── Search ─────────────────────────────────────────────────────────────────────

def search_fts(
    con: sqlite3.Connection,
    query: str,
    tag_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Full-text search with optional tag and date filters."""
    # FTS query — escape special chars for safety
    safe_query = query.replace('"', '""')

    params: list[Any] = [safe_query]
    extra_clauses = []

    if tag_filter:
        extra_clauses.append(
            "c.id IN (SELECT capture_id FROM tags WHERE value LIKE ?)"
        )
        params.append(f"%{tag_filter}%")
    if date_from:
        extra_clauses.append("c.created_at >= ?")
        params.append(date_from)
    if date_to:
        extra_clauses.append("c.created_at <= ?")
        params.append(date_to)

    extra_where = ("AND " + " AND ".join(extra_clauses)) if extra_clauses else ""
    params.append(limit)

    rows = con.execute(
        f"""SELECT c.id, c.type, c.template_id, c.summary, c.confidence, c.created_at,
                   rank
            FROM captures_fts
            JOIN captures c ON c.id = captures_fts.rowid
            WHERE captures_fts MATCH ? {extra_where}
            ORDER BY rank
            LIMIT ?""",
        params,
    ).fetchall()

    results = []
    for row in rows:
        r = dict(row)
        r["tags"] = [
            dict(t) for t in
            con.execute(
                "SELECT prefix, value FROM tags WHERE capture_id=?", (r["id"],)
            ).fetchall()
        ]
        results.append(r)
    return results


def migrate_add_aiex(db_path: Path | None = None) -> None:
    """
    One-time migration: add 'AIEX' to the captures type CHECK constraint.

    Safe to call on every startup — exits immediately if already migrated
    or if the captures table doesn't exist yet (init_db hasn't run).
    """
    with get_connection(db_path) as con:
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='captures'"
        ).fetchone()
        if not row or "AIEX" in row["sql"]:
            return  # Already migrated or table doesn't exist yet

        # SQLite doesn't support ALTER COLUMN — requires table recreation.
        con.executescript("""
            PRAGMA foreign_keys=OFF;

            BEGIN;

            ALTER TABLE captures RENAME TO _captures_backup;

            DROP TRIGGER IF EXISTS captures_fts_insert;
            DROP TRIGGER IF EXISTS captures_fts_delete;
            DROP TRIGGER IF EXISTS captures_fts_update;
            DROP TABLE IF EXISTS captures_fts;

            CREATE TABLE captures (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                type         TEXT NOT NULL CHECK(type IN ('RC','SYN','REV','DC','AIEX')),
                template_id  TEXT NOT NULL,
                content_json TEXT NOT NULL,
                raw_ocr      TEXT NOT NULL,
                summary      TEXT NOT NULL DEFAULT '',
                confidence   REAL NOT NULL DEFAULT 0.0,
                image_path   TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL
            );

            INSERT INTO captures SELECT * FROM _captures_backup;
            DROP TABLE _captures_backup;

            CREATE VIRTUAL TABLE captures_fts
            USING fts5(
                raw_ocr,
                summary,
                content='captures',
                content_rowid='id'
            );

            INSERT INTO captures_fts(rowid, raw_ocr, summary)
                SELECT id, raw_ocr, summary FROM captures;

            CREATE TRIGGER captures_fts_insert
            AFTER INSERT ON captures BEGIN
                INSERT INTO captures_fts(rowid, raw_ocr, summary)
                VALUES (new.id, new.raw_ocr, new.summary);
            END;

            CREATE TRIGGER captures_fts_delete
            AFTER DELETE ON captures BEGIN
                INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                VALUES ('delete', old.id, old.raw_ocr, old.summary);
            END;

            CREATE TRIGGER captures_fts_update
            AFTER UPDATE ON captures BEGIN
                INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                VALUES ('delete', old.id, old.raw_ocr, old.summary);
                INSERT INTO captures_fts(rowid, raw_ocr, summary)
                VALUES (new.id, new.raw_ocr, new.summary);
            END;

            COMMIT;

            PRAGMA foreign_keys=ON;
        """)


def get_next_aiex_id(con: sqlite3.Connection) -> str:
    """Return the next sequential AIEX-NNN template ID."""
    row = con.execute(
        """SELECT MAX(CAST(SUBSTR(template_id, 6) AS INTEGER)) AS max_num
           FROM captures WHERE type='AIEX'"""
    ).fetchone()
    next_num = (row["max_num"] or 0) + 1
    return f"AIEX-{next_num:03d}"


def get_stats(con: sqlite3.Connection) -> dict:
    counts = {
        row["type"]: row["cnt"]
        for row in con.execute(
            "SELECT type, COUNT(*) AS cnt FROM captures GROUP BY type"
        ).fetchall()
    }
    top_tags = con.execute(
        """SELECT prefix || value AS tag, COUNT(*) AS cnt
           FROM tags GROUP BY prefix, value ORDER BY cnt DESC LIMIT 10"""
    ).fetchall()
    questions = con.execute(
        "SELECT COUNT(*) AS cnt FROM tags WHERE prefix='?'"
    ).fetchone()["cnt"]
    insights = con.execute(
        "SELECT COUNT(*) AS cnt FROM tags WHERE prefix='$'"
    ).fetchone()["cnt"]
    date_range = con.execute(
        "SELECT MIN(created_at) AS earliest, MAX(created_at) AS latest FROM captures"
    ).fetchone()
    total = con.execute("SELECT COUNT(*) AS cnt FROM captures").fetchone()["cnt"]

    return {
        "total_captures": total,
        "by_type": counts,
        "top_tags": [dict(r) for r in top_tags],
        "open_questions": questions,
        "key_insights": insights,
        "date_range": {
            "earliest": date_range["earliest"],
            "latest": date_range["latest"],
        },
    }
