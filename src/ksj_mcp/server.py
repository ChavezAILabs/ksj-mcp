"""
KSJ MCP Server — FastMCP entry point.

16 tools:
  upload_capture     — OCR a journal photo and store it
  manual_capture     — Log a capture from transcribed text (no OCR needed)
  bulk_upload        — Process a whole folder of photos at once
  search_captures    — Full-text search with optional filters
  list_by_tag        — Browse all captures with a given tag or prefix
  find_connections   — Tag overlap + @-reference connections for a capture
  get_stats          — Summary counts, top tags, open questions
  export_captures    — Dump captures as Markdown or JSON
  suggest_synthesis  — Find RC clusters ready for a SYN entry
  export_study_deck  — Export ? questions as a portable study deck CSV
  journal_health     — KPI dashboard + coaching recommendations
  get_breakthroughs  — All SYN entries chronologically with insights
  dream_patterns     — Recurring symbols, emotions, themes across DC pages
  knowledge_progress — REV knowledge status progression by topic
  extract_insights   — Load DB context for AI extraction of a research session
  commit_aiex        — Write confirmed AIEX insights to the knowledge base
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .database import (
    check_duplicate,
    get_capture,
    get_captures_by_tag,
    get_connections,
    get_dc_pattern_data,
    get_journal_kpis,
    get_next_aiex_id,
    get_question_captures,
    get_rc_tag_clusters,
    get_rev_progress,
    get_stats as db_get_stats,
    get_syn_breakthroughs,
    init_db,
    insert_capture,
    insert_tags,
    list_captures,
    migrate_add_aiex,
    search_fts,
    get_connection,
)
from .connections import build_connections
from .ocr import OcrNotAvailableError, detect_template_type, extract_text
from .templates import parse_template

# ── Server init ───────────────────────────────────────────────────────────────

mcp = FastMCP(
    name="ksj",
    instructions="""
You are an AI assistant integrated with the Knowledge Synthesis Journal
(KSJ) v2.0 system via MCP server. You help users capture, synthesize,
review, and understand their journal entries through photo uploads and
direct queries.

## The 4 Templates

**Rapid Capture (RC-001 to RC-040)**
Fast note-taking with schema tags. Left page: dot grid.

**Synthesis (SYN-001 to SYN-010)**
Connecting ideas and identifying breakthroughs (★). Left page: isometric grid.

**Review (REV-001 to REV-008)**
Reflection on learning periods. Tracks knowledge status:
Needs Work → Solid → Mastered. Left page: quad ruled grid.

**Dream Capture (DC-001 to DC-008)**
Morning dream recording. Captures narrative, characters, symbols,
emotions, sensory details, and waking life context.

**AI Insight Extraction (AIEX-001, AIEX-002, ...)**
AI-assisted extraction of high-value insights from research sessions.
Entries are generated digitally (no OCR) and written directly to the
database. Each confirmed insight gets its own sequential AIEX-NNN ID.
Use extract_insights() to prepare a session, then commit_aiex() to store.

## Schema Tag System
RC, SYN, REV pages:
- `#topic` — subject or theme
- `@source` — origin of information
- `!priority` — urgent or important
- `?question` — open questions
- `$insight` — breakthrough realization
- `A→B` — cause/effect or connection

DC (Dream Capture) pages use a dream-specific variant:
- `#theme` — dream theme or subject
- `@symbol` — recurring symbol or character
- `!recurring` — recurring dream motif
- `*sensory` — sensory detail (unique to DC)

## What You Can Do
- Search and retrieve entries by tag, template, or concept
- Identify patterns across entries over time
- Generate study decks from $insight and key content (platform-agnostic CSV)
- Analyze dream patterns across DC entries
- Track knowledge status progression from REV entries
- Surface breakthrough connections across RC and SYN entries

