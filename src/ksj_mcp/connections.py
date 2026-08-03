"""
Connection detection for KSJ MCP server.

Two methods:
  tag_overlap  — captures sharing schema tags; strength is IDF-weighted:
                 a tag on 3 captures carries far more signal than a tag on
                 200, so strength = Σ log2(1 + N/df) over shared tags
  reference    — explicit @TemplateID references in the capture text
"""

import math
import re
import sqlite3

from .database import get_connections, insert_connection

# Matches @RC-001, @SYN-003, @REV-002, @DC-004 etc.
_REF_PATTERN = re.compile(
    r'@(RC|SYN|REV|DC)-(\d{3})',
    re.IGNORECASE,
)


def find_tag_connections(con: sqlite3.Connection, capture_id: int) -> list[dict]:
    """
    Find other captures that share at least one schema tag with *capture_id*.

    Strength is inverse-document-frequency weighted: each shared tag
    contributes log2(1 + N/df), where N is the total capture count and df
    the number of captures carrying that tag. A ubiquitous tag contributes
    ~1.0; a rare one contributes much more.

    Returns list of dicts sorted by strength (descending):
      {"target_id": int, "strength": float, "shared_tags": [str],
       "shared_count": int}
    """
    # Get tags for this capture
    rows = con.execute(
        "SELECT prefix, value FROM tags WHERE capture_id=?", (capture_id,)
    ).fetchall()
    if not rows:
        return []

    my_tags = [(r["prefix"], r["value"]) for r in rows]
    total = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]

    placeholders = ",".join("(?,?)" for _ in my_tags)
    flat_params = [x for pair in my_tags for x in pair]

    # Document frequency per tag (how many captures carry it)
    df_rows = con.execute(
        f"""SELECT prefix, value, COUNT(DISTINCT capture_id) AS df
            FROM tags
            WHERE (prefix, value) IN ({placeholders})
            GROUP BY prefix, value""",
        flat_params,
    ).fetchall()
    idf = {
        (r["prefix"], r["value"]): math.log2(1 + total / max(r["df"], 1))
        for r in df_rows
    }

    # Find all other captures sharing any of these tags
    candidates = con.execute(
        f"""SELECT capture_id, prefix, value
            FROM tags
            WHERE (prefix, value) IN ({placeholders})
              AND capture_id != ?""",
        flat_params + [capture_id],
    ).fetchall()

    # Accumulate IDF-weighted overlap per candidate
    overlap: dict[int, dict] = {}
    for row in candidates:
        cid = row["capture_id"]
        key = (row["prefix"], row["value"])
        entry = overlap.setdefault(cid, {"tags": [], "strength": 0.0})
        entry["tags"].append(f"{row['prefix']}{row['value']}")
        entry["strength"] += idf.get(key, 1.0)

    return [
        {
            "target_id":    cid,
            "strength":     round(e["strength"], 2),
            "shared_tags":  e["tags"],
            "shared_count": len(e["tags"]),
        }
        for cid, e in sorted(overlap.items(), key=lambda x: -x[1]["strength"])
    ]


def find_reference_connections(
    con: sqlite3.Connection, capture_id: int
) -> list[dict]:
    """
    Find explicit @TemplateID references in the OCR text of *capture_id*.

    Returns list of dicts for each referenced template found in the DB:
      {"target_id": int, "template_id": str, "strength": 1.0}
    """
    row = con.execute(
        "SELECT COALESCE(corrected_ocr, raw_ocr) AS body FROM captures WHERE id=?",
        (capture_id,),
    ).fetchone()
    if row is None:
        return []

    raw_ocr = row["body"]
    refs: list[dict] = []
    seen: set[str] = set()

    for m in _REF_PATTERN.finditer(raw_ocr):
        template_id = f"{m.group(1).upper()}-{m.group(2)}"
        if template_id in seen:
            continue
        seen.add(template_id)

        target = con.execute(
            "SELECT id FROM captures WHERE template_id=? COLLATE NOCASE",
            (template_id,),
        ).fetchone()
        if target:
            refs.append({
                "target_id": target["id"],
                "template_id": template_id,
                "strength": 1.0,
            })

    return refs


def build_connections(con: sqlite3.Connection, capture_id: int) -> list[dict]:
    """
    Run both detection methods for *capture_id*, persist new connections,
    and return a combined list of all connections for this capture.

    Each returned dict:
      {"type": str, "method": str, "strength": float, "connected_id": int,
       "connected_template": str, "shared_tags": list[str]}
    """
    results = []

    # Tag overlap
    for tc in find_tag_connections(con, capture_id):
        conn_id = insert_connection(
            con,
            source_id=capture_id,
            target_id=tc["target_id"],
            type_="tag_overlap",
            strength=tc["strength"],
            method="tag_overlap",
        )
        target_row = con.execute(
            "SELECT template_id FROM captures WHERE id=?", (tc["target_id"],)
        ).fetchone()
        results.append({
            "connection_id": conn_id,
            "type": "tag_overlap",
            "method": "tag_overlap",
            "strength": tc["strength"],
            "connected_id": tc["target_id"],
            "connected_template": target_row["template_id"] if target_row else "?",
            "shared_tags": tc["shared_tags"],
        })

    # @-references
    for rc in find_reference_connections(con, capture_id):
        conn_id = insert_connection(
            con,
            source_id=capture_id,
            target_id=rc["target_id"],
            type_="reference",
            strength=rc["strength"],
            method="reference",
        )
        results.append({
            "connection_id": conn_id,
            "type": "reference",
            "method": "reference",
            "strength": rc["strength"],
            "connected_id": rc["target_id"],
            "connected_template": rc["template_id"],
            "shared_tags": [],
        })

    con.commit()
    return results


def rebuild_connections(con: sqlite3.Connection) -> dict:
    """
    Idempotent full rebuild of the connection graph.

    Needed because edges are otherwise built only at upload time: a page
    referencing @RC-015 before RC-015 exists never gets that edge,
    correct_ocr changes the reference set, and schema migrations invalidate
    prior edges. Deletes every edge and re-derives all of them from current
    tags and text.

    Returns {"captures": int, "edges": int, "references": int}.
    """
    con.execute("DELETE FROM connections")
    con.commit()

    ids = [r["id"] for r in con.execute("SELECT id FROM captures").fetchall()]
    for cid in ids:
        build_connections(con, cid)

    edges = con.execute("SELECT COUNT(*) AS n FROM connections").fetchone()["n"]
    refs = con.execute(
        "SELECT COUNT(*) AS n FROM connections WHERE type='reference'"
    ).fetchone()["n"]
    return {"captures": len(ids), "edges": edges, "references": refs}
