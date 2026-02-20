"""
Connection detection for KSJ MCP server.

Two methods:
  tag_overlap  — captures that share one or more schema tags; strength = overlap count
  reference    — explicit @TemplateID references in OCR text (e.g. @RC-012)
"""

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

    Returns list of dicts sorted by shared-tag count (descending):
      {"target_id": int, "strength": float, "shared_tags": [str]}
    """
    # Get tags for this capture
    rows = con.execute(
        "SELECT prefix, value FROM tags WHERE capture_id=?", (capture_id,)
    ).fetchall()
    if not rows:
        return []

    my_tags = [(r["prefix"], r["value"]) for r in rows]

    # Find all other captures sharing any of these tags
    placeholders = ",".join("(?,?)" for _ in my_tags)
    flat_params = [x for pair in my_tags for x in pair]
    candidates = con.execute(
        f"""SELECT capture_id, prefix, value
            FROM tags
            WHERE (prefix, value) IN ({placeholders})
              AND capture_id != ?""",
        flat_params + [capture_id],
    ).fetchall()

    # Count overlap per candidate
    overlap: dict[int, list[str]] = {}
    for row in candidates:
        cid = row["capture_id"]
        tag = f"{row['prefix']}{row['value']}"
        overlap.setdefault(cid, []).append(tag)

    return [
        {"target_id": cid, "strength": float(len(tags)), "shared_tags": tags}
        for cid, tags in sorted(overlap.items(), key=lambda x: -len(x[1]))
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
        "SELECT raw_ocr FROM captures WHERE id=?", (capture_id,)
    ).fetchone()
    if row is None:
        return []

    raw_ocr = row["raw_ocr"]
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
