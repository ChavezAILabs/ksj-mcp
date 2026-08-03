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
from datetime import datetime, timedelta, timezone

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
    # Get tags for this capture. Entity-role tags are excluded — shared
    # entities get their own (higher-ranked) entity_overlap edges, and
    # counting them here would double-weight them.
    rows = con.execute(
        """SELECT prefix, value FROM tags
           WHERE capture_id=? AND (role IS NULL OR role != 'entity')""",
        (capture_id,),
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


def find_entity_connections(con: sqlite3.Connection, capture_id: int) -> list[dict]:
    """
    Find other captures sharing named entities with *capture_id*.

    Entity co-occurrence is a sparser, higher-signal edge than tag overlap —
    two captures both mentioning @Veronica share far more than two captures
    both tagged #screenplay. Strength uses the same IDF form as tags:
    log2(1 + N/df) per shared entity, where df is how many captures mention it.
    """
    my_entities = con.execute(
        "SELECT entity_id FROM capture_entities WHERE capture_id=?", (capture_id,)
    ).fetchall()
    if not my_entities:
        return []

    entity_ids = [r["entity_id"] for r in my_entities]
    total = con.execute("SELECT COUNT(*) AS n FROM captures").fetchone()["n"]
    placeholders = ",".join("?" * len(entity_ids))

    df_rows = con.execute(
        f"""SELECT entity_id, COUNT(DISTINCT capture_id) AS df
            FROM capture_entities WHERE entity_id IN ({placeholders})
            GROUP BY entity_id""",
        entity_ids,
    ).fetchall()
    idf = {r["entity_id"]: math.log2(1 + total / max(r["df"], 1)) for r in df_rows}

    candidates = con.execute(
        f"""SELECT ce.capture_id, ce.entity_id, e.name
            FROM capture_entities ce JOIN entities e ON e.id = ce.entity_id
            WHERE ce.entity_id IN ({placeholders}) AND ce.capture_id != ?""",
        entity_ids + [capture_id],
    ).fetchall()

    overlap: dict[int, dict] = {}
    for row in candidates:
        entry = overlap.setdefault(row["capture_id"], {"names": [], "strength": 0.0})
        entry["names"].append(row["name"])
        entry["strength"] += idf.get(row["entity_id"], 1.0)

    return [
        {
            "target_id":       cid,
            "strength":        round(e["strength"], 2),
            "shared_entities": e["names"],
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

    # Entity co-occurrence (ranked above tag overlap at read time)
    for ec in find_entity_connections(con, capture_id):
        conn_id = insert_connection(
            con,
            source_id=capture_id,
            target_id=ec["target_id"],
            type_="entity_overlap",
            strength=ec["strength"],
            method="entity_overlap",
        )
        target_row = con.execute(
            "SELECT template_id FROM captures WHERE id=?", (ec["target_id"],)
        ).fetchone()
        results.append({
            "connection_id": conn_id,
            "type": "entity_overlap",
            "method": "entity_overlap",
            "strength": ec["strength"],
            "connected_id": ec["target_id"],
            "connected_template": target_row["template_id"] if target_row else "?",
            "shared_tags": [f"@{n}" for n in ec["shared_entities"]],
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
    prior edges. Deletes every DERIVED edge and re-derives them from current
    tags and text; user-asserted edges are never touched — they are
    deliberate human statements, not derivable data.

    Returns {"captures": int, "edges": int, "references": int}.
    """
    con.execute("DELETE FROM connections WHERE asserted_by != 'user'")
    con.commit()

    ids = [r["id"] for r in con.execute("SELECT id FROM captures").fetchall()]
    for cid in ids:
        build_connections(con, cid)

    edges = con.execute("SELECT COUNT(*) AS n FROM connections").fetchone()["n"]
    refs = con.execute(
        "SELECT COUNT(*) AS n FROM connections WHERE type='reference'"
    ).fetchone()["n"]
    return {"captures": len(ids), "edges": edges, "references": refs}


def find_unapplied(con: sqlite3.Connection, capture_id: int, limit: int = 5) -> list[dict]:
    """
    Prior findings this capture probably should have cited and didn't.

    Surfaces earlier captures that (a) carry a $insight or !priority tag,
    (b) share a rare tag or a named entity with *capture_id*, and (c) have
    never been @-referenced by anything. This is the propagation-failure
    check: a finding recorded once and uncited where it mattered.

    Deliberately searches both hand-written and AI-extracted sources — the
    documented failures crossed exactly that boundary.
    """
    this = con.execute(
        "SELECT created_at FROM captures WHERE id=?", (capture_id,)
    ).fetchone()
    if this is None:
        return []

    candidates: dict[int, list[str]] = {}

    # Shared non-entity tags, rare only (df <= 4: a tag half the base carries
    # is not a signal that two captures are about the same finding)
    for row in con.execute(
        """SELECT t2.capture_id AS cid, t2.prefix || t2.value AS tag
           FROM tags t1
           JOIN tags t2 ON t2.prefix = t1.prefix AND t2.value = t1.value
                       AND t2.capture_id != t1.capture_id
           WHERE t1.capture_id = ?
             AND (t1.role IS NULL OR t1.role != 'entity')
             AND (SELECT COUNT(DISTINCT capture_id) FROM tags
                  WHERE prefix = t1.prefix AND value = t1.value) <= 4""",
        (capture_id,),
    ).fetchall():
        candidates.setdefault(row["cid"], []).append(row["tag"])

    # Shared entities (always a strong signal)
    for row in con.execute(
        """SELECT ce2.capture_id AS cid, e.name
           FROM capture_entities ce1
           JOIN capture_entities ce2 ON ce2.entity_id = ce1.entity_id
                                    AND ce2.capture_id != ce1.capture_id
           JOIN entities e ON e.id = ce1.entity_id
           WHERE ce1.capture_id = ?""",
        (capture_id,),
    ).fetchall():
        candidates.setdefault(row["cid"], []).append(f"@{row['name']}")

    if not candidates:
        return []

    results = []
    for cid, shared in candidates.items():
        row = con.execute(
            """SELECT c.id, c.template_id, c.volume, c.summary, c.created_at
               FROM captures c
               WHERE c.id = ? AND c.created_at < ? AND c.valid_until IS NULL
                 AND EXISTS (SELECT 1 FROM tags t WHERE t.capture_id = c.id
                             AND t.role IN ('insight', 'priority'))
                 AND NOT EXISTS (SELECT 1 FROM connections e
                                 WHERE e.target_id = c.id AND e.type = 'reference')""",
            (cid, this["created_at"]),
        ).fetchone()
        if row:
            results.append({
                "id":          row["id"],
                "template_id": row["template_id"],
                "volume":      row["volume"],
                "summary":     row["summary"],
                "created_at":  row["created_at"],
                "shared":      sorted(set(shared)),
            })

    results.sort(key=lambda r: -len(r["shared"]))
    return results[:limit]


# ── Traversal ─────────────────────────────────────────────────────────────────

def _edge_map(con: sqlite3.Connection, min_overlap_strength: float = 2.0) -> dict[int, list[tuple[int, str]]]:
    """
    Undirected adjacency map over the connection graph. Weak tag-overlap
    edges are excluded — traversal over a similarity blob returns the whole
    database at depth 2; references, asserted edges, and entity overlap
    always traverse.
    """
    adj: dict[int, list[tuple[int, str]]] = {}
    rows = con.execute(
        """SELECT source_id, target_id, type, relation, strength FROM connections
           WHERE type != 'tag_overlap' OR strength >= ?""",
        (min_overlap_strength,),
    ).fetchall()
    for r in rows:
        label = r["relation"] or r["type"]
        adj.setdefault(r["source_id"], []).append((r["target_id"], label))
        adj.setdefault(r["target_id"], []).append((r["source_id"], label))
    return adj


def find_path(con: sqlite3.Connection, from_id: int, to_id: int, max_depth: int = 6) -> list[dict] | None:
    """
    Shortest chain of connections between two captures (BFS, undirected).
    Returns a list of {"id", "via"} hops starting at from_id, or None.
    """
    if from_id == to_id:
        return [{"id": from_id, "via": None}]
    adj = _edge_map(con)
    visited = {from_id}
    queue: list[list[tuple[int, str | None]]] = [[(from_id, None)]]
    while queue:
        path = queue.pop(0)
        if len(path) > max_depth:
            continue
        node = path[-1][0]
        for neighbor, label in adj.get(node, []):
            if neighbor in visited:
                continue
            new_path = path + [(neighbor, label)]
            if neighbor == to_id:
                return [{"id": n, "via": v} for n, v in new_path]
            visited.add(neighbor)
            queue.append(new_path)
    return None


def neighborhood(con: sqlite3.Connection, capture_id: int, depth: int = 2, max_nodes: int = 50) -> dict[int, int]:
    """
    Captures reachable from *capture_id* within *depth* hops.
    Returns {capture_id: distance}, excluding the start node.
    """
    adj = _edge_map(con)
    dist = {capture_id: 0}
    frontier = [capture_id]
    for d in range(1, depth + 1):
        nxt = []
        for node in frontier:
            for neighbor, _ in adj.get(node, []):
                if neighbor not in dist:
                    dist[neighbor] = d
                    nxt.append(neighbor)
                    if len(dist) > max_nodes:
                        dist.pop(capture_id)
                        return dist
        frontier = nxt
    dist.pop(capture_id)
    return dist


# ── Lint ──────────────────────────────────────────────────────────────────────

def run_lint(con: sqlite3.Connection, stale_question_days: int = 30) -> dict:
    """
    Karpathy-style knowledge base health check, adapted to KSJ (§2.5):
    orphans, supersession inconsistencies, unresolved refutes pairs, stale
    open questions, and singleton tags (likely normalization failures).
    """
    orphans = [dict(r) for r in con.execute(
        """SELECT c.id, c.template_id, c.volume, c.summary FROM captures c
           WHERE c.valid_until IS NULL
             AND NOT EXISTS (SELECT 1 FROM connections e
                             WHERE e.source_id = c.id OR e.target_id = c.id)
           ORDER BY c.created_at""",
    ).fetchall()]

    # A capture with a supersedes edge pointing at it should be closed out
    stale_claims = [dict(r) for r in con.execute(
        """SELECT DISTINCT c.id, c.template_id, c.summary FROM captures c
           JOIN connections e ON e.target_id = c.id AND e.relation = 'supersedes'
           WHERE c.valid_until IS NULL""",
    ).fetchall()]

    refutes_pairs = [dict(r) for r in con.execute(
        """SELECT e.source_id, e.target_id,
                  cs.template_id AS source_template, ct.template_id AS target_template
           FROM connections e
           JOIN captures cs ON cs.id = e.source_id
           JOIN captures ct ON ct.id = e.target_id
           WHERE e.relation = 'refutes'
             AND cs.valid_until IS NULL AND ct.valid_until IS NULL""",
    ).fetchall()]

    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_question_days)).isoformat()
    stale_questions = [dict(r) for r in con.execute(
        """SELECT DISTINCT c.id, c.template_id, c.created_at, t.value AS question
           FROM captures c
           JOIN tags t ON t.capture_id = c.id AND t.prefix = '?'
           WHERE c.created_at < ? AND c.valid_until IS NULL
             AND NOT EXISTS (
                 SELECT 1 FROM connections e
                 JOIN tags t2 ON t2.prefix = '$' AND t2.capture_id =
                     CASE WHEN e.source_id = c.id THEN e.target_id ELSE e.source_id END
                 WHERE e.source_id = c.id OR e.target_id = c.id)
           ORDER BY c.created_at""",
        (cutoff,),
    ).fetchall()]

    singleton_tags = [dict(r) for r in con.execute(
        """SELECT prefix, value, COUNT(*) AS uses FROM tags
           WHERE prefix IN ('#', '@', '!')
           GROUP BY prefix, value HAVING COUNT(*) = 1
           ORDER BY value LIMIT 25""",
    ).fetchall()]

    return {
        "orphans": orphans,
        "stale_claims": stale_claims,
        "refutes_pairs": refutes_pairs,
        "stale_questions": stale_questions,
        "singleton_tags": singleton_tags,
    }