## Input Method
Users upload photos of journal pages. Extract structured content
and tags before responding. Prioritize accuracy over speed when
reading handwritten content.
""".strip(),
)

def _data_dir() -> Path:
    """Return the KSJ data directory.

    Resolution order:
    1. KSJ_DATA_DIR environment variable (absolute path)
    2. ~/.ksj-mcp/  (stable across uvx runs and uv cache cleans)
    """
    env = os.environ.get("KSJ_DATA_DIR")
    return Path(env) if env else Path.home() / ".ksj-mcp"


_DB_PATH     = _data_dir() / "captures.db"
_IMAGES_DIR  = _data_dir() / "images"

init_db(_DB_PATH)
migrate_add_aiex(_DB_PATH)
_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}


def _db():
    return get_connection(_DB_PATH)


# ── Shared upload helper ──────────────────────────────────────────────────────

def _process_image(image_path: str, force: bool = False) -> dict:
    """
    Core upload pipeline: OCR → duplicate check → parse → store → copy image
    → detect connections → highlight strongest connection.

    Returns a result dict:
      {
        "ok":           bool,
        "error":        str | None,
        "capture_id":   int | None,
        "template_id":  str,
        "summary":      str,
        "tags":         list,
        "confidence":   float,
        "connections":  list,
        "highlight":    dict | None,   # strongest / most surprising connection
        "duplicate":    dict | None,   # existing capture if dupe was found
        "stored_image": str,           # path inside data/images/
      }
    """
    result = {
        "ok": False, "error": None, "capture_id": None,
        "template_id": "", "summary": "", "tags": [],
        "confidence": 0.0, "connections": [], "highlight": None,
        "duplicate": None, "stored_image": "",
    }

    # OCR
    try:
        ocr_result = extract_text(image_path)
    except OcrNotAvailableError as e:
        result["error"] = f"OCR Error:\n\n{e}"
        return result
    except FileNotFoundError:
        result["error"] = f"File not found: {image_path}"
        return result
    except Exception as e:
        result["error"] = f"Unexpected OCR error: {e}"
        return result

    raw_text      = ocr_result["raw_text"]
    template_type = ocr_result["template_type"]
    template_id   = ocr_result["template_id"]
    confidence    = ocr_result["confidence"]

    if template_type == "UNKNOWN":
        result["error"] = (
            "Could not detect a template ID (RC-XXX / SYN-XXX / REV-XXX / DC-XXX). "
            "Make sure the template number is visible and the photo is clear.\n"
            "Tip: try better lighting or hold the camera more parallel to the page."
        )
        if confidence < 0.6:
            result["error"] += f"\nOCR confidence was low ({confidence:.0%}) — retaking the photo may help."
        return result

    # Low-confidence warning (non-fatal)
    low_conf_warning = ""
    if confidence < 0.6:
        low_conf_warning = (
            f"\n  ⚠ Low OCR confidence ({confidence:.0%}) — consider retaking with better lighting "
            "or holding the camera more parallel to the page."
        )

    result["template_id"] = template_id
    result["confidence"]  = confidence

    with _db() as con:
        # Duplicate detection
        existing = check_duplicate(con, template_id)
        if existing and not force:
            result["duplicate"] = existing
            result["error"] = (
                f"{template_id} already exists in your knowledge base "
                f"(stored {existing['created_at'][:10]}, #{existing['id']}).\n"
                f"  Summary: {existing['summary'] or '(none)'}\n\n"
                f"To replace it, upload again with force=True."
            )
            return result

        # Parse template
        parsed  = parse_template(template_type, raw_text)
        summary = parsed["summary"]
        tags    = parsed["tags"]

        result["summary"] = summary
        result["tags"]    = tags

        # Copy image to data/images/ for self-containment
        src = Path(image_path)
        ts  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        dest = _IMAGES_DIR / f"{template_id}_{ts}{src.suffix.lower()}"
        try:
            shutil.copy2(src, dest)
            stored_image = str(dest)
        except Exception:
            stored_image = image_path  # fall back to original path

        result["stored_image"] = stored_image

        # Store capture
        capture_id = insert_capture(
            con,
            type_=template_type,
            template_id=template_id,
            content=parsed["fields"],
            raw_ocr=raw_text,
            summary=summary,
            confidence=confidence,
            image_path=stored_image,
        )
        insert_tags(con, capture_id, tags)
        con.commit()

        # Detect connections
        connections = build_connections(con, capture_id)

        # Find strongest / most surprising connection for highlight
        highlight = None
        if connections:
            # Sort: prefer tag_overlap with highest strength; break ties by age (oldest = most surprising)
            def _score(c):
                age_days = 0
                other_cap = get_capture(con, c["connected_id"])
                if other_cap:
                    try:
                        dt = datetime.fromisoformat(other_cap["created_at"])
                        age_days = (datetime.now(timezone.utc) - dt).days
                    except Exception:
                        pass
                return (c["strength"], age_days)

            best = max(connections, key=_score)
            other = get_capture(con, best["connected_id"])
            if other:
                age_days = 0
                try:
                    dt = datetime.fromisoformat(other["created_at"])
                    age_days = (datetime.now(timezone.utc) - dt).days
                except Exception:
                    pass
                highlight = {
                    "template_id": other["template_id"],
                    "summary":     other["summary"],
                    "strength":    best["strength"],
                    "age_days":    age_days,
                    "shared_tags": best.get("shared_tags", []),
                    "method":      best["method"],
                }

    result["ok"]          = True
    result["capture_id"]  = capture_id
    result["connections"] = connections
    result["highlight"]   = highlight
    result["_low_conf"]   = low_conf_warning
    return result


def _format_upload_result(r: dict, image_path: str) -> str:
    """Format a _process_image result dict as a human-readable string."""
    if not r["ok"]:
        return r["error"]

    tag_list = ", ".join(f"{t['prefix']}{t['value']}" for t in r["tags"]) or "none"
    lines = [
        f"Stored capture #{r['capture_id']}",
        f"  Template : {r['template_id']}",
        f"  Summary  : {r['summary'] or '(empty)'}",
        f"  Tags     : {tag_list}",
        f"  OCR conf : {r['confidence']:.0%}",
    ]

    if r.get("_low_conf"):
        lines.append(r["_low_conf"])

    conns = r["connections"]
    if not conns:
        lines.append("  No connections to existing captures yet.")
    else:
        lines.append(f"  {len(conns)} connection(s) detected:")
        for c in conns:
            shared = f" (shared: {', '.join(c['shared_tags'])})" if c.get("shared_tags") else ""
            lines.append(f"    → {c['connected_template']} [{c['method']}]{shared}")

    # Connection highlight — the "wow" moment
    h = r["highlight"]
    if h:
        age_str = f"{h['age_days']} day{'s' if h['age_days'] != 1 else ''} ago" if h["age_days"] > 0 else "recently"
        shared_str = f" — shared: {', '.join(h['shared_tags'])}" if h["shared_tags"] else ""
        lines.append(
            f"\n  ★ Strongest connection: {h['template_id']} ({age_str})\n"
            f"    \"{h['summary'] or '(no summary)'}\"{shared_str}"
        )

    return "\n".join(lines)


# ── Tool: upload_capture ──────────────────────────────────────────────────────

@mcp.tool()
def upload_capture(image_path: str, force: bool = False) -> str:
    """
    Process a journal page photo: run OCR, parse the template, extract schema
    tags, store the capture, copy the image to the knowledge base, and detect
    connections to existing captures.

    Args:
        image_path: Absolute path to the image file (JPG, PNG, TIFF, etc.)
        force:      Set to True to overwrite an existing capture with the same
                    template ID (default False — warns instead).

    Returns a summary of what was found and stored, including the strongest
    connection detected.
    """
    result = _process_image(image_path, force=force)
    return _format_upload_result(result, image_path)


# ── Tool: manual_capture ──────────────────────────────────────────────────────

@mcp.tool()
def manual_capture(text: str, template_id: str = "", force: bool = False) -> str:
    """
    Log a journal capture from transcribed text, bypassing OCR entirely.

    Use this when upload_capture cannot access the image file — for example
    when the file path is not reachable by the MCP server process. Provide
    the text you have already read or transcribed from the journal page.

    Args:
        text:        The transcribed content of the journal page (all fields
                     you can read — First Impressions, Key Points, Tags, etc.).
        template_id: Template ID (e.g. "RC-001"). If omitted, the server will
                     try to detect it from the text automatically.
        force:       Set to True to overwrite an existing capture with the
                     same template ID (default False — warns instead).

    Returns the same summary as upload_capture, including any connections
    detected to existing captures.
    """
    # Detect template ID from text if caller did not provide one
    if template_id:
        template_type, tid = detect_template_type(template_id)
        if template_type == "UNKNOWN":
            # Caller passed something like "RC-001" — try matching directly
            template_type, tid = detect_template_type(template_id.upper())
        if template_type == "UNKNOWN":
            return (
                f"Could not parse template ID '{template_id}'. "
                "Expected format: RC-001, SYN-003, REV-002, DC-005, etc."
            )
    else:
        template_type, tid = detect_template_type(text)
        if template_type == "UNKNOWN":
            return (
                "Could not detect a template ID (RC-XXX / SYN-XXX / REV-XXX / DC-XXX) "
                "in the provided text. Please pass template_id explicitly, "
                "e.g. template_id=\"RC-001\"."
            )

    result = {
        "ok": False, "error": None, "capture_id": None,
        "template_id": tid, "summary": "", "tags": [],
        "confidence": 1.0, "connections": [], "highlight": None,
        "duplicate": None, "stored_image": "",
        "_low_conf": "",
    }

    with _db() as con:
        existing = check_duplicate(con, tid)
        if existing and not force:
            result["duplicate"] = existing
            result["error"] = (
                f"{tid} already exists in your knowledge base "
                f"(stored {existing['created_at'][:10]}, #{existing['id']}).\n"
                f"  Summary: {existing['summary'] or '(none)'}\n\n"
                f"To replace it, call manual_capture again with force=True."
            )
            return _format_upload_result(result, "")

        parsed  = parse_template(template_type, text)
        summary = parsed["summary"]
        tags    = parsed["tags"]

        result["summary"] = summary
        result["tags"]    = tags

        capture_id = insert_capture(
            con,
            type_=template_type,
            template_id=tid,
            content=parsed["fields"],
            raw_ocr=text,
            summary=summary,
            confidence=1.0,
            image_path="",
        )
        insert_tags(con, capture_id, tags)
        con.commit()

        connections = build_connections(con, capture_id)

        highlight = None
        if connections:
            def _score(c):
                age_days = 0
                other_cap = get_capture(con, c["connected_id"])
                if other_cap:
                    try:
                        dt = datetime.fromisoformat(other_cap["created_at"])
                        age_days = (datetime.now(timezone.utc) - dt).days
                    except Exception:
                        pass
                return (c["strength"], age_days)

            best  = max(connections, key=_score)
            other = get_capture(con, best["connected_id"])
            if other:
                age_days = 0
                try:
                    dt = datetime.fromisoformat(other["created_at"])
                    age_days = (datetime.now(timezone.utc) - dt).days
                except Exception:
                    pass
                highlight = {
                    "template_id": other["template_id"],
                    "summary":     other["summary"],
                    "strength":    best["strength"],
                    "age_days":    age_days,
                    "shared_tags": best.get("shared_tags", []),
                    "method":      best["method"],
                }

    result["ok"]          = True
    result["capture_id"]  = capture_id
    result["connections"] = connections
    result["highlight"]   = highlight
    return _format_upload_result(result, "")


# ── Tool: bulk_upload ─────────────────────────────────────────────────────────

@mcp.tool()
def bulk_upload(folder_path: str, force: bool = False) -> str:
    """
    Process all journal page photos in a folder at once.

    Finds every image file (JPG, PNG, TIFF, BMP, WebP) in the folder and runs
    the full upload pipeline on each one. Non-image files are skipped silently.

    Args:
        folder_path: Absolute path to the folder containing journal photos.
        force:       Set to True to overwrite existing captures with matching
                     template IDs (default False — skips duplicates with a warning).

    Returns a summary table of all processed images.
    """
    folder = Path(folder_path)
    if not folder.exists():
        return f"Folder not found: {folder_path}"
    if not folder.is_dir():
        return f"Not a folder: {folder_path}"

    images = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    )

    if not images:
        return f"No image files found in {folder_path}"

    ok_count = dupe_count = error_count = 0
    lines = [f"Bulk upload — {len(images)} image(s) found in {folder_path}\n{'─' * 50}"]

    for img in images:
        result = _process_image(str(img), force=force)

        if result["duplicate"] and not force:
            dupe_count += 1
            lines.append(
                f"  SKIP  {img.name}\n"
                f"        {result['template_id']} already exists (#{result['duplicate']['id']}) — use force=True to overwrite"
            )
        elif not result["ok"]:
            error_count += 1
            err = (result["error"] or "Unknown error").split("\n")[0]
            lines.append(f"  ERROR {img.name}\n        {err}")
        else:
            ok_count += 1
            tag_count  = len(result["tags"])
            conn_count = len(result["connections"])
            highlight  = ""
            if result["highlight"]:
                h = result["highlight"]
                highlight = f"  ★ → {h['template_id']}"
            lines.append(
                f"  OK    {img.name}  →  {result['template_id']}  "
                f"[{tag_count} tag(s), {conn_count} connection(s)]{highlight}"
            )

    lines.append(f"\n{'─' * 50}")
    lines.append(f"Done: {ok_count} stored, {dupe_count} skipped (duplicate), {error_count} failed")
    return "\n".join(lines)


# ── Tool: search_captures ─────────────────────────────────────────────────────

@mcp.tool()
def search_captures(
    query: str,
    tag_filter: str = "",
    date_from: str = "",
    date_to: str = "",
) -> str:
    """
    Search all journal entries by concept, keyword, or phrase — across every
    template type (RC, SYN, REV, DC) at once.

    This is the primary way to find entries by idea rather than tag. Use it
    whenever the user asks to find notes, recall something they wrote, or
    explore a topic. Natural language queries work well.

    Examples:
      "neural networks"        → entries mentioning neural networks
      "why does attention"     → entries with that question or phrase
      "spaced repetition"      → concept search across all templates
      "dream flying"           → DC entries with flying imagery

    Args:
        query:      The concept, keyword, or phrase to search for.
        tag_filter: Optional tag value to narrow results (e.g. "machine-learning").
        date_from:  Optional ISO date lower bound (e.g. "2025-09-01").
        date_to:    Optional ISO date upper bound (e.g. "2025-12-31").

    Note: search matches terms that appear in the journal text. For tag-only
    browsing without a text query, use list_by_tag instead.
    """
    if not query.strip():
        return "Please provide a search query."

    with _db() as con:
        results = search_fts(
            con,
            query=query,
            tag_filter=tag_filter or None,
            date_from=date_from or None,
            date_to=date_to or None,
        )

    if not results:
        return f"No captures found for query: {query!r}"

    lines = [f"Found {len(results)} capture(s) for {query!r}:\n"]
    for r in results:
        tag_str = " ".join(f"{t['prefix']}{t['value']}" for t in r.get("tags", [])[:5])
        lines.append(
            f"  [{r['template_id']}] #{r['id']}  conf={r['confidence']:.0%}\n"
            f"    {r['summary'] or '(no summary)'}\n"
            f"    Tags: {tag_str or 'none'}\n"
            f"    Date: {r['created_at'][:10]}\n"
        )
    return "\n".join(lines)


# ── Tool: find_connections ────────────────────────────────────────────────────

@mcp.tool()
def find_connections(capture_id: int) -> str:
    """
    Show all connections for a specific capture (tag overlap and @-references).

    Args:
        capture_id: The numeric ID returned by upload_capture or search_captures.
    """
    with _db() as con:
        capture = get_capture(con, capture_id)
        if capture is None:
            return f"Capture #{capture_id} not found."
        connections = get_connections(con, capture_id)

    if not connections:
        return (
            f"No connections found for {capture['template_id']} (#{capture_id}).\n"
            "Upload more captures to discover relationships."
        )

    lines = [f"Connections for {capture['template_id']} (#{capture_id}):\n"]
    for c in connections:
        method_label = c["method"].replace("_", " ")
        lines.append(
            f"  → {c['connected_template']}  [{method_label}]  strength={c['strength']:.0f}\n"
            f"    {c['connected_summary'] or '(no summary)'}"
        )
    return "\n".join(lines)


# ── Tool: get_stats ───────────────────────────────────────────────────────────

@mcp.tool()
def get_stats() -> str:
    """
    Return an overview of your knowledge base: capture counts, top tags,
    open questions, key insights, and date range.
    """
    with _db() as con:
        stats = db_get_stats(con)

    if stats["total_captures"] == 0:
        return "Your knowledge base is empty. Upload a journal photo to get started."

    by_type = stats["by_type"]
    type_lines = "\n".join(
        f"  {t}: {by_type.get(t, 0)}"
        for t in ("RC", "SYN", "REV", "DC", "AIEX")
    )
    top_tags = "\n".join(
        f"  {r['tag']}  ({r['cnt']} captures)"
        for r in stats["top_tags"]
    )
    dr = stats["date_range"]
    date_str = (
        f"{dr['earliest'][:10]}  →  {dr['latest'][:10]}"
        if dr["earliest"]
        else "n/a"
    )

    return (
        f"Knowledge Base Stats\n"
        f"{'─' * 40}\n"
        f"Total captures : {stats['total_captures']}\n\n"
        f"By type:\n{type_lines}\n\n"
        f"Open questions (?)  : {stats['open_questions']}\n"
        f"Key insights  ($)   : {stats['key_insights']}\n\n"
        f"Top tags:\n{top_tags or '  (none yet)'}\n\n"
        f"Date range: {date_str}"
    )


# ── Tool: export_captures ─────────────────────────────────────────────────────

@mcp.tool()
def export_captures(format: str = "markdown", tag_filter: str = "") -> str:
    """
    Export all captures (or a tag-filtered subset) as Markdown or JSON.

    Args:
        format:     "markdown" (default) or "json"
        tag_filter: Optional tag value — only include captures with this tag
                    (e.g. "machine-learning")

    Returns the full export as a string (no file is written).
    """
    fmt = format.lower().strip()
    if fmt not in ("markdown", "json"):
        return 'Invalid format. Use "markdown" or "json".'

    with _db() as con:
        if tag_filter:
            ids = [
                r["capture_id"]
                for r in con.execute(
                    "SELECT DISTINCT capture_id FROM tags WHERE value LIKE ?",
                    (f"%{tag_filter}%",),
                ).fetchall()
            ]
            captures = [get_capture(con, cid) for cid in ids if cid]
            captures = [c for c in captures if c]
        else:
            rows = list_captures(con, limit=1000)
            captures = [get_capture(con, r["id"]) for r in rows]
            captures = [c for c in captures if c]

    if not captures:
        return "No captures to export" + (f" with tag filter: {tag_filter!r}" if tag_filter else "") + "."

    if fmt == "json":
        return json.dumps(captures, indent=2, default=str)

    lines = ["# KSJ Knowledge Base Export\n"]
    for c in captures:
        tags_str = " ".join(f"{t['prefix']}{t['value']}" for t in c.get("tags", []))
        lines.append(f"## {c['template_id']}  (#{c['id']})")
        lines.append(f"**Date:** {c['created_at'][:10]}  |  **Confidence:** {c['confidence']:.0%}")
        lines.append(f"**Tags:** {tags_str or 'none'}")
        lines.append(f"\n{c['summary'] or '*(no summary)*'}\n")

        content = c.get("content", {})
        if content:
            for field, val in content.items():
                if val and field != "tags_raw":
                    lines.append(f"**{field.replace('_', ' ').title()}:**")
                    lines.append(str(val))
        lines.append("---\n")

    return "\n".join(lines)


# ── Tool: suggest_synthesis ───────────────────────────────────────────────────

@mcp.tool()
def suggest_synthesis(min_captures: int = 3) -> str:
    """
    Scan your Rapid Capture entries and identify topic clusters ready to be
    synthesized into a SYN page.

    Args:
        min_captures: Minimum number of RC entries on a topic to flag it
                      (default 3).
    """
    with _db() as con:
        clusters = get_rc_tag_clusters(con, min_size=min_captures)

    if not clusters:
        return (
            f"No topic clusters found with {min_captures}+ RC entries yet.\n"
            "Keep capturing — suggestions appear once a theme builds up."
        )

    ready       = [c for c in clusters if not c["syn_exists"]]
    in_progress = [c for c in clusters if c["syn_exists"]]

    lines = ["Synthesis Suggestions\n" + "─" * 40]

    if ready:
        lines.append(f"\n★ Ready to synthesize ({len(ready)} topic(s)):\n")
        for c in ready:
            pages = ", ".join(c["rc_templates"])
            lines.append(
                f"  #{c['tag']}  —  {c['rc_count']} RC entries\n"
                f"    Pages: {pages}\n"
                f"    → Open a new SYN page and connect these ideas.\n"
            )

    if in_progress:
        lines.append(f"\n↻ Already synthesizing ({len(in_progress)} topic(s)):\n")
        for c in in_progress:
            syn_pages = ", ".join(c["syn_templates"])
            lines.append(
                f"  #{c['tag']}  —  {c['rc_count']} RC entries\n"
                f"    SYN: {syn_pages}  (consider updating with new captures)\n"
            )

    return "\n".join(lines)


# ── Tool: export_study_deck ───────────────────────────────────────────────────

@mcp.tool()
def export_study_deck(tag_filter: str = "") -> str:
    """
    Export your open questions as a portable study deck (tab-separated CSV).

    Turns every ? question in your journal into a flashcard:
      Front — the question (from the ? tag)
      Back  — connected $ insight captures; falls back to the capture summary
      Tags  — the # topic tags on that capture

    The output is a standard tab-separated CSV compatible with:
      - Anki (File → Import → Tab-separated)
      - Quizlet (Import → Tab between terms, newline between cards)
      - Obsidian, Notion, Google Sheets, or any CSV-aware tool
      - Print as a plain study sheet — no app required

    Args:
        tag_filter: Optional # topic tag to limit the export
                    (e.g. "machine-learning"). Leave blank for all questions.

    Returns a tab-separated text block. No file is written to disk.
    """
    with _db() as con:
        question_caps = get_question_captures(con)

    if tag_filter:
        question_caps = [
            c for c in question_caps
            if tag_filter.lower() in c["topics"]
        ]

    if not question_caps:
        msg = "No ? questions found"
        if tag_filter:
            msg += f" with topic #{tag_filter}"
        return msg + ". Upload captures with ?question tags to build your study deck."

    lines = [
        "#separator:tab",
        "#html:false",
        "#columns:Front\tBack\tTags",
        "",
    ]

    for cap in question_caps:
        for question in cap["questions"]:
            front = question.replace("-", " ").capitalize()
            if not front.endswith("?"):
                front += "?"

            if cap["insights"]:
                back_parts = [i["summary"] for i in cap["insights"] if i["summary"]]
                back = " | ".join(back_parts) if back_parts else cap["summary"]
            else:
                back = cap["summary"] or f"(see {cap['template_id']})"

            tags_str = " ".join(
                t.replace(" ", "-") for t in cap["topics"]
            ) if cap["topics"] else "ksj"

            lines.append(f"{front.replace(chr(9),' ')}\t{back.replace(chr(9),' ')}\t{tags_str}")

    card_count = len(lines) - 4
    lines.insert(0, f"# KSJ Study Deck Export — {card_count} card(s)\n")
    return "\n".join(lines)


# ── Tool: journal_health ──────────────────────────────────────────────────────

@mcp.tool()
def journal_health() -> str:
    """
    KPI dashboard and coaching recommendations for your journal practice.

    Tracks:
      - Capture velocity (captures/week over last 4 weeks)
      - Insight velocity ($ insights/week)
      - Days since last Review entry
      - Unanswered open questions and their age
      - Synthesis ratio (RC entries per SYN page — target ~4:1)
      - Template balance (which template types are unused)

    Returns a health score and specific, actionable recommendations.
    """
    with _db() as con:
        kpis = get_journal_kpis(con)

    if kpis["total"] == 0:
        return "Your knowledge base is empty. Upload a journal photo to get started."

    by_type = kpis["by_type"]
    recommendations = []
    score_penalties  = 0

    # ── Synthesis ratio check ──────────────────────────────────────────
    ratio = kpis["synthesis_ratio"]
    rc    = by_type.get("RC", 0)
    syn   = by_type.get("SYN", 0)
    if ratio is None and rc >= 4:
        recommendations.append(
            "★ You have RC entries but no SYN pages yet. "
            f"With {rc} rapid captures, you're ready to synthesize. "
            "Open SYN-001 and look for patterns."
        )
        score_penalties += 2
    elif ratio and ratio > 8:
        recommendations.append(
            f"★ Synthesis backlog: {rc} RC entries for only {syn} SYN page(s) "
            f"({ratio:.0f}:1 ratio, target ~4:1). "
            "Time to connect some dots — run suggest_synthesis to see what's ready."
        )
        score_penalties += 2
    elif ratio and ratio > 4:
        recommendations.append(
            f"↻ Synthesis ratio is {ratio:.1f}:1 (target ~4:1). "
            "Consider opening a SYN page soon."
        )
        score_penalties += 1

    # ── Review cadence ─────────────────────────────────────────────────
    days_rev = kpis["days_since_last_rev"]
    rev_count = by_type.get("REV", 0)
    if rev_count == 0:
        recommendations.append(
            "↻ No Review entries yet. REV pages help you see your progress "
            "across learning periods — consider opening REV-001."
        )
        score_penalties += 1
    elif days_rev and days_rev > 30:
        recommendations.append(
            f"↻ Your last Review was {days_rev} days ago. "
            "A monthly review keeps your learning visible and intentional."
        )
        score_penalties += 1

    # ── Open questions ─────────────────────────────────────────────────
    unanswered = kpis["unanswered_questions"]
    oldest_days = kpis["oldest_unanswered_days"]
    if unanswered > 0:
        age_str = f", oldest {oldest_days} days old" if oldest_days else ""
        recommendations.append(
            f"? You have {unanswered} unanswered question(s){age_str}. "
            "Run find_open_questions or search_captures with a ? tag to revisit them."
        )
        if oldest_days and oldest_days > 30:
            score_penalties += 1

    # ── Unused templates ───────────────────────────────────────────────
    unused = kpis["unused_templates"]
    template_desc = {"RC": "Rapid Capture", "SYN": "Synthesis", "REV": "Review", "DC": "Dream Capture"}
    for t in unused:
        recommendations.append(
            f"○ No {template_desc[t]} ({t}) entries yet. "
            + ("Try capturing a dream tomorrow morning." if t == "DC"
               else f"Consider trying a {template_desc[t]} page.")
        )

    # ── Velocity ───────────────────────────────────────────────────────
    vel = kpis["capture_velocity"]
    ins = kpis["insight_velocity"]
    if vel == 0:
        recommendations.append("○ No captures in the last 4 weeks. Upload some photos to keep the knowledge base growing.")
        score_penalties += 2
    elif vel < 1:
        recommendations.append(f"○ Capture rate is low ({vel}/week). Even one page a week compounds over a semester.")

    # ── Score ──────────────────────────────────────────────────────────
    max_score = 10
    score = max(0, max_score - score_penalties * 2)
    score_bar = "█" * score + "░" * (max_score - score)

    # ── Format output ──────────────────────────────────────────────────
    rev_str = (
        f"{days_rev} days ago" if days_rev is not None
        else ("never" if rev_count == 0 else "n/a")
    )
    ratio_str = f"{ratio:.1f}:1" if ratio is not None else f"{rc} RC / 0 SYN"

    lines = [
        "Journal Health\n" + "─" * 40,
        f"  Health score : {score_bar}  {score}/{max_score}",
        "",
        "KPIs (last 4 weeks):",
        f"  Capture velocity  : {vel:.1f} / week",
        f"  Insight velocity  : {ins:.1f} / week",
        f"  Last Review       : {rev_str}",
        f"  Open questions    : {unanswered}",
        f"  Synthesis ratio   : {ratio_str}  (target ~4:1)",
        "",
        "Captures by type:",
    ] + [
        f"  {t}: {by_type.get(t, 0)}"
        for t in ("RC", "SYN", "REV", "DC")
    ]

    if recommendations:
        lines += ["", "Recommendations:"]
        for r in recommendations:
            lines.append(f"  {r}")
    else:
        lines.append("\n✓ Your journal practice looks healthy. Keep it up.")

    return "\n".join(lines)


# ── Tool: list_by_tag ─────────────────────────────────────────────────────────

@mcp.tool()
def list_by_tag(tag: str, prefix: str = "") -> str:
    """
    Browse all captures that carry a specific tag — no text query required.

    Use this to find every note related to a topic, source, question, or insight:
      list_by_tag("machine-learning")           → all captures with that tag
      list_by_tag("machine-learning", prefix="#") → only # topic tags
      list_by_tag("RC-012", prefix="@")          → captures referencing @RC-012
      list_by_tag("deadline", prefix="!")        → priority items
      list_by_tag("attention-mechanism", prefix="?") → that open question

    Args:
        tag:    Tag value to look up (without the prefix character).
        prefix: Optional prefix to narrow the search: #  @  !  ?  $  ->
                Leave blank to match the tag across all prefix types.
    """
    if not tag.strip():
        return "Please provide a tag value to look up."

    with _db() as con:
        results = get_captures_by_tag(con, tag.strip(), prefix=prefix.strip())

    if not results:
        pfx_str = f"{prefix}{tag}" if prefix else tag
        return f"No captures found with tag: {pfx_str!r}"

    pfx_label = f"{prefix}{tag}" if prefix else tag
    lines = [f"Captures tagged {pfx_label!r}  ({len(results)} found):\n"]
    for r in results:
        tag_str = " ".join(f"{t['prefix']}{t['value']}" for t in r.get("tags", [])[:5])
        lines.append(
            f"  [{r['template_id']}] #{r['id']}  {r['created_at'][:10]}\n"
            f"    {r['summary'] or '(no summary)'}\n"
            f"    Tags: {tag_str or 'none'}\n"
        )
    return "\n".join(lines)


# ── Tool: get_breakthroughs ───────────────────────────────────────────────────

@mcp.tool()
def get_breakthroughs() -> str:
    """
    Return all Synthesis (SYN) entries in chronological order — your complete
    breakthrough timeline.

    Shows the breakthrough field, patterns identified, $ insight tags, and
    topic tags for each SYN page. Use this to see how your thinking has evolved
    and which ideas led to the biggest discoveries.
    """
    with _db() as con:
        breakthroughs = get_syn_breakthroughs(con)

    if not breakthroughs:
        return (
            "No Synthesis entries yet.\n"
            "Upload a SYN page photo to start tracking your breakthroughs."
        )

    lines = [f"Breakthrough Timeline — {len(breakthroughs)} SYN entry(s)\n" + "─" * 50]

    for b in breakthroughs:
        date = b["created_at"][:10]
        topics = " ".join(f"#{t}" for t in b["topics"]) or "(no topics)"
        insights = " ".join(f"${i}" for i in b["insights"]) or "(no insights tagged)"

        lines.append(f"\n★ {b['template_id']}  —  {date}")
        lines.append(f"  Topics   : {topics}")
        lines.append(f"  Insights : {insights}")

        if b["breakthrough"]:
            # Show first 200 chars of breakthrough field
            excerpt = b["breakthrough"][:200].replace("\n", " ")
            lines.append(f"  Breakthrough: {excerpt}{'…' if len(b['breakthrough']) > 200 else ''}")

        if b["patterns"]:
            excerpt = b["patterns"][:150].replace("\n", " ")
            lines.append(f"  Patterns: {excerpt}{'…' if len(b['patterns']) > 150 else ''}")

    return "\n".join(lines)


# ── Tool: dream_patterns ──────────────────────────────────────────────────────

@mcp.tool()
def dream_patterns() -> str:
    """
    Analyze recurring patterns across all Dream Capture (DC) entries.

    Aggregates symbols, emotions, and themes from every DC page to surface
    what appears most frequently in your dreams — recurring characters, objects,
    emotional states, and topic clusters. The more DC pages you upload, the
    more meaningful the patterns become.
    """
    with _db() as con:
        dc_entries = get_dc_pattern_data(con)

    if not dc_entries:
        return (
            "No Dream Capture entries yet.\n"
            "Photograph a DC page the next morning after a vivid dream."
        )

    if len(dc_entries) < 3:
        return (
            f"Only {len(dc_entries)} dream entry(s) so far — patterns become clearer with more data.\n"
            "Here's what's been captured:\n\n"
            + "\n".join(
                f"  {d['template_id']} ({d['created_at'][:10]}): {d['summary'] or '(no summary)'}"
                for d in dc_entries
            )
        )

    # Word frequency for symbols and emotions
    import re as _re

    def _word_freq(texts: list[str]) -> dict[str, int]:
        freq: dict[str, int] = {}
        stopwords = {"the", "a", "an", "and", "or", "in", "on", "at", "of",
                     "to", "is", "was", "it", "i", "my", "me", "with", "very"}
        for text in texts:
            for word in _re.findall(r'\b[a-zA-Z]{3,}\b', text.lower()):
                if word not in stopwords:
                    freq[word] = freq.get(word, 0) + 1
        return {k: v for k, v in sorted(freq.items(), key=lambda x: -x[1]) if v > 1}

    symbol_texts  = [d["symbols"]  for d in dc_entries if d["symbols"]]
    emotion_texts = [d["emotions"] for d in dc_entries if d["emotions"]]

    symbol_freq  = _word_freq(symbol_texts)
    emotion_freq = _word_freq(emotion_texts)

    # Aggregate DC-specific tags by prefix
    def _tag_freq_by_prefix(prefix: str) -> dict[str, int]:
        freq: dict[str, int] = {}
        for d in dc_entries:
            for t in d["tags"]:
                if t["prefix"] == prefix:
                    freq[t["value"]] = freq.get(t["value"], 0) + 1
        return {k: v for k, v in sorted(freq.items(), key=lambda x: -x[1]) if v > 1}

    recurring_themes   = _tag_freq_by_prefix("#")   # #theme
    recurring_symbols  = _tag_freq_by_prefix("@")   # @symbol
    recurring_motifs   = _tag_freq_by_prefix("!")   # !recurring
    recurring_sensory  = _tag_freq_by_prefix("*")   # *sensory

    lines = [
        f"Dream Pattern Analysis — {len(dc_entries)} DC entries\n" + "─" * 50,
        f"\nDate range: {dc_entries[0]['created_at'][:10]}  →  {dc_entries[-1]['created_at'][:10]}",
    ]

    if symbol_freq:
        top_symbols = list(symbol_freq.items())[:10]
        lines.append("\nRecurring symbols (from text):")
        lines.append("  " + "  |  ".join(f"{w} ×{c}" for w, c in top_symbols))
    else:
        lines.append("\nRecurring symbols: (none detected yet)")

    if recurring_symbols:
        lines.append("\nTagged symbols (@):")
        lines.append("  " + "  |  ".join(f"@{k} ×{v}" for k, v in list(recurring_symbols.items())[:10]))

    if emotion_freq:
        top_emotions = list(emotion_freq.items())[:10]
        lines.append("\nRecurring emotions (from text):")
        lines.append("  " + "  |  ".join(f"{w} ×{c}" for w, c in top_emotions))
    else:
        lines.append("\nRecurring emotions: (none detected yet)")

    if recurring_motifs:
        lines.append("\nRecurring motifs (!):")
        lines.append("  " + "  |  ".join(f"!{k} ×{v}" for k, v in list(recurring_motifs.items())[:10]))

    if recurring_sensory:
        lines.append("\nSensory details (*):")
        lines.append("  " + "  |  ".join(f"*{k} ×{v}" for k, v in list(recurring_sensory.items())[:10]))

    if recurring_themes:
        lines.append("\nRecurring themes (#):")
        lines.append(
            "  " + "  |  ".join(f"#{k} ×{v}" for k, v in list(recurring_themes.items())[:10])
        )
    else:
        lines.append("\nRecurring themes: (tag more entries to detect themes)")

    lines.append("\nAll entries (chronological):")
    for d in dc_entries:
        lines.append(f"  {d['template_id']}  {d['created_at'][:10]}  —  {d['summary'] or '(no summary)'}")

    return "\n".join(lines)


# ── Tool: knowledge_progress ──────────────────────────────────────────────────

@mcp.tool()
def knowledge_progress(topic: str = "") -> str:
    """
    Track Knowledge Status progression across your Review (REV) entries.

    Shows how topics move through Needs Work → Solid → Mastered over time,
    based on the Knowledge Status field on each REV page.

    Args:
        topic: Optional # topic tag to filter (e.g. "calculus"). Leave blank
               to show all topics across all REV entries.
    """
    with _db() as con:
        entries = get_rev_progress(con, topic_filter=topic.strip())

    if not entries:
        if topic:
            return f"No Review entries found for topic #{topic}."
        return (
            "No Review entries yet.\n"
            "Upload a REV page photo to start tracking your knowledge progression."
        )

    # Group by topic tag, collect status sequence over time
    topic_timelines: dict[str, list[dict]] = {}
    untopiced: list[dict] = []

    for e in entries:
        if e["topics"]:
            for t in e["topics"]:
                topic_timelines.setdefault(t, []).append(e)
        else:
            untopiced.append(e)

    _status_order = {"Needs Work": 0, "Solid": 1, "Mastered": 2, "": -1}
    _status_icon  = {"Needs Work": "○", "Solid": "◑", "Mastered": "●", "": "·"}

    lines = [
        f"Knowledge Progress{f' — #{topic}' if topic else ''}\n" + "─" * 50
    ]

    if topic_timelines:
        for t_name, t_entries in sorted(topic_timelines.items()):
            statuses = [e["knowledge_status"] for e in t_entries]
            latest   = t_entries[-1]["knowledge_status"]
            icon     = _status_icon.get(latest, "·")

            # Show progression arrow
            visible = [s for s in statuses if s]
            progress_str = " → ".join(visible) if visible else "(no status recorded)"

            lines.append(f"\n#{t_name}  {icon} {latest or 'Unknown'}")
            lines.append(f"  Progression: {progress_str}")
            lines.append(f"  Reviews: {len(t_entries)}")
            for e in t_entries:
                st = e["knowledge_status"] or "—"
                lines.append(f"    {e['template_id']}  {e['created_at'][:10]}  [{st}]")

    if untopiced:
        lines.append("\nReviews without topic tags:")
        for e in untopiced:
            st = e["knowledge_status"] or "—"
            lines.append(f"  {e['template_id']}  {e['created_at'][:10]}  [{st}]  {e['summary'] or ''}")

    # Summary
    all_statuses = [e["knowledge_status"] for e in entries if e["knowledge_status"]]
    if all_statuses:
        from collections import Counter
        counts = Counter(all_statuses)
        lines.append(
            f"\nOverall: {counts.get('Mastered',0)} Mastered  "
            f"{counts.get('Solid',0)} Solid  "
            f"{counts.get('Needs Work',0)} Needs Work"
        )

    return "\n".join(lines)


# ── Tool: extract_insights ────────────────────────────────────────────────────

@mcp.tool()
def extract_insights(session_text: str, source_platform: str = "") -> str:
    """
    Prepare an AI research session for insight extraction.

    Loads knowledge base context (existing tags, potentially related entries)
    and returns it alongside the session text and extraction instructions.
    Claude then performs the extraction in its response to the user.

    This tool does NOT write to the database. After the user reviews the
    extracted insights, call commit_aiex() to store confirmed entries.

    Trigger phrases: "Extract insights from this session", "Run AIEX on this
    conversation", "Generate insight extraction report".

    Args:
        session_text:    Full or partial transcript of the research session.
        source_platform: Platform where session occurred (e.g. "Claude Desktop",
                         "Claude Mobile"). Leave blank if unknown.

    Returns a context block + extraction instructions for Claude to process.
    """
    if not session_text.strip():
        return "Please provide session_text to extract insights from."

    with _db() as con:
        stats = db_get_stats(con)

        # Top tags for context
        top_tags = "  ".join(
            f"{r['tag']}" for r in stats["top_tags"][:15]
        )

        # Search for related entries using the first few words of the session
        related = []
        try:
            words = [w for w in session_text[:300].split() if len(w) > 4][:5]
            if words:
                related = search_fts(con, query=" ".join(words[:3]), limit=5)
        except Exception:
            pass

    total    = stats["total_captures"]
    by_type  = stats["by_type"]
    type_str = "  ".join(
        f"{t}: {by_type.get(t, 0)}"
        for t in ("RC", "SYN", "REV", "DC", "AIEX")
    )
    platform_line = f"**Platform:** {source_platform}" if source_platform else "**Platform:** (unspecified)"

    related_block = ""
    if related:
        rel_lines = []
        for r in related:
            tag_str = " ".join(f"{t['prefix']}{t['value']}" for t in r.get("tags", [])[:4])
            rel_lines.append(
                f"  - {r['template_id']} — \"{r['summary'][:80] or '(no summary)'}\""
                + (f"  |  {tag_str}" if tag_str else "")
            )
        related_block = "\n### Potentially related existing entries:\n" + "\n".join(rel_lines)

    tag_block = (f"\n### Active knowledge base tags:\n  {top_tags}") if top_tags else ""

    session_body = session_text[:8000]
    truncated    = (
        "\n*(session truncated to 8000 characters — paste earlier or key passages if needed)*"
        if len(session_text) > 8000 else ""
    )

    today = datetime.now(timezone.utc).date().isoformat()

    return f"""## KSJ — AI Insight Extraction
{platform_line}
**Knowledge base:** {total} capture(s)  ({type_str})
{related_block}{tag_block}

