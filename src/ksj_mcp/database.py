"""
SQLite + FTS5 database layer for KSJ MCP server.

Tables:
  captures    — one row per journal page photo processed
  tags        — normalized tag rows linked to a capture
  connections — detected relationships between captures
"""

import json
import os
import shutil
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
                type        TEXT NOT NULL CHECK(type IN ('RC','SYN','REV','DC','AIEX','UNKNOWN')),
                template_id TEXT,                    -- e.g. RC-001; NULL = unidentified page
                page_suffix TEXT,                    -- stray trailing letter (tolerated, not interpreted)
                volume      INTEGER NOT NULL DEFAULT 1,
                content_json TEXT NOT NULL,          -- parsed fields as JSON
                raw_ocr     TEXT NOT NULL,
                corrected_ocr TEXT,                  -- user-corrected transcription (raw_ocr preserved)
                summary     TEXT NOT NULL DEFAULT '',
                confidence  REAL NOT NULL DEFAULT 0.0,
                image_path  TEXT NOT NULL DEFAULT '',
                source      TEXT NOT NULL DEFAULT 'journal',  -- 'journal' | 'ai_extract'
                valid_from  TEXT,                -- bi-temporal: when this claim became current
                valid_until TEXT,                -- set when superseded; the row is never deleted
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tags (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id  INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                prefix      TEXT NOT NULL,           -- # @ ! ? $ * or -> (as written)
                value       TEXT NOT NULL,           -- normalized (casefold, whitespace collapsed)
                display     TEXT,                    -- original string as written
                role        TEXT                     -- canonical semantic role from (prefix, template type)
            );

            CREATE TABLE IF NOT EXISTS connections (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id   INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                target_id   INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                type        TEXT NOT NULL,           -- tag_overlap | entity_overlap | reference | asserted
                strength    REAL NOT NULL DEFAULT 1.0,
                method      TEXT NOT NULL,
                relation    TEXT,                    -- supersedes | refutes | narrows | supports | distills | assesses | observes (asserted only)
                note        TEXT,                    -- optional human annotation
                asserted_by TEXT NOT NULL DEFAULT 'derived'  -- 'derived' | 'user'
            );

            CREATE TABLE IF NOT EXISTS entities (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,           -- display form, as written
                normalized  TEXT NOT NULL,           -- casefold + whitespace collapse
                kind        TEXT NOT NULL DEFAULT 'other',  -- person|place|work|org|symbol|other
                UNIQUE(normalized, kind)
            );

            CREATE TABLE IF NOT EXISTS capture_entities (
                capture_id  INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                entity_id   INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                source      TEXT NOT NULL DEFAULT 'extracted',  -- 'extracted' | 'asserted'
                PRIMARY KEY (capture_id, entity_id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            INSERT OR IGNORE INTO settings (key, value) VALUES ('active_volumes', '*');
            INSERT OR IGNORE INTO settings (key, value) VALUES ('current_volume', '1');

            CREATE INDEX IF NOT EXISTS idx_tags_capture ON tags(capture_id);
            CREATE INDEX IF NOT EXISTS idx_tags_value   ON tags(prefix, value);
            CREATE INDEX IF NOT EXISTS idx_conn_source  ON connections(source_id);
            CREATE INDEX IF NOT EXISTS idx_conn_target  ON connections(target_id);
            -- One page per (volume, template ID, suffix); unidentified pages
            -- (NULL template_id) are exempt — NULLs are distinct in SQLite.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_captures_vol_tid
                ON captures(volume, template_id, COALESCE(page_suffix, ''));
            -- Dedup key for edges: one row per (source, target, type) so a
            -- reference edge can coexist with a tag_overlap edge on the same
            -- pair, and re-inserts update strength instead of doubling.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_conn_unique
                ON connections(source_id, target_id, type);

            CREATE VIRTUAL TABLE IF NOT EXISTS captures_fts
            USING fts5(
                raw_ocr,
                summary,
                content='captures',
                content_rowid='id'
            );

            -- Keep FTS in sync via triggers. The index holds the corrected
            -- transcription when one exists; the FTS column keeps the name
            -- raw_ocr because external-content column names must match the
            -- content table.
            CREATE TRIGGER IF NOT EXISTS captures_fts_insert
            AFTER INSERT ON captures BEGIN
                INSERT INTO captures_fts(rowid, raw_ocr, summary)
                VALUES (new.id, COALESCE(new.corrected_ocr, new.raw_ocr), new.summary);
            END;

            CREATE TRIGGER IF NOT EXISTS captures_fts_delete
            AFTER DELETE ON captures BEGIN
                INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                VALUES ('delete', old.id, COALESCE(old.corrected_ocr, old.raw_ocr), old.summary);
            END;

            CREATE TRIGGER IF NOT EXISTS captures_fts_update
            AFTER UPDATE ON captures BEGIN
                INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                VALUES ('delete', old.id, COALESCE(old.corrected_ocr, old.raw_ocr), old.summary);
                INSERT INTO captures_fts(rowid, raw_ocr, summary)
                VALUES (new.id, COALESCE(new.corrected_ocr, new.raw_ocr), new.summary);
            END;
        """)


# ── CRUD ───────────────────────────────────────────────────────────────────────

def insert_capture(
    con: sqlite3.Connection,
    type_: str,
    template_id: str | None,
    content: dict[str, Any],
    raw_ocr: str,
    summary: str,
    confidence: float,
    image_path: str = "",
    volume: int = 1,
    source: str = "journal",
    page_suffix: str | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = con.execute(
        """INSERT INTO captures
               (type, template_id, page_suffix, volume, content_json, raw_ocr,
                summary, confidence, image_path, source, valid_from, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (type_, template_id or None, page_suffix, volume, json.dumps(content),
         raw_ocr, summary, confidence, image_path, source, now, now),
    )
    return cur.lastrowid


def insert_tags(con: sqlite3.Connection, capture_id: int, tags: list[dict]) -> None:
    con.executemany(
        "INSERT INTO tags (capture_id, prefix, value, display, role) VALUES (?, ?, ?, ?, ?)",
        [
            (capture_id, t["prefix"], t["value"],
             t.get("display", t["value"]), t.get("role"))
            for t in tags
        ],
    )
    # Entity-role tags populate the entities table (§1.9/§1.10 unification:
    # a dream symbol and a screenplay character are the same kind of object).
    for t in tags:
        if t.get("role") == "entity":
            link_capture_entity(
                con, capture_id,
                name=t.get("display", t["value"]),
                kind="other",
                source="extracted",
            )


# ── Settings ───────────────────────────────────────────────────────────────────

def get_setting(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    con.commit()


def get_current_volume(con: sqlite3.Connection) -> int:
    try:
        return int(get_setting(con, "current_volume", "1"))
    except (TypeError, ValueError):
        return 1


def get_active_volumes(con: sqlite3.Connection) -> list[int] | None:
    """Volumes enabled for reading. None means all ('*')."""
    raw = get_setting(con, "active_volumes", "*")
    if raw is None or raw.strip() == "*":
        return None
    try:
        vols = sorted({int(v) for v in raw.split(",") if v.strip()})
        return vols or None
    except ValueError:
        return None


def _volume_where(volumes: list[int] | None, alias: str = "c") -> tuple[str, list]:
    """SQL fragment (starting with AND) restricting *alias* to *volumes*."""
    if not volumes:
        return "", []
    placeholders = ",".join("?" * len(volumes))
    return f" AND {alias}.volume IN ({placeholders})", list(volumes)


# ── Entities ───────────────────────────────────────────────────────────────────

def upsert_entity(con: sqlite3.Connection, name: str, kind: str = "other") -> int:
    """Insert or fetch an entity by (normalized name, kind). Returns entity id."""
    normalized = " ".join(name.casefold().split())
    row = con.execute(
        "SELECT id FROM entities WHERE normalized=? AND kind=?", (normalized, kind)
    ).fetchone()
    if row:
        return row["id"]
    cur = con.execute(
        "INSERT INTO entities (name, normalized, kind) VALUES (?, ?, ?)",
        (name.strip(), normalized, kind),
    )
    return cur.lastrowid


def link_capture_entity(
    con: sqlite3.Connection,
    capture_id: int,
    name: str,
    kind: str = "other",
    source: str = "extracted",
) -> int:
    """Link *capture_id* to the entity *name*, creating the entity if needed."""
    entity_id = upsert_entity(con, name, kind)
    con.execute(
        "INSERT OR IGNORE INTO capture_entities (capture_id, entity_id, source) VALUES (?, ?, ?)",
        (capture_id, entity_id, source),
    )
    return entity_id


def get_entities_for_capture(con: sqlite3.Connection, capture_id: int) -> list[dict]:
    rows = con.execute(
        """SELECT e.id, e.name, e.kind, ce.source
           FROM capture_entities ce JOIN entities e ON e.id = ce.entity_id
           WHERE ce.capture_id=? ORDER BY e.name""",
        (capture_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_captures_for_entity(con: sqlite3.Connection, name: str) -> list[dict]:
    normalized = " ".join(name.casefold().split())
    rows = con.execute(
        """SELECT c.id, c.type, c.template_id, c.volume, c.summary, c.created_at,
                  e.name AS entity_name, e.kind
           FROM entities e
           JOIN capture_entities ce ON ce.entity_id = e.id
           JOIN captures c ON c.id = ce.capture_id
           WHERE e.normalized = ?
           ORDER BY c.created_at DESC""",
        (normalized,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_capture_correction(
    con: sqlite3.Connection,
    capture_id: int,
    corrected_text: str,
    content: dict[str, Any],
    summary: str,
    tags: list[dict],
) -> bool:
    """
    Apply an OCR correction to a stored capture.

    Stores *corrected_text* in corrected_ocr (raw_ocr is preserved), replaces
    the parsed fields, summary, and tags, and removes connections derived from
    the old text: tag-overlap edges in both directions and outbound references.
    Inbound references from other captures stay — they cite this capture's ID,
    which the correction does not change.

    The caller should re-run build_connections() afterwards. Returns False if
    the capture does not exist.
    """
    row = con.execute(
        "SELECT id FROM captures WHERE id=?", (capture_id,)
    ).fetchone()
    if row is None:
        return False

    con.execute(
        "UPDATE captures SET corrected_ocr=?, content_json=?, summary=? WHERE id=?",
        (corrected_text, json.dumps(content), summary, capture_id),
    )
    con.execute("DELETE FROM tags WHERE capture_id=?", (capture_id,))
    insert_tags(con, capture_id, tags)
    con.execute(
        "DELETE FROM connections WHERE type='tag_overlap' AND (source_id=? OR target_id=?)",
        (capture_id, capture_id),
    )
    con.execute(
        "DELETE FROM connections WHERE type='reference' AND source_id=?",
        (capture_id,),
    )
    con.commit()
    return True


def insert_connection(
    con: sqlite3.Connection,
    source_id: int,
    target_id: int,
    type_: str,
    strength: float,
    method: str,
    relation: str | None = None,
    note: str | None = None,
    asserted_by: str = "derived",
) -> int:
    """
    Upsert an edge keyed on (source_id, target_id, type).

    Tag/entity overlap is symmetric, so those edges are stored in canonical
    (low id → high id) direction — one row per pair. References and asserted
    edges are directional and keep their true direction. Keying on type means
    different edge kinds coexist on the same pair (the old either-direction
    check silently swallowed references), and re-inserting updates in place
    instead of duplicating.
    """
    if type_ in ("tag_overlap", "entity_overlap") and source_id > target_id:
        source_id, target_id = target_id, source_id
    cur = con.execute(
        """INSERT INTO connections (source_id, target_id, type, strength, method,
                                    relation, note, asserted_by)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_id, target_id, type) DO UPDATE SET
               strength    = excluded.strength,
               method      = excluded.method,
               relation    = excluded.relation,
               note        = excluded.note,
               asserted_by = excluded.asserted_by""",
        (source_id, target_id, type_, strength, method, relation, note, asserted_by),
    )
    row = con.execute(
        "SELECT id FROM connections WHERE source_id=? AND target_id=? AND type=?",
        (source_id, target_id, type_),
    ).fetchone()
    return row["id"] if row else cur.lastrowid


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


def get_capture_by_template(
    con: sqlite3.Connection, template_id: str, type_: str | None = None
) -> dict | None:
    """
    Look up a single capture by its template ID (e.g. "SYN-004"), optionally
    constrained to a type so a caller can't accidentally match the wrong
    template family. Case-insensitive; most recent volume wins on collision.
    """
    sql = "SELECT * FROM captures WHERE template_id=? COLLATE NOCASE"
    params: list[Any] = [template_id]
    if type_:
        sql += " AND type=?"
        params.append(type_.upper())
    row = con.execute(sql + " ORDER BY volume DESC LIMIT 1", params).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["content"] = json.loads(result.pop("content_json"))
    result["tags"] = [
        dict(t) for t in con.execute(
            "SELECT prefix, value, display, role FROM tags WHERE capture_id=?",
            (result["id"],),
        ).fetchall()
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


def get_connections(
    con: sqlite3.Connection,
    capture_id: int,
    volumes: list[int] | None = None,
) -> list[dict]:
    """
    All edges touching *capture_id*, ranked: asserted relations first, then
    references, then entity overlap, then tag overlap.

    Each row carries a "direction" label: directional edges (reference,
    asserted) get 'cites'/'cited_by' relative to *capture_id*; symmetric
    overlap edges get 'shared'.
    """
    vol_sql, vol_params = _volume_where(volumes, alias="cap")
    rows = con.execute(
        f"""SELECT c.id, c.source_id, c.target_id, c.type, c.strength, c.method,
                  c.relation, c.note, c.asserted_by,
                  cap.template_id AS connected_template,
                  cap.volume      AS connected_volume,
                  cap.summary     AS connected_summary,
                  cap.valid_until AS connected_valid_until,
                  CASE
                      WHEN c.type NOT IN ('reference', 'asserted') THEN 'shared'
                      WHEN c.source_id=? THEN 'cites'
                      ELSE 'cited_by'
                  END AS direction
           FROM connections c
           JOIN captures cap ON cap.id = CASE
               WHEN c.source_id=? THEN c.target_id
               ELSE c.source_id
           END
           WHERE (c.source_id=? OR c.target_id=?){vol_sql}
           ORDER BY (c.type='asserted') DESC, (c.type='reference') DESC,
                    (c.type='entity_overlap') DESC, c.strength DESC""",
        [capture_id, capture_id, capture_id, capture_id] + vol_params,
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

        # Connected captures that carry $ insight tags — ranked by strongest
        # edge and capped, matching find_connections()'s limit pattern.
        # Unranked/uncapped here produced a single flashcard's Back field
        # joining hundreds of insight summaries on a well-connected journal
        # (13.9MB response observed on a 986-capture / 331k-edge corpus during
        # the v3.6.0 QA pass) — a handful of the strongest connections is what
        # a usable study card actually needs.
        connected = con.execute(
            """SELECT cap2.id, cap2.summary, cap2.template_id, MAX(conn.strength) AS best_strength
               FROM connections conn
               JOIN captures cap2 ON cap2.id = CASE
                   WHEN conn.source_id=? THEN conn.target_id
                   ELSE conn.source_id
               END
               JOIN tags t ON t.capture_id = cap2.id AND t.prefix = '$'
               WHERE conn.source_id=? OR conn.target_id=?
               GROUP BY cap2.id
               ORDER BY best_strength DESC
               LIMIT 5""",
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


def check_duplicate(
    con: sqlite3.Connection,
    template_id: str,
    volume: int | None = None,
) -> dict | None:
    """
    Return the existing capture dict if *template_id* is already stored,
    otherwise None. When *volume* is given, only that volume collides — a
    second journal legitimately starts over at RC-001.
    """
    sql = ("SELECT id, template_id, volume, summary, created_at FROM captures "
           "WHERE template_id=? COLLATE NOCASE")
    params: list[Any] = [template_id]
    if volume is not None:
        sql += " AND volume=?"
        params.append(volume)
    row = con.execute(sql + " ORDER BY volume DESC", params).fetchone()
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

    # KPIs measure the OWNER'S practice: hand-written journal captures only.
    # AI-extracted entries (source='ai_extract') would inflate velocity and
    # synthesis figures meaninglessly.

    # Totals by type
    type_counts = {
        row["type"]: row["cnt"]
        for row in con.execute(
            "SELECT type, COUNT(*) AS cnt FROM captures WHERE source='journal' GROUP BY type"
        ).fetchall()
    }
    total = sum(type_counts.values())

    # Capture velocity: per week over last 4 weeks
    recent = con.execute(
        "SELECT COUNT(*) AS cnt FROM captures WHERE created_at >= ? AND source='journal'",
        (four_weeks_ago,),
    ).fetchone()["cnt"]
    capture_velocity = round(recent / 4, 1)

    # Insight velocity: $ tags per week over last 4 weeks
    insights_recent = con.execute(
        """SELECT COUNT(*) AS cnt FROM tags t
           JOIN captures c ON c.id = t.capture_id
           WHERE t.prefix='$' AND c.created_at >= ? AND c.source='journal'""",
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
           JOIN tags t ON t.capture_id = c.id AND t.prefix = '?'
           WHERE c.source='journal'""",
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
    role: str = "",
    volumes: list[int] | None = None,
) -> list[dict]:
    """
    Return all captures that carry a tag matching *tag_value* (case-insensitive).
    Optionally filter by *prefix* (the character as written: '#', '@', '?',
    '$', '!', '*', '->') and/or *role* (the canonical meaning: 'topic',
    'theme', 'priority', 'motif', 'entity', ...). Results sorted by
    created_at descending. Each capture row includes the roles its matching
    tag carries, so callers can report prefix-collision splits.
    """
    clauses = ["LOWER(t.value) = LOWER(?)"]
    params: list[Any] = [tag_value]

    if prefix:
        clauses.append("t.prefix = ?")
        params.append(prefix)
    if role:
        clauses.append("t.role = ?")
        params.append(role)
    clauses.append("c.valid_until IS NULL")

    where = " AND ".join(clauses)
    vol_sql, vol_params = _volume_where(volumes)
    rows = con.execute(
        f"""SELECT DISTINCT c.id, c.type, c.template_id, c.volume, c.summary,
                   c.confidence, c.created_at, t.role AS matched_role
            FROM captures c
            JOIN tags t ON t.capture_id = c.id
            WHERE {where}{vol_sql}
            ORDER BY c.created_at DESC
            LIMIT ?""",
        params + vol_params + [limit],
    ).fetchall()

    results = []
    for row in rows:
        r = dict(row)
        r["tags"] = [
            dict(t) for t in con.execute(
                "SELECT prefix, value, role FROM tags WHERE capture_id=?", (r["id"],)
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


def get_topic_evidence_gaps(con: sqlite3.Connection, topic: str) -> dict:
    """
    Evidence against a claimed Solid/Mastered status on *topic*, for
    audit_knowledge_status. Two signals, same "uncited" predicate shape
    run_lint's stale_questions already uses (composed, not reimplemented):

      open_questions   — captures tagged both #topic and an unresolved ?
                          question (never connected to a $ insight)
      uncited_insights — captures tagged both #topic and a $/! insight or
                          priority that no reference edge ever points at
                          (the same propagation-failure shape find_unapplied
                          checks, scoped to this topic instead of one capture)

    Neither has an age cutoff — a status claim being audited today needs to
    hold up against evidence that exists today, not just old evidence.
    """
    open_questions = [dict(r) for r in con.execute(
        """SELECT DISTINCT c.id, c.template_id, c.created_at, t.value AS question
           FROM captures c
           JOIN tags t ON t.capture_id = c.id AND t.prefix = '?'
           WHERE c.valid_until IS NULL
             AND EXISTS (SELECT 1 FROM tags tt WHERE tt.capture_id = c.id
                         AND tt.prefix = '#' AND LOWER(tt.value) = LOWER(?))
             AND NOT EXISTS (
                 SELECT 1 FROM connections e
                 JOIN tags t2 ON t2.prefix = '$' AND t2.capture_id =
                     CASE WHEN e.source_id = c.id THEN e.target_id ELSE e.source_id END
                 WHERE e.source_id = c.id OR e.target_id = c.id)
           ORDER BY c.created_at""",
        (topic,),
    ).fetchall()]

    uncited_insights = [dict(r) for r in con.execute(
        """SELECT DISTINCT c.id, c.template_id, c.created_at, t.value AS insight
           FROM captures c
           JOIN tags t ON t.capture_id = c.id AND t.role IN ('insight', 'priority')
           WHERE c.valid_until IS NULL
             AND EXISTS (SELECT 1 FROM tags tt WHERE tt.capture_id = c.id
                         AND tt.prefix = '#' AND LOWER(tt.value) = LOWER(?))
             AND NOT EXISTS (SELECT 1 FROM connections e
                             WHERE e.target_id = c.id AND e.type = 'reference')
           ORDER BY c.created_at""",
        (topic,),
    ).fetchall()]

    return {"open_questions": open_questions, "uncited_insights": uncited_insights}


def get_dream_cooccurrence(con: sqlite3.Connection, tag: str, window_days: int) -> dict:
    """
    Co-occurrence data between DC captures and RC/REV captures sharing
    #tag, within window_days of each other in either direction. Computed
    once here and shared by dream_correlation() and bridge_dream_research()
    (chain 1: composed, not reimplemented in the companion tool) — see
    ksj-mcp_SPEC_3.0_3.1_2026-08-02.md §1.10a(c) for the non-negotiable
    guardrails this exists to support: report base rates, report
    window_days + match count, never call this "correlation" in output text.
    """
    dc_rows = [dict(r) for r in con.execute(
        """SELECT DISTINCT c.id, c.template_id, c.created_at, c.summary
           FROM captures c JOIN tags t ON t.capture_id = c.id
           WHERE c.type='DC' AND c.valid_until IS NULL
             AND t.prefix='#' AND LOWER(t.value)=LOWER(?)
           ORDER BY c.created_at""",
        (tag,),
    ).fetchall()]
    other_rows = [dict(r) for r in con.execute(
        """SELECT DISTINCT c.id, c.template_id, c.type, c.created_at, c.summary
           FROM captures c JOIN tags t ON t.capture_id = c.id
           WHERE c.type IN ('RC','REV') AND c.valid_until IS NULL
             AND t.prefix='#' AND LOWER(t.value)=LOWER(?)
           ORDER BY c.created_at""",
        (tag,),
    ).fetchall()]

    dc_total = con.execute(
        "SELECT COUNT(*) AS n FROM captures WHERE type='DC' AND valid_until IS NULL"
    ).fetchone()["n"]
    other_total = con.execute(
        "SELECT COUNT(*) AS n FROM captures WHERE type IN ('RC','REV') AND valid_until IS NULL"
    ).fetchone()["n"]

    pairs = []
    for dc in dc_rows:
        dc_dt = datetime.fromisoformat(dc["created_at"])
        for other in other_rows:
            other_dt = datetime.fromisoformat(other["created_at"])
            # Calendar-date difference, not elapsed-time truncation: two
            # entries on consecutive calendar dates but <24h apart (e.g.
            # 11pm and 1am) should read as 1 day apart, not 0 — matching
            # how a journal-keeper would actually describe the gap.
            gap_days = abs((dc_dt.date() - other_dt.date()).days)
            if gap_days <= window_days:
                pairs.append({
                    "dc_id": dc["id"], "dc_template": dc["template_id"],
                    "other_id": other["id"], "other_template": other["template_id"],
                    "other_type": other["type"], "other_summary": other["summary"],
                    "day_gap": gap_days,
                    "direction": (
                        "before" if other_dt < dc_dt
                        else "after" if other_dt > dc_dt
                        else "same day"
                    ),
                })

    return {
        "tag": tag,
        "window_days": window_days,
        "dc_matches": dc_rows,
        "other_matches": other_rows,
        "pairs": pairs,
        "dc_total": dc_total,
        "other_total": other_total,
    }


# ── Search ─────────────────────────────────────────────────────────────────────

def search_fts(
    con: sqlite3.Connection,
    query: str,
    tag_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
    volumes: list[int] | None = None,
    include_superseded: bool = False,
) -> list[dict]:
    """
    Full-text search with optional tag and date filters.

    Superseded captures (valid_until set) are excluded by default — queries
    return the current slice; pass include_superseded=True for history.
    """
    # Quote every token so FTS5 operators and punctuation ('.', '-', NEAR,
    # AND/OR, '*') in user input can't reach the query parser — a query like
    # "KSJ v2.0 upgrade" is otherwise a syntax error.
    tokens = query.split()
    if not tokens:
        return []
    safe_query = " ".join('"' + t.replace('"', '""') + '"' for t in tokens)

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
    if not include_superseded:
        extra_clauses.append("c.valid_until IS NULL")

    extra_where = ("AND " + " AND ".join(extra_clauses)) if extra_clauses else ""
    vol_sql, vol_params = _volume_where(volumes)
    params.extend(vol_params)
    params.append(limit)

    rows = con.execute(
        f"""SELECT c.id, c.type, c.template_id, c.volume, c.summary, c.confidence, c.created_at,
                   rank
            FROM captures_fts
            JOIN captures c ON c.id = captures_fts.rowid
            WHERE captures_fts MATCH ? {extra_where}{vol_sql}
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

    Safe to call on every startup — handles partial migration state (leftover
    _captures_backup table) and exits immediately when already complete.

    Uses isolation_level=None (autocommit mode) with explicit BEGIN/COMMIT
    to avoid conflicts with Python's sqlite3 implicit transaction management.
    """
    path = db_path or _DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)

    # Open a dedicated connection in autocommit mode for reliable DDL control.
    con = sqlite3.connect(str(path), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=OFF")

    try:
        captures_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='captures'"
        ).fetchone()
        backup_exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_captures_backup'"
        ).fetchone() is not None

        # Already migrated with no leftover backup — nothing to do.
        if captures_row and "AIEX" in captures_row["sql"] and not backup_exists:
            return

        # Partial migration: new captures exists with AIEX but backup wasn't dropped.
        if backup_exists and captures_row and "AIEX" in captures_row["sql"]:
            con.execute("DROP TABLE _captures_backup")
            return

        # No captures table yet — init_db will create it with AIEX support.
        if not captures_row and not backup_exists:
            return

        # Full migration needed (old schema without AIEX, or backup-only state).
        con.execute("BEGIN")
        try:
            # If captures still exists with old schema, rename it to backup.
            if captures_row and "AIEX" not in captures_row["sql"]:
                con.execute("ALTER TABLE captures RENAME TO _captures_backup")

            # Drop stale FTS triggers and table (IF EXISTS is safe).
            for trigger in ("captures_fts_insert", "captures_fts_delete", "captures_fts_update"):
                con.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            con.execute("DROP TABLE IF EXISTS captures_fts")

            # Create new captures table with AIEX support.
            con.execute("""
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
                )
            """)

            # Copy existing data and drop backup.
            con.execute("INSERT INTO captures SELECT * FROM _captures_backup")
            con.execute("DROP TABLE _captures_backup")

            # Rebuild tags + connections so their FKs point to captures again.
            # SQLite auto-updated their REFERENCES when we renamed captures →
            # _captures_backup; now that _captures_backup is gone we must fix them.
            con.execute("""
                CREATE TABLE _tags_tmp (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    prefix     TEXT NOT NULL,
                    value      TEXT NOT NULL
                )
            """)
            con.execute("INSERT INTO _tags_tmp SELECT * FROM tags")
            con.execute("DROP TABLE tags")
            con.execute("ALTER TABLE _tags_tmp RENAME TO tags")
            con.execute("CREATE INDEX IF NOT EXISTS idx_tags_capture ON tags(capture_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_tags_value   ON tags(prefix, value)")

            con.execute("""
                CREATE TABLE _connections_tmp (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    target_id INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    type      TEXT NOT NULL,
                    strength  REAL NOT NULL DEFAULT 1.0,
                    method    TEXT NOT NULL
                )
            """)
            con.execute("INSERT INTO _connections_tmp SELECT * FROM connections")
            con.execute("DROP TABLE connections")
            con.execute("ALTER TABLE _connections_tmp RENAME TO connections")
            con.execute("CREATE INDEX IF NOT EXISTS idx_conn_source ON connections(source_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_conn_target ON connections(target_id)")

            # Rebuild FTS virtual table and triggers.
            con.execute("""
                CREATE VIRTUAL TABLE captures_fts USING fts5(
                    raw_ocr, summary,
                    content='captures', content_rowid='id'
                )
            """)
            con.execute("""
                INSERT INTO captures_fts(rowid, raw_ocr, summary)
                SELECT id, raw_ocr, summary FROM captures
            """)
            con.execute("""
                CREATE TRIGGER captures_fts_insert AFTER INSERT ON captures BEGIN
                    INSERT INTO captures_fts(rowid, raw_ocr, summary)
                    VALUES (new.id, new.raw_ocr, new.summary);
                END
            """)
            con.execute("""
                CREATE TRIGGER captures_fts_delete AFTER DELETE ON captures BEGIN
                    INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                    VALUES ('delete', old.id, old.raw_ocr, old.summary);
                END
            """)
            con.execute("""
                CREATE TRIGGER captures_fts_update AFTER UPDATE ON captures BEGIN
                    INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                    VALUES ('delete', old.id, old.raw_ocr, old.summary);
                    INSERT INTO captures_fts(rowid, raw_ocr, summary)
                    VALUES (new.id, new.raw_ocr, new.summary);
                END
            """)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.execute("PRAGMA foreign_keys=ON")
        con.close()


def migrate_fix_fk_references(db_path: Path | None = None) -> None:
    """
    Repair tags/connections tables whose FK references still point to
    _captures_backup after a partial AIEX migration.

    This can happen if migrate_add_aiex ran (renamed captures → backup, created
    new captures, dropped backup) but did not rebuild tags/connections.  With
    foreign_keys=ON, any write to tags or connections will then fail with
    'no such table: main._captures_backup'.

    Safe to call on every startup — exits immediately when not needed.
    """
    path = db_path or _DEFAULT_DB
    if not path.exists():
        return

    con = sqlite3.connect(str(path), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=OFF")

    try:
        tags_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tags'"
        ).fetchone()

        # Nothing to fix if tags doesn't exist or already references captures.
        if not tags_row or "_captures_backup" not in (tags_row["sql"] or ""):
            return

        con.execute("BEGIN")
        try:
            # Rebuild tags
            con.execute("""
                CREATE TABLE _tags_tmp (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    prefix     TEXT NOT NULL,
                    value      TEXT NOT NULL
                )
            """)
            con.execute("INSERT INTO _tags_tmp SELECT * FROM tags")
            con.execute("DROP TABLE tags")
            con.execute("ALTER TABLE _tags_tmp RENAME TO tags")
            con.execute("CREATE INDEX IF NOT EXISTS idx_tags_capture ON tags(capture_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_tags_value   ON tags(prefix, value)")

            # Rebuild connections
            con.execute("""
                CREATE TABLE _connections_tmp (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    target_id INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    type      TEXT NOT NULL,
                    strength  REAL NOT NULL DEFAULT 1.0,
                    method    TEXT NOT NULL
                )
            """)
            con.execute("INSERT INTO _connections_tmp SELECT * FROM connections")
            con.execute("DROP TABLE connections")
            con.execute("ALTER TABLE _connections_tmp RENAME TO connections")
            con.execute("CREATE INDEX IF NOT EXISTS idx_conn_source ON connections(source_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_conn_target ON connections(target_id)")

            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.execute("PRAGMA foreign_keys=ON")
        con.close()


def migrate_add_corrected_ocr(db_path: Path | None = None) -> None:
    """
    Additive migration: corrected_ocr column + FTS triggers that index the
    corrected transcription when one exists.

    Safe to call on every startup — exits immediately when already applied.
    Must run after migrate_add_aiex / migrate_fix_fk_references, which rebuild
    the captures table and triggers with pre-correction definitions.
    """
    path = db_path or _DEFAULT_DB
    if not path.exists():
        return

    con = sqlite3.connect(str(path), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")

    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(captures)").fetchall()]
        if not cols:
            return  # no captures table yet — init_db creates it with the column

        trigger_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='captures_fts_update'"
        ).fetchone()
        triggers_current = trigger_row is not None and "corrected_ocr" in trigger_row["sql"]

        if "corrected_ocr" in cols and triggers_current:
            return

        con.execute("BEGIN")
        try:
            if "corrected_ocr" not in cols:
                con.execute("ALTER TABLE captures ADD COLUMN corrected_ocr TEXT")

            # Recreate FTS sync triggers with COALESCE(corrected_ocr, raw_ocr).
            # All corrected_ocr values are NULL at migration time, so the
            # existing index contents stay valid — no rebuild needed.
            for name in ("captures_fts_insert", "captures_fts_delete", "captures_fts_update"):
                con.execute(f"DROP TRIGGER IF EXISTS {name}")
            con.execute("""
                CREATE TRIGGER captures_fts_insert AFTER INSERT ON captures BEGIN
                    INSERT INTO captures_fts(rowid, raw_ocr, summary)
                    VALUES (new.id, COALESCE(new.corrected_ocr, new.raw_ocr), new.summary);
                END
            """)
            con.execute("""
                CREATE TRIGGER captures_fts_delete AFTER DELETE ON captures BEGIN
                    INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                    VALUES ('delete', old.id, COALESCE(old.corrected_ocr, old.raw_ocr), old.summary);
                END
            """)
            con.execute("""
                CREATE TRIGGER captures_fts_update AFTER UPDATE ON captures BEGIN
                    INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                    VALUES ('delete', old.id, COALESCE(old.corrected_ocr, old.raw_ocr), old.summary);
                    INSERT INTO captures_fts(rowid, raw_ocr, summary)
                    VALUES (new.id, COALESCE(new.corrected_ocr, new.raw_ocr), new.summary);
                END
            """)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


def migrate_v3(db_path: Path | None = None) -> bool:
    """
    One-shot server 3.0 schema migration (§1.13 of the spec). All schema
    changes land as a single unit, in one transaction:

      - template_id becomes nullable ('unidentified' pages are stored)
      - page_suffix column (NULL back-fill)
      - volume column (back-fill 1) + composite unique index
      - entities / capture_entities tables (empty — no legacy back-fill)
      - tags.display (back-fill = value) and tags.role (derived from
        prefix + template type, a pure function of stored data)
      - captures.source ('journal' back-fill; existing AIEX rows → 'ai_extract')
      - settings table (active_volumes='*', current_volume=1)
      - connections table is cleared: the caller MUST run rebuild_connections
        afterwards (edge semantics changed — IDF strengths, typed dedup)

    Copies captures.db to captures.db.bak-v3 before touching anything.
    Safe to call on every startup — no-op once applied. Returns True only
    when the migration actually ran (caller then rebuilds connections).
    """
    path = db_path or _DEFAULT_DB
    if not path.exists():
        return False

    con = sqlite3.connect(str(path), isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=OFF")

    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(captures)").fetchall()]
        if not cols:
            return False   # no captures table — init_db creates v3 directly
        if "volume" in cols:
            return False   # already migrated

        # Flush WAL so the file copy is a complete backup.
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(path, path.with_name(path.name + ".bak-v3"))

        has_corrected = "corrected_ocr" in cols
        corrected_src = "corrected_ocr" if has_corrected else "NULL"

        con.execute("BEGIN")
        try:
            # ── captures ──────────────────────────────────────────────────
            con.execute("ALTER TABLE captures RENAME TO _captures_v2")
            for trigger in ("captures_fts_insert", "captures_fts_delete", "captures_fts_update"):
                con.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            con.execute("DROP TABLE IF EXISTS captures_fts")

            con.execute("""
                CREATE TABLE captures (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    type        TEXT NOT NULL CHECK(type IN ('RC','SYN','REV','DC','AIEX','UNKNOWN')),
                    template_id TEXT,
                    page_suffix TEXT,
                    volume      INTEGER NOT NULL DEFAULT 1,
                    content_json TEXT NOT NULL,
                    raw_ocr     TEXT NOT NULL,
                    corrected_ocr TEXT,
                    summary     TEXT NOT NULL DEFAULT '',
                    confidence  REAL NOT NULL DEFAULT 0.0,
                    image_path  TEXT NOT NULL DEFAULT '',
                    source      TEXT NOT NULL DEFAULT 'journal',
                    created_at  TEXT NOT NULL
                )
            """)
            con.execute(f"""
                INSERT INTO captures (id, type, template_id, page_suffix, volume,
                                      content_json, raw_ocr, corrected_ocr, summary,
                                      confidence, image_path, source, created_at)
                SELECT id, type, template_id, NULL, 1,
                       content_json, raw_ocr, {corrected_src}, summary,
                       confidence, image_path,
                       CASE WHEN type='AIEX' THEN 'ai_extract' ELSE 'journal' END,
                       created_at
                FROM _captures_v2
            """)
            con.execute("DROP TABLE _captures_v2")

            # ── tags (adds display + role) ────────────────────────────────
            con.execute("""
                CREATE TABLE _tags_new (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    capture_id  INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    prefix      TEXT NOT NULL,
                    value       TEXT NOT NULL,
                    display     TEXT,
                    role        TEXT
                )
            """)
            con.execute("""
                INSERT INTO _tags_new (id, capture_id, prefix, value, display, role)
                SELECT id, capture_id, prefix, value, value, NULL FROM tags
            """)
            con.execute("DROP TABLE tags")
            con.execute("ALTER TABLE _tags_new RENAME TO tags")

            # ── connections: recreate empty (caller rebuilds) ─────────────
            con.execute("DROP TABLE connections")
            con.execute("""
                CREATE TABLE connections (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id   INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    target_id   INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    type        TEXT NOT NULL,
                    strength    REAL NOT NULL DEFAULT 1.0,
                    method      TEXT NOT NULL
                )
            """)

            # ── new tables ────────────────────────────────────────────────
            con.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT NOT NULL,
                    normalized  TEXT NOT NULL,
                    kind        TEXT NOT NULL DEFAULT 'other',
                    UNIQUE(normalized, kind)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS capture_entities (
                    capture_id  INTEGER NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
                    entity_id   INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    source      TEXT NOT NULL DEFAULT 'extracted',
                    PRIMARY KEY (capture_id, entity_id)
                )
            """)
            con.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('active_volumes', '*')")
            con.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('current_volume', '1')")

            # ── indexes ───────────────────────────────────────────────────
            con.execute("CREATE INDEX IF NOT EXISTS idx_tags_capture ON tags(capture_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_tags_value   ON tags(prefix, value)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_conn_source  ON connections(source_id)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_conn_target  ON connections(target_id)")
            con.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_captures_vol_tid
                    ON captures(volume, template_id, COALESCE(page_suffix, ''))
            """)
            con.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_conn_unique
                    ON connections(source_id, target_id, type)
            """)

            # ── FTS ───────────────────────────────────────────────────────
            con.execute("""
                CREATE VIRTUAL TABLE captures_fts USING fts5(
                    raw_ocr, summary, content='captures', content_rowid='id'
                )
            """)
            con.execute("""
                INSERT INTO captures_fts(rowid, raw_ocr, summary)
                SELECT id, COALESCE(corrected_ocr, raw_ocr), summary FROM captures
            """)
            con.execute("""
                CREATE TRIGGER captures_fts_insert AFTER INSERT ON captures BEGIN
                    INSERT INTO captures_fts(rowid, raw_ocr, summary)
                    VALUES (new.id, COALESCE(new.corrected_ocr, new.raw_ocr), new.summary);
                END
            """)
            con.execute("""
                CREATE TRIGGER captures_fts_delete AFTER DELETE ON captures BEGIN
                    INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                    VALUES ('delete', old.id, COALESCE(old.corrected_ocr, old.raw_ocr), old.summary);
                END
            """)
            con.execute("""
                CREATE TRIGGER captures_fts_update AFTER UPDATE ON captures BEGIN
                    INSERT INTO captures_fts(captures_fts, rowid, raw_ocr, summary)
                    VALUES ('delete', old.id, COALESCE(old.corrected_ocr, old.raw_ocr), old.summary);
                    INSERT INTO captures_fts(rowid, raw_ocr, summary)
                    VALUES (new.id, COALESCE(new.corrected_ocr, new.raw_ocr), new.summary);
                END
            """)

            # ── tags.role back-fill: pure function of stored data ─────────
            from .templates import assign_role
            rows = con.execute(
                """SELECT t.id, t.prefix, t.value, c.type
                   FROM tags t JOIN captures c ON c.id = t.capture_id"""
            ).fetchall()
            for r in rows:
                con.execute(
                    "UPDATE tags SET role=? WHERE id=?",
                    (assign_role(r["prefix"], r["value"], r["type"]), r["id"]),
                )
            # NOTE: entities are deliberately NOT back-filled from legacy OCR —
            # Tesseract-quality cursive output would populate the table with
            # noise worse than an empty table (§1.13 rule 7).

            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return True
    finally:
        con.execute("PRAGMA foreign_keys=ON")
        con.close()


def migrate_v31(db_path: Path | None = None) -> None:
    """
    Additive server 3.1 migration: bi-temporal fields on captures
    (valid_from back-filled from created_at, valid_until NULL) and typed-edge
    fields on connections (relation, note, asserted_by='derived').

    Plain ALTER TABLE ADD COLUMN — no rebuild, no data movement. Safe to
    call on every startup; no-op once applied. Runs after migrate_v3.
    """
    path = db_path or _DEFAULT_DB
    if not path.exists():
        return

    con = sqlite3.connect(str(path), isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        cap_cols = [r[1] for r in con.execute("PRAGMA table_info(captures)").fetchall()]
        if not cap_cols:
            return  # fresh DB — init_db creates current schema
        conn_cols = [r[1] for r in con.execute("PRAGMA table_info(connections)").fetchall()]
        if "valid_from" in cap_cols and "relation" in conn_cols:
            return

        con.execute("BEGIN")
        try:
            if "valid_from" not in cap_cols:
                con.execute("ALTER TABLE captures ADD COLUMN valid_from TEXT")
                con.execute("ALTER TABLE captures ADD COLUMN valid_until TEXT")
                con.execute("UPDATE captures SET valid_from = created_at")
            if "relation" not in conn_cols:
                con.execute("ALTER TABLE connections ADD COLUMN relation TEXT")
                con.execute("ALTER TABLE connections ADD COLUMN note TEXT")
                con.execute(
                    "ALTER TABLE connections ADD COLUMN asserted_by TEXT NOT NULL DEFAULT 'derived'"
                )
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


# ── JSONL export / import (versioned interchange format) ──────────────────────

EXPORT_SCHEMA_VERSION = "ksj-export-v1"


def export_jsonl(con: sqlite3.Connection) -> str:
    """
    Full knowledge base as JSONL: one header record, then capture / tag /
    entity / capture_entity / edge records. Documented in docs/EXPORT_FORMAT.md.
    Derived edges are included for completeness but import rebuilds them;
    only asserted edges are restored verbatim.
    """
    lines = [json.dumps({
        "kind": "header",
        "schema_version": EXPORT_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    })]

    for r in con.execute("SELECT * FROM captures ORDER BY id").fetchall():
        rec = dict(r)
        rec_fields = json.loads(rec.pop("content_json"))
        lines.append(json.dumps({
            "kind": "capture", "id": rec["id"], "type": rec["type"],
            "template_id": rec["template_id"], "page_suffix": rec["page_suffix"],
            "volume": rec["volume"], "date": rec["created_at"],
            "fields": rec_fields, "raw_ocr": rec["raw_ocr"],
            "corrected_ocr": rec["corrected_ocr"], "summary": rec["summary"],
            "confidence": rec["confidence"], "image_path": rec["image_path"],
            "source": rec["source"], "valid_from": rec["valid_from"],
            "valid_until": rec["valid_until"],
        }, ensure_ascii=False))

    for r in con.execute("SELECT * FROM tags ORDER BY id").fetchall():
        lines.append(json.dumps({
            "kind": "tag", "capture_id": r["capture_id"], "prefix": r["prefix"],
            "value": r["value"], "display": r["display"], "role": r["role"],
        }, ensure_ascii=False))

    for r in con.execute("SELECT * FROM entities ORDER BY id").fetchall():
        lines.append(json.dumps({
            "kind": "entity", "id": r["id"], "name": r["name"],
            "normalized": r["normalized"], "entity_kind": r["kind"],
        }, ensure_ascii=False))

    for r in con.execute("SELECT * FROM capture_entities").fetchall():
        lines.append(json.dumps({
            "kind": "capture_entity", "capture_id": r["capture_id"],
            "entity_id": r["entity_id"], "source": r["source"],
        }, ensure_ascii=False))

    for r in con.execute("SELECT * FROM connections ORDER BY id").fetchall():
        lines.append(json.dumps({
            "kind": "edge", "source": r["source_id"], "target": r["target_id"],
            "type": r["type"], "relation": r["relation"], "strength": r["strength"],
            "method": r["method"], "note": r["note"], "asserted_by": r["asserted_by"],
        }, ensure_ascii=False))

    return "\n".join(lines)


def import_jsonl(con: sqlite3.Connection, text: str) -> dict:
    """
    Restore a ksj-export-v1 JSONL dump. Captures colliding with an existing
    (volume, template_id, suffix) are skipped, so importing into a non-empty
    base is additive, not destructive. Old capture/entity ids are remapped.
    Asserted edges are restored; derived edges must be rebuilt by the caller
    (rebuild_connections) so they reflect the merged base.

    Returns {"captures": n, "skipped": n, "tags": n, "entities": n,
             "asserted_edges": n}.
    """
    stats = {"captures": 0, "skipped": 0, "tags": 0, "entities": 0, "asserted_edges": 0}
    id_map: dict[int, int] = {}       # old capture id -> new
    entity_map: dict[int, int] = {}   # old entity id -> new
    header_seen = False

    records = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))

    for rec in records:
        if rec.get("kind") == "header":
            if rec.get("schema_version") != EXPORT_SCHEMA_VERSION:
                raise ValueError(
                    f"Unsupported export schema {rec.get('schema_version')!r} "
                    f"(this server reads {EXPORT_SCHEMA_VERSION})"
                )
            header_seen = True
    if not header_seen:
        raise ValueError("Not a KSJ export: missing header record.")

    for rec in records:
        kind = rec.get("kind")
        if kind == "capture":
            tid = rec.get("template_id")
            if tid and check_duplicate(con, tid, volume=rec.get("volume", 1)):
                stats["skipped"] += 1
                continue
            cur = con.execute(
                """INSERT INTO captures
                       (type, template_id, page_suffix, volume, content_json,
                        raw_ocr, corrected_ocr, summary, confidence, image_path,
                        source, valid_from, valid_until, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rec["type"], tid, rec.get("page_suffix"), rec.get("volume", 1),
                 json.dumps(rec.get("fields", {})), rec.get("raw_ocr", ""),
                 rec.get("corrected_ocr"), rec.get("summary", ""),
                 rec.get("confidence", 0.0), rec.get("image_path", ""),
                 rec.get("source", "journal"), rec.get("valid_from"),
                 rec.get("valid_until"), rec.get("date") or rec.get("created_at", "")),
            )
            id_map[rec["id"]] = cur.lastrowid
            stats["captures"] += 1
        elif kind == "entity":
            entity_map[rec["id"]] = upsert_entity(
                con, rec["name"], rec.get("entity_kind", "other")
            )
            stats["entities"] += 1

    for rec in records:
        kind = rec.get("kind")
        if kind == "tag" and rec["capture_id"] in id_map:
            con.execute(
                "INSERT INTO tags (capture_id, prefix, value, display, role) VALUES (?, ?, ?, ?, ?)",
                (id_map[rec["capture_id"]], rec["prefix"], rec["value"],
                 rec.get("display", rec["value"]), rec.get("role")),
            )
            stats["tags"] += 1
        elif kind == "capture_entity" and rec["capture_id"] in id_map and rec["entity_id"] in entity_map:
            con.execute(
                "INSERT OR IGNORE INTO capture_entities (capture_id, entity_id, source) VALUES (?, ?, ?)",
                (id_map[rec["capture_id"]], entity_map[rec["entity_id"]],
                 rec.get("source", "extracted")),
            )
        elif kind == "edge" and rec.get("asserted_by") == "user":
            src, tgt = id_map.get(rec["source"]), id_map.get(rec["target"])
            if src and tgt:
                insert_connection(
                    con, src, tgt, rec.get("type", "asserted"),
                    rec.get("strength", 1.0), rec.get("method", "asserted"),
                    relation=rec.get("relation"), note=rec.get("note"),
                    asserted_by="user",
                )
                stats["asserted_edges"] += 1

    con.commit()
    return stats


def get_next_aiex_id(con: sqlite3.Connection) -> str:
    """Return the next sequential AIEX-NNN template ID."""
    row = con.execute(
        """SELECT MAX(CAST(SUBSTR(template_id, 6) AS INTEGER)) AS max_num
           FROM captures WHERE type='AIEX'"""
    ).fetchone()
    next_num = (row["max_num"] or 0) + 1
    return f"AIEX-{next_num:03d}"


def get_stats(con: sqlite3.Connection, volumes: list[int] | None = None) -> dict:
    vol_sql, vol_params = _volume_where(volumes)
    where = f"WHERE 1=1{vol_sql}"
    counts = {
        row["type"]: row["cnt"]
        for row in con.execute(
            f"SELECT type, COUNT(*) AS cnt FROM captures c {where} GROUP BY type",
            vol_params,
        ).fetchall()
    }
    top_tags = con.execute(
        f"""SELECT t.prefix || t.value AS tag, COUNT(*) AS cnt
           FROM tags t JOIN captures c ON c.id = t.capture_id
           WHERE 1=1{vol_sql}
           GROUP BY t.prefix, t.value ORDER BY cnt DESC LIMIT 10""",
        vol_params,
    ).fetchall()
    questions = con.execute(
        f"""SELECT COUNT(*) AS cnt FROM tags t JOIN captures c ON c.id = t.capture_id
           WHERE t.prefix='?'{vol_sql}""",
        vol_params,
    ).fetchone()["cnt"]
    insights = con.execute(
        f"""SELECT COUNT(*) AS cnt FROM tags t JOIN captures c ON c.id = t.capture_id
           WHERE t.prefix='$'{vol_sql}""",
        vol_params,
    ).fetchone()["cnt"]
    date_range = con.execute(
        f"SELECT MIN(created_at) AS earliest, MAX(created_at) AS latest FROM captures c {where}",
        vol_params,
    ).fetchone()
    total = con.execute(
        f"SELECT COUNT(*) AS cnt FROM captures c {where}", vol_params
    ).fetchone()["cnt"]

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