---

### Session to Process:

{session_body}{truncated}

---

### Extraction Instructions

Extract all high-value insights from the session above. For each insight:

1. Write a concise statement (1–3 sentences capturing the core idea)
2. Assign confidence tier using Cirlot's color symbolism:
   - 🟢 **Seed** — organic potential, not yet in motion (interesting direction, needs development)
   - 🔴 **Developing** — active energy, transformation underway (substantive, worth pursuing soon)
   - 🟡 **Strong** — solar illumination, highest realization (specific, novel, act now)
3. Add `#topic` tags (and `@source`, `?question`, `$insight` where applicable)
4. Note connections to existing entries listed above

Also extract:
- **Open questions** worth pursuing
- **Action items** (include priority `!` for urgent items)

Present the extraction as a structured review for user approval, then call \
`commit_aiex()` with the confirmed JSON:

```json
{{
  "entry_type": "AIEX-001",
  "date": "{today}",
  "source_platform": "{source_platform}",
  "session_focus": "<session topic in 5–10 words>",
  "insights": [
    {{
      "text": "<insight text>",
      "confidence_tier": "Seed | Developing | Strong",
      "tags": ["#topic1", "#topic2"],
      "connections": ["<connection to existing entry if applicable>"]
    }}
  ],
  "open_questions": ["<question 1>"],
  "action_items": [{{"text": "<action>", "priority": "!", "status": "open"}}]
}}
```

No database writes occur until `commit_aiex()` is called with confirmed data."""


# ── Tool: commit_aiex ─────────────────────────────────────────────────────────

@mcp.tool()
def commit_aiex(session_json: str) -> str:
    """
    Write confirmed AIEX insights to the knowledge base.

    Takes a JSON string conforming to the AIEX-001 schema and stores each
    confirmed insight as a separate AIEX-NNN entry. IDs are assigned
    sequentially at write time. All entries are tagged as AI-Extracted.

    Call this after the user has reviewed and approved the output from
    extract_insights().

    Args:
        session_json: JSON string with fields: entry_type, date,
                      source_platform, session_focus, insights (list),
                      open_questions (list), action_items (list).
                      Each insight must have: text, confidence_tier, tags,
                      connections.

    Returns a confirmation listing every AIEX ID assigned.
    """
    try:
        data = json.loads(session_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}\n\nMake sure to pass a valid JSON string."

    insights_raw = data.get("insights", [])
    if not insights_raw:
        return "No insights found in session_json. Provide at least one insight with a 'text' field."

    date            = data.get("date", datetime.now(timezone.utc).date().isoformat())
    source_platform = data.get("source_platform", "")
    session_focus   = data.get("session_focus", "")
    open_questions  = data.get("open_questions", [])
    action_items    = data.get("action_items", [])

    valid_tiers = {"Seed", "Developing", "Strong"}

    stored: list[dict] = []

    with _db() as con:
        for insight_data in insights_raw:
            text = (insight_data.get("text") or "").strip()
            if not text:
                continue

            confidence_tier = insight_data.get("confidence_tier", "Seed")
            if confidence_tier not in valid_tiers:
                confidence_tier = "Seed"

            connections_text = insight_data.get("connections", [])
            tag_strings      = insight_data.get("tags", [])

            content = {
                "insight":          text,
                "confidence_tier":  confidence_tier,
                "session_focus":    session_focus,
                "source_platform":  source_platform,
                "date":             date,
                "connections":      connections_text,
                "open_questions":   open_questions,
                "action_items":     action_items,
            }

            # Parse tags from list format: ["#topic", "@source", "bare-word"]
            tags: list[dict] = []
            seen: set[tuple[str, str]] = set()

            for tag_str in tag_strings:
                tag_str = tag_str.strip()
                if len(tag_str) < 2:
                    continue
                if tag_str[0] in ('#', '@', '!', '?', '$', '*'):
                    prefix = tag_str[0]
                    value  = tag_str[1:].lower()
                else:
                    prefix = '#'
                    value  = tag_str.lower()
                key = (prefix, value)
                if key not in seen:
                    seen.add(key)
                    tags.append({"prefix": prefix, "value": value})

            # Also extract inline schema tags from the insight text itself
            from .templates import extract_schema_tags
            for t in extract_schema_tags(text):
                key = (t["prefix"], t["value"])
                if key not in seen:
                    seen.add(key)
                    tags.append(t)

            aiex_id    = get_next_aiex_id(con)
            capture_id = insert_capture(
                con,
                type_="AIEX",
                template_id=aiex_id,
                content=content,
                raw_ocr=text,
                summary=text[:200],
                confidence=1.0,
                image_path="",
            )
            insert_tags(con, capture_id, tags)
            con.commit()

            connections = build_connections(con, capture_id)

            stored.append({
                "aiex_id":     aiex_id,
                "capture_id":  capture_id,
                "text":        text,
                "tier":        confidence_tier,
                "tags":        tags,
                "connections": connections,
            })

    if not stored:
        return "No insights were committed. Ensure each insight has a non-empty 'text' field."

    _tier_emoji = {"Seed": "🟢", "Developing": "🔴", "Strong": "🟡"}

    lines = [f"AIEX Commit — {len(stored)} insight(s) stored\n{'─' * 40}"]
    for s in stored:
        tag_str  = " ".join(f"{t['prefix']}{t['value']}" for t in s["tags"][:5])
        conn_str = f"\n    ★ {len(s['connections'])} connection(s) detected" if s["connections"] else ""
        emoji    = _tier_emoji.get(s["tier"], "")
        preview  = s["text"][:80] + ("…" if len(s["text"]) > 80 else "")
        lines.append(
            f"\n  {s['aiex_id']} (#{s['capture_id']})  {emoji} {s['tier']}\n"
            f"    {preview}\n"
            f"    Tags: {tag_str or 'none'}{conn_str}"
        )

    if open_questions:
        lines.append(f"\n\nOpen questions ({len(open_questions)}):")
        for q in open_questions:
            lines.append(f"  ? {q}")

    if action_items:
        lines.append(f"\nAction items ({len(action_items)}):")
        for item in action_items:
            if isinstance(item, dict):
                p = "!" if item.get("priority") == "!" else " "
                lines.append(f"  {p} {item.get('text', str(item))}")
            else:
                lines.append(f"    {item}")

    lines.append(f"\nAll entries stored as type=AIEX (AI-Extracted flag).")
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
