"""
KSJ MCP Server — FastMCP entry point.

29 tools:
  export_html        — Self-contained HTML view: timeline, index, connections, graph
  assert_connection  — Assert supersedes/refutes/narrows/supports between captures
  find_path          — Shortest connection chain between two captures
  neighborhood       — Everything within N hops of a capture
  lint               — Health check: orphans, stale claims, contradictions, old questions
  export_backup      — Full base to versioned JSONL (ksj-export-v1)
  import_backup      — Restore a JSONL backup (additive, non-destructive)
  manual_capture     — Store a capture from assistant-transcribed text (primary path)
  upload_capture     — OCR a journal photo locally (Tesseract) and store it
  correct_ocr        — Replace a stored capture's transcription; re-parse and reconnect
  identify_capture   — Assign/fix the template ID of a stored (or unidentified) capture
  set_volume         — Configure which journal volume is written to / searched
  assert_entity      — Manually link a named entity (person, place, symbol) to a capture
  rebuild_connections— Re-derive the whole connection graph from current tags and text
  bulk_upload        — Process a whole folder of photos at once
  search_captures    — Full-text search with optional filters
  list_by_tag        — Browse all captures with a given tag or prefix
  find_connections   — Tag overlap + @-reference connections for a capture
  get_stats          — Summary counts, top tags, open questions
  export_captures    — Dump captures as Markdown or JSON
  suggest_synthesis  — Find RC clusters ready for a SYN entry
  surface_connections— Independent scan of a SYN page's RC cluster + comparison dialogue (Phase 1/2; no DB write yet)
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .database import (
    check_duplicate,
    get_active_volumes,
    get_capture,
    get_capture_by_template,
    get_captures_by_tag,
    get_captures_for_entity,
    get_connections,
    get_current_volume,
    get_entities_for_capture,
    export_jsonl,
    import_jsonl,
    link_capture_entity,
    migrate_v3,
    migrate_v31,
    set_setting,
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
    insert_connection,
    insert_tags,
    list_captures,
    migrate_add_aiex,
    migrate_add_corrected_ocr,
    migrate_fix_fk_references,
    search_fts,
    update_capture_correction,
    get_connection,
)
from .connections import (
    build_connections,
    find_path as graph_find_path,
    find_unapplied,
    neighborhood as graph_neighborhood,
    rebuild_connections as db_rebuild_connections,
    run_lint,
)
from .htmlview import collect_view_data, render_html
from .ocr import (
    CloudOcrConfigError,
    OcrNotAvailableError,
    active_backend,
    detect_template_type,
    extract_text,
    parse_template_id,
)
from .templates import assign_role, extract_schema_tags, normalize_tag_value, parse_template

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
- `@source` — origin of information (a template ID like @RC-012 is a
  reference; any other @-value is a named entity: @Veronica, @UCLA)
- `!priority` — urgent or important
- `?question` — open questions
- `$insight` — breakthrough realization
- `A→B` — cause/effect or connection

DC (Dream Capture) pages use a dream-specific variant:
- `#theme` — dream theme or subject
- `@symbol` — recurring symbol or character (these are entities too)
- `!recurring` — recurring dream motif
- `*sensory` — sensory detail (unique to DC)

The server stores each tag's semantic ROLE alongside the character as
written (topic vs theme, priority vs motif, reference vs entity), so query
tools can disambiguate — use the role parameter on list_by_tag when it
matters. Content written inside tag bubbles counts as a tag even without
the prefix character.

## Volumes
Each physical journal is a volume; volume 2 continues volume 1's knowledge
base and cross-volume connections are expected. When a user starts a new
journal, call set_volume(current_volume=N) once. If an upload reports a
template-ID collision, ask whether this is a new journal (new volume) or a
re-capture of the same page (force=True).

## What You Can Do
- Search and retrieve entries by tag, template, or concept
- Identify patterns across entries over time
- Generate study decks from $insight and key content (platform-agnostic CSV)
- Analyze dream patterns across DC entries
- Track knowledge status progression from REV entries
- Surface breakthrough connections across RC and SYN entries

## Input Method
PRIMARY PATH — assistant vision: when the user shares a photo of a journal
page, read it yourself, show the transcription for confirmation, then store
it with manual_capture(). Your vision is far more accurate on handwriting
than local OCR. Keep field labels as written, capture every schema tag, and
treat content inside tag bubbles as tags even when the prefix character is
missing.

FALLBACK — upload_capture()/bulk_upload() run local Tesseract OCR on an
image file path. Best for printed or very neat text, or when the user
prefers fully local processing. If a stored capture's text came out wrong,
fix it with correct_ocr() — the original OCR text is always preserved.

Prioritize accuracy over speed when reading handwritten content.
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

# Migrations run BEFORE init_db: on an old database they rebuild it to the
# current schema; on a fresh install they are no-ops and init_db creates the
# current schema directly. (init_db's CREATE INDEX statements assume current
# columns, so it must not run first against an old schema.)
migrate_add_aiex(_DB_PATH)
migrate_fix_fk_references(_DB_PATH)
migrate_add_corrected_ocr(_DB_PATH)
_v3_migrated = migrate_v3(_DB_PATH)
migrate_v31(_DB_PATH)
init_db(_DB_PATH)
_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

if _v3_migrated:
    # §1.13 rule 8: edge semantics changed (IDF strengths, typed dedup), so
    # the graph is re-derived from current tags and text after migration.
    _con = get_connection(_DB_PATH)
    try:
        db_rebuild_connections(_con)
    finally:
        _con.close()

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp"}


def _db():
    return get_connection(_DB_PATH)


def _cloud_ocr_notice() -> str:
    """
    Consent line prepended to OCR-tool output whenever a cloud backend is
    active — the user must always see, in the tool result itself, that their
    images are leaving the machine and where they are going.
    """
    backend = active_backend()
    if backend == "tesseract":
        return ""
    return (
        f"⚠ Cloud OCR is ON (KSJ_OCR_BACKEND={backend}): each image is sent to "
        f"your own {backend} endpoint for text extraction. Unset KSJ_OCR_BACKEND "
        f"for fully local processing.\n\n"
    )


def _read_scope(con) -> tuple[list[int] | None, str]:
    """
    Active read scope: (volumes, note). volumes is None when all volumes are
    visible; the note is appended to tool output whenever a filter is active —
    a tool whose purpose is catching forgotten prior work must never silently
    hide prior volumes (§1.5).
    """
    vols = get_active_volumes(con)
    if vols is None:
        return None, ""
    return vols, (
        f"\n\nScope: volume(s) {', '.join(map(str, vols))} — other volumes are "
        f"excluded. Use set_volume(active_volumes='*') to search everything."
    )


# ── Shared upload helper ──────────────────────────────────────────────────────

def _process_image(image_path: str, force: bool = False, volume: int = 0) -> dict:
    """
    Core upload pipeline: OCR → identify (tiered) → volume-aware duplicate
    check → parse → store → copy image → detect connections → highlight.

    OCR always runs and the page is ALWAYS stored — identification failure
    stores the page as UNIDENTIFIED rather than discarding the photo, since
    the photo (that page, in that light, at that moment) is the expensive
    irreversible part and the six-character ID is the cheap recoverable one.

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
        "volume":       int | None,
        "unidentified": bool,
      }
    """
    result = {
        "ok": False, "error": None, "capture_id": None,
        "template_id": "", "summary": "", "tags": [],
        "confidence": 0.0, "connections": [], "highlight": None,
        "duplicate": None, "stored_image": "",
        "volume": None, "unidentified": False, "_id_note": "",
    }

    # OCR
    try:
        ocr_result = extract_text(image_path)
    except OcrNotAvailableError as e:
        result["error"] = f"OCR Error:\n\n{e}"
        return result
    except CloudOcrConfigError as e:
        result["error"] = f"Cloud OCR configuration error:\n\n{e}"
        return result
    except FileNotFoundError:
        result["error"] = f"File not found: {image_path}"
        return result
    except Exception as e:
        result["error"] = f"Unexpected OCR error: {e}"
        return result

    raw_text      = ocr_result["raw_text"]
    template_type = ocr_result["template_type"]
    template_id   = ocr_result["template_id"] or None
    page_suffix   = ocr_result.get("page_suffix")
    page_volume   = ocr_result.get("volume")
    id_conf       = ocr_result.get("id_confidence", 1.0 if template_id else 0.0)
    confidence    = ocr_result["confidence"]

    # Low-confidence warning (non-fatal)
    low_conf_warning = ""
    if confidence < 0.6:
        low_conf_warning = (
            f"\n  ⚠ Low OCR confidence ({confidence:.0%}) — consider retaking with better lighting "
            "or holding the camera more parallel to the page."
        )

    result["template_id"] = template_id or ""
    result["confidence"]  = confidence

    with _db() as con:
        # Volume resolution (§1.4): written on the page > per-upload
        # parameter > stored default.
        vol = page_volume or volume or get_current_volume(con)
        result["volume"] = vol

        if template_type == "UNKNOWN":
            # §1.2: identification failed, but the page is stored anyway.
            result["unidentified"] = True
        else:
            # Duplicate detection is per volume: a second journal
            # legitimately starts over at RC-001.
            existing = check_duplicate(con, template_id, volume=vol)
            if existing and not force:
                result["duplicate"] = existing
                any_vol = check_duplicate(con, template_id)
                result["error"] = (
                    f"{template_id} already exists in volume {existing['volume']} "
                    f"(stored {existing['created_at'][:10]}, #{existing['id']}).\n"
                    f"  Summary: {existing['summary'] or '(none)'}\n\n"
                    f"Is this a page from a NEW journal? Upload again with "
                    f"volume={existing['volume'] + 1}, or run "
                    f"set_volume(current_volume={existing['volume'] + 1}) once when "
                    f"starting a new book.\n"
                    f"Re-uploading the SAME page (e.g. a cleaner photo)? Use force=True to replace it."
                )
                return result
            if existing and force:
                con.execute("DELETE FROM captures WHERE id=?", (existing["id"],))
                con.commit()

        # Parse template
        parsed  = parse_template(template_type, raw_text)
        summary = parsed["summary"]
        tags    = parsed["tags"]

        result["summary"] = summary
        result["tags"]    = tags

        # Copy image to data/images/ for self-containment
        src = Path(image_path)
        ts  = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        dest = _IMAGES_DIR / f"{template_id or 'unidentified'}_{ts}{src.suffix.lower()}"
        try:
            shutil.copy2(src, dest)
            stored_image = str(dest)
        except Exception:
            stored_image = image_path  # fall back to original path

        result["stored_image"] = stored_image

        # Store capture  (unapplied check runs after storing — see below)
        capture_id = insert_capture(
            con,
            type_=template_type,
            template_id=template_id,
            content=parsed["fields"],
            raw_ocr=raw_text,
            summary=summary,
            confidence=confidence,
            image_path=stored_image,
            volume=vol,
            page_suffix=page_suffix,
        )
        insert_tags(con, capture_id, tags)
        con.commit()

        if result["unidentified"]:
            result["_id_note"] = (
                f"\n  ⚠ No template ID detected — stored as UNIDENTIFIED (#{capture_id}).\n"
                f"    The text and tags are safe. Fix the ID any time with "
                f"identify_capture({capture_id}, \"RC-001\"), or re-run the text "
                f"with correct_ocr({capture_id}, ...) from a cleaner read."
            )
        elif id_conf < 1.0:
            result["_id_note"] = (
                f"\n  ⚠ Template ID read loosely as {template_id} — if that's wrong, "
                f"call identify_capture({capture_id}, \"<correct-id>\")."
            )

        # Detect connections
        connections = build_connections(con, capture_id)

        # §2.2a: surface prior uncited findings at the moment of writing
        result["_unapplied"] = find_unapplied(con, capture_id)

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
    template_label = r["template_id"] or "UNIDENTIFIED"
    if r.get("template_id") and r.get("page_suffix"):
        template_label += r["page_suffix"]
    lines = [
        f"Stored capture #{r['capture_id']}",
        f"  Template : {template_label}",
        f"  Volume   : {r.get('volume') or 1}",
        f"  Summary  : {r['summary'] or '(empty)'}",
        f"  Tags     : {tag_list}",
        f"  OCR conf : {r['confidence']:.0%}",
    ]

    if r.get("_low_conf"):
        lines.append(r["_low_conf"])
    if r.get("_id_note"):
        lines.append(r["_id_note"])

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

    # §2.2a unapplied check: earlier findings on the same rare topic or
    # entity that nothing has ever cited — surfaced at the moment of writing
    unapplied = r.get("_unapplied") or []
    if unapplied:
        lines.append("\n  ⚑ Earlier findings on this topic that nothing has cited yet:")
        for u in unapplied:
            label = u["template_id"] or f"#{u['id']}"
            shared = ", ".join(u["shared"][:4])
            summary = (u["summary"] or "(no summary)")[:70]
            lines.append(
                f"    {label} ({u['created_at'][:10]}) — shared: {shared}\n"
                f"      \"{summary}\""
            )
        lines.append(
            "    If one applies to this page, link it: assert_connection() or "
            "add @<ID> via correct_ocr()."
        )

    return "\n".join(lines)


# ── Tool: upload_capture ──────────────────────────────────────────────────────

@mcp.tool()
def upload_capture(image_path: str, force: bool = False, volume: int = 0) -> str:
    """
    Process a journal page photo: run OCR, parse the template, extract schema
    tags, store the capture, copy the image to the knowledge base, and detect
    connections to existing captures.

    The page is always stored, even when no template ID can be read — it is
    kept as UNIDENTIFIED and can be fixed later with identify_capture().

    Args:
        image_path: Absolute path to the image file (JPG, PNG, TIFF, etc.)
        force:      Set to True to overwrite an existing capture with the same
                    template ID in the same volume (default False — warns instead).
        volume:     Which journal/book this page belongs to. 0 = automatic:
                    a volume written on the page (e.g. "V2 RC-001") wins,
                    otherwise the stored current_volume setting (default 1).

    Returns a summary of what was found and stored, including the strongest
    connection detected.
    """
    result = _process_image(image_path, force=force, volume=volume)
    return _cloud_ocr_notice() + _format_upload_result(result, image_path)


# ── Tool: manual_capture ──────────────────────────────────────────────────────

@mcp.tool()
def manual_capture(text: str, template_id: str = "", force: bool = False, volume: int = 0) -> str:
    """
    Store a journal capture from transcribed text. This is the PRIMARY path
    for handwritten pages: the user shares a photo of the page, YOU (the
    assistant) read it with vision, confirm the transcription with the user,
    then call this tool with the text. Assistant vision is far more accurate
    on handwriting than local OCR — prefer this over upload_capture whenever
    the user can show you the page.

    Transcribe faithfully: keep field labels (First Impressions, Key Points,
    Tags, etc.) as written, include every schema tag (#topic @source !priority
    ?question $insight *sensory), and treat content inside tag bubbles as tags.

    Args:
        text:        The transcribed content of the journal page (all fields
                     you can read — First Impressions, Key Points, Tags, etc.).
        template_id: Template ID (e.g. "RC-001"). If omitted, the server will
                     try to detect it from the text automatically.
        force:       Set to True to overwrite an existing capture with the
                     same template ID in the same volume (default False — warns).
        volume:      Which journal/book this page belongs to. 0 = automatic:
                     a volume written on the page/text wins, otherwise the
                     stored current_volume setting (default 1).

    Returns the same summary as upload_capture, including any connections
    detected to existing captures.
    """
    # Detect template ID from the explicit parameter or from the text
    parsed_id = parse_template_id(template_id) if template_id else parse_template_id(text)
    if parsed_id["template_type"] == "UNKNOWN":
        if template_id:
            return (
                f"Could not parse template ID '{template_id}'. "
                "Expected format: RC-001, SYN-003, REV-002, DC-005, etc."
            )
        return (
            "Could not detect a template ID (RC-XXX / SYN-XXX / REV-XXX / DC-XXX) "
            "in the provided text. Please pass template_id explicitly, "
            "e.g. template_id=\"RC-001\"."
        )
    template_type = parsed_id["template_type"]
    tid           = parsed_id["template_id"]
    page_suffix   = parsed_id["page_suffix"]

    result = {
        "ok": False, "error": None, "capture_id": None,
        "template_id": tid, "summary": "", "tags": [],
        "confidence": 1.0, "connections": [], "highlight": None,
        "duplicate": None, "stored_image": "",
        "_low_conf": "", "_id_note": "", "volume": None,
        "unidentified": False, "page_suffix": page_suffix,
    }

    with _db() as con:
        vol = parsed_id["volume"] or volume or get_current_volume(con)
        result["volume"] = vol

        existing = check_duplicate(con, tid, volume=vol)
        if existing and not force:
            result["duplicate"] = existing
            result["error"] = (
                f"{tid} already exists in volume {existing['volume']} "
                f"(stored {existing['created_at'][:10]}, #{existing['id']}).\n"
                f"  Summary: {existing['summary'] or '(none)'}\n\n"
                f"Is this a page from a NEW journal? Call manual_capture again with "
                f"volume={existing['volume'] + 1}, or run "
                f"set_volume(current_volume={existing['volume'] + 1}) once when "
                f"starting a new book.\n"
                f"Re-capturing the SAME page? Use force=True to replace it."
            )
            return _format_upload_result(result, "")
        if existing and force:
            con.execute("DELETE FROM captures WHERE id=?", (existing["id"],))
            con.commit()

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
            volume=vol,
            page_suffix=page_suffix,
        )
        insert_tags(con, capture_id, tags)
        con.commit()

        connections = build_connections(con, capture_id)
        result["_unapplied"] = find_unapplied(con, capture_id)

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


# ── Tool: correct_ocr ─────────────────────────────────────────────────────────

@mcp.tool()
def correct_ocr(capture_id: int, text: str) -> str:
    """
    Replace a stored capture's transcription with corrected text.

    Use this when a capture's OCR came out wrong — for example a Tesseract
    read of handwriting, or a transcription typo. The original OCR text is
    preserved in the database; the corrected text becomes what search, tag
    extraction, and connection detection use from now on.

    Re-runs template parsing, tag extraction, and connection detection on the
    corrected text.

    Args:
        capture_id: The numeric capture ID (shown as #N in upload output and
                    search results). Not the template ID.
        text:       The full corrected transcription of the page, including
                    field labels and all schema tags.

    Returns a summary of the re-parsed capture and rebuilt connections.
    """
    if not text.strip():
        return "Please provide the corrected text."

    with _db() as con:
        cap = get_capture(con, capture_id)
        if cap is None:
            return (
                f"No capture with id #{capture_id}. "
                "Use the numeric ID shown in upload output or search results."
            )

        parsed  = parse_template(cap["type"], text)
        summary = parsed["summary"]
        tags    = parsed["tags"]

        update_capture_correction(
            con, capture_id,
            corrected_text=text,
            content=parsed["fields"],
            summary=summary,
            tags=tags,
        )
        connections = build_connections(con, capture_id)

    tag_list = ", ".join(f"{t['prefix']}{t['value']}" for t in tags) or "none"
    lines = [
        f"Corrected capture #{capture_id} ({cap['template_id']})",
        f"  Summary : {summary or '(empty)'}",
        f"  Tags    : {tag_list}",
        f"  Original OCR text preserved; search and connections now use the correction.",
    ]
    if not connections:
        lines.append("  No connections to existing captures after rebuild.")
    else:
        lines.append(f"  {len(connections)} connection(s) after rebuild:")
        for c in connections[:10]:
            shared = f" (shared: {', '.join(c['shared_tags'])})" if c.get("shared_tags") else ""
            lines.append(f"    → {c['connected_template']} [{c['method']}]{shared}")
        if len(connections) > 10:
            lines.append(f"    … and {len(connections) - 10} more")
    return "\n".join(lines)


# ── Tool: identify_capture ────────────────────────────────────────────────────

@mcp.tool()
def identify_capture(capture_id: int, template_id: str, volume: int = 0) -> str:
    """
    Assign or fix the template ID of a stored capture.

    Use this for pages stored as UNIDENTIFIED (no readable template ID at
    upload time) or when the ID was misread. Re-parses the stored text with
    the correct template's parser and rebuilds tags and connections.

    Args:
        capture_id:  The numeric capture ID (#N in upload output).
        template_id: The correct template ID, e.g. "RC-007" or "DC-003".
        volume:      Optionally move the capture to this volume (0 = keep).
    """
    parsed_id = parse_template_id(template_id)
    if parsed_id["template_type"] == "UNKNOWN":
        return (
            f"Could not parse template ID '{template_id}'. "
            "Expected format: RC-001, SYN-003, REV-002, DC-005, etc."
        )
    ttype, tid, suffix = (
        parsed_id["template_type"], parsed_id["template_id"], parsed_id["page_suffix"]
    )

    with _db() as con:
        cap = get_capture(con, capture_id)
        if cap is None:
            return f"No capture with id #{capture_id}."

        vol = volume or cap.get("volume", 1)
        existing = check_duplicate(con, tid, volume=vol)
        if existing and existing["id"] != capture_id:
            return (
                f"{tid} already exists in volume {vol} (#{existing['id']}, "
                f"stored {existing['created_at'][:10]}). If this capture belongs "
                f"to a different journal, pass volume=N."
            )

        text    = cap.get("corrected_ocr") or cap["raw_ocr"]
        parsed  = parse_template(ttype, text)
        con.execute(
            """UPDATE captures SET type=?, template_id=?, page_suffix=?, volume=?,
                                   content_json=?, summary=? WHERE id=?""",
            (ttype, tid, suffix, vol, json.dumps(parsed["fields"]),
             parsed["summary"], capture_id),
        )
        con.execute("DELETE FROM tags WHERE capture_id=?", (capture_id,))
        insert_tags(con, capture_id, parsed["tags"])
        con.execute(
            "DELETE FROM connections WHERE type='tag_overlap' AND (source_id=? OR target_id=?)",
            (capture_id, capture_id),
        )
        con.execute(
            "DELETE FROM connections WHERE type='reference' AND source_id=?",
            (capture_id,),
        )
        con.commit()
        connections = build_connections(con, capture_id)

    tag_list = ", ".join(f"{t['prefix']}{t['value']}" for t in parsed["tags"]) or "none"
    label = tid + (suffix or "")
    return (
        f"Capture #{capture_id} identified as {label} (volume {vol}).\n"
        f"  Summary : {parsed['summary'] or '(empty)'}\n"
        f"  Tags    : {tag_list}\n"
        f"  {len(connections)} connection(s) after re-parse.\n"
        f"Tip: run rebuild_connections() to pick up any @{tid} references "
        f"written on other pages before this one was identified."
    )


# ── Tool: set_volume ──────────────────────────────────────────────────────────

@mcp.tool()
def set_volume(current_volume: int = 0, active_volumes: str = "") -> str:
    """
    Configure journal volumes: which book new captures go into, and which
    books are visible to search and browsing.

    Volumes model multiple physical journals: volume 2 continues volume 1's
    knowledge base (cross-volume connections are normal and expected). Set
    current_volume once when starting a new book.

    Args:
        current_volume: Volume for NEW captures (0 = leave unchanged).
        active_volumes: Read scope for search/list/connections tools:
                        "*" for all volumes, or a comma list like "2,3".
                        Empty = leave unchanged. journal_health and
                        knowledge_progress always see all volumes —
                        longitudinal KPIs over a truncated history would
                        mislead.

    Call with no arguments to just see the current settings.
    """
    with _db() as con:
        if current_volume > 0:
            set_setting(con, "current_volume", str(current_volume))
        if active_volumes.strip():
            raw = active_volumes.strip()
            if raw != "*":
                try:
                    vols = sorted({int(v) for v in raw.split(",") if v.strip()})
                except ValueError:
                    return f"Could not parse active_volumes {active_volumes!r} — use '*' or e.g. '1,2'."
                if not vols:
                    return "active_volumes cannot be empty — use '*' for all volumes."
                raw = ",".join(str(v) for v in vols)
            set_setting(con, "active_volumes", raw)

        cur  = get_current_volume(con)
        vols = get_active_volumes(con)
        counts = con.execute(
            "SELECT volume, COUNT(*) AS cnt FROM captures GROUP BY volume ORDER BY volume"
        ).fetchall()

    scope = "all volumes" if vols is None else f"volume(s) {', '.join(map(str, vols))}"
    lines = [
        "Volume settings",
        f"  New captures go to : volume {cur}",
        f"  Read scope         : {scope}",
        "  Captures per volume:",
    ]
    if counts:
        lines += [f"    volume {r['volume']}: {r['cnt']}" for r in counts]
    else:
        lines.append("    (no captures yet)")
    return "\n".join(lines)


# ── Tool: assert_entity ───────────────────────────────────────────────────────

@mcp.tool()
def assert_entity(capture_id: int, name: str, kind: str = "other") -> str:
    """
    Manually link a named entity (person, place, work, organization, dream
    symbol...) to a capture.

    Entities are first-class objects: '@' tags that aren't template IDs
    create them automatically (e.g. @Veronica, @the-old-house), and this
    tool covers what extraction missed or mis-typed. Entities are global
    across volumes — a character in book 1 and book 3 is one entity.

    Args:
        capture_id: The capture the entity appears in.
        name:       The entity's name as written (display form).
        kind:       person | place | work | org | symbol | other
    """
    kind = kind.strip().lower() or "other"
    valid_kinds = {"person", "place", "work", "org", "symbol", "other"}
    if kind not in valid_kinds:
        return f"Unknown kind {kind!r} — use one of: {', '.join(sorted(valid_kinds))}."
    if not name.strip():
        return "Please provide the entity name."

    with _db() as con:
        cap = get_capture(con, capture_id)
        if cap is None:
            return f"No capture with id #{capture_id}."
        link_capture_entity(con, capture_id, name=name.strip(), kind=kind, source="asserted")
        con.commit()
        appearances = get_captures_for_entity(con, name.strip())

    lines = [
        f"Entity '{name.strip()}' ({kind}) linked to "
        f"{cap['template_id'] or 'UNIDENTIFIED'} (#{capture_id}).",
    ]
    if len(appearances) > 1:
        lines.append(f"\nThis entity appears in {len(appearances)} capture(s):")
        for a in appearances[:10]:
            lines.append(
                f"  [{a['template_id'] or 'UNIDENTIFIED'}] vol {a['volume']}  "
                f"{a['created_at'][:10]}  {a['summary'][:60] or '(no summary)'}"
            )
    return "\n".join(lines)


# ── Tool: rebuild_connections ─────────────────────────────────────────────────

@mcp.tool()
def rebuild_connections() -> str:
    """
    Re-derive the entire connection graph from current tags and text.

    Run this after correcting OCR text, identifying previously-unidentified
    pages, or uploading pages out of order — a page that referenced @RC-015
    before RC-015 existed gets its edge on rebuild. Idempotent: running it
    twice produces the same graph.
    """
    with _db() as con:
        stats = db_rebuild_connections(con)
    return (
        f"Connection graph rebuilt.\n"
        f"  Captures processed : {stats['captures']}\n"
        f"  Edges              : {stats['edges']} "
        f"({stats['references']} reference(s), "
        f"{stats['edges'] - stats['references']} overlap)"
    )


# ── Tool: assert_connection ───────────────────────────────────────────────────

@mcp.tool()
def assert_connection(source_id: int, target_id: int, relation: str, note: str = "") -> str:
    """
    Assert a typed, directional relationship between two captures by hand.

    Relations (source → target):
      supersedes — source replaces target. Target is closed out (kept in
                   history, hidden from current-slice search — never deleted).
      refutes    — source contradicts target.
      narrows    — source restricts target's claim without overturning it.
      supports   — source is evidence for target.

    Automatic contradiction detection is deliberately not offered — it
    cannot be done reliably. Supersession is either asserted here by a
    human or not recorded. Re-asserting between the same pair replaces the
    previous assertion.

    Args:
        source_id: The newer / asserting capture (numeric ID).
        target_id: The capture being superseded / refuted / supported.
        relation:  supersedes | refutes | narrows | supports
        note:      Optional one-line reason, stored on the edge.
    """
    relation = relation.strip().lower()
    valid = {"supersedes", "refutes", "narrows", "supports"}
    if relation not in valid:
        return f"Unknown relation {relation!r} — use one of: {', '.join(sorted(valid))}."
    if source_id == target_id:
        return "A capture cannot relate to itself."

    with _db() as con:
        src = get_capture(con, source_id)
        tgt = get_capture(con, target_id)
        if src is None:
            return f"No capture with id #{source_id}."
        if tgt is None:
            return f"No capture with id #{target_id}."

        insert_connection(
            con, source_id, target_id, "asserted", 1.0, "asserted",
            relation=relation, note=note.strip() or None, asserted_by="user",
        )

        closed_note = ""
        if relation == "supersedes" and not tgt.get("valid_until"):
            now = datetime.now(timezone.utc).isoformat()
            con.execute("UPDATE captures SET valid_until=? WHERE id=?", (now, target_id))
            closed_note = (
                f"\n{tgt['template_id'] or f'#{target_id}'} is closed out: kept in "
                f"history, hidden from current-slice search (find it with "
                f"include_superseded if needed)."
            )
        con.commit()

    src_label = src["template_id"] or f"#{source_id}"
    tgt_label = tgt["template_id"] or f"#{target_id}"
    note_str = f'\n  Note: "{note.strip()}"' if note.strip() else ""
    return f"Asserted: {src_label} {relation} {tgt_label}.{note_str}{closed_note}"


# ── Tool: find_path ───────────────────────────────────────────────────────────

@mcp.tool()
def find_path(from_id: int, to_id: int) -> str:
    """
    Find the shortest chain of connections between two captures — how one
    idea reaches another through references, shared entities, asserted
    relations, and strong tag overlap.

    Args:
        from_id: Starting capture (numeric ID).
        to_id:   Destination capture (numeric ID).
    """
    with _db() as con:
        a = get_capture(con, from_id)
        b = get_capture(con, to_id)
        if a is None:
            return f"No capture with id #{from_id}."
        if b is None:
            return f"No capture with id #{to_id}."
        path = graph_find_path(con, from_id, to_id)
        labels = {}
        if path:
            for hop in path:
                cap = get_capture(con, hop["id"])
                labels[hop["id"]] = cap["template_id"] or f"#{hop['id']}" if cap else f"#{hop['id']}"

    a_label = a["template_id"] or f"#{from_id}"
    b_label = b["template_id"] or f"#{to_id}"
    if path is None:
        return (
            f"No path between {a_label} and {b_label} within 6 hops.\n"
            "They live in disconnected parts of the graph — that itself can be "
            "interesting: is there a connection worth writing down?"
        )

    lines = [f"Path from {a_label} to {b_label} ({len(path) - 1} hop(s)):\n"]
    for hop in path:
        via = f"  --[{hop['via'].replace('_', ' ')}]--> " if hop["via"] else "  "
        lines.append(f"{via}{labels[hop['id']]}")
    return "\n".join(lines)


# ── Tool: neighborhood ────────────────────────────────────────────────────────

@mcp.tool()
def neighborhood(capture_id: int, depth: int = 2) -> str:
    """
    Everything within N hops of a capture in the connection graph — its
    local knowledge cluster.

    Args:
        capture_id: Center capture (numeric ID).
        depth:      How many hops out to walk (default 2, max 4).
    """
    depth = max(1, min(depth, 4))
    with _db() as con:
        cap = get_capture(con, capture_id)
        if cap is None:
            return f"No capture with id #{capture_id}."
        dist = graph_neighborhood(con, capture_id, depth=depth)
        rows = []
        for cid, d in sorted(dist.items(), key=lambda x: (x[1], x[0])):
            other = get_capture(con, cid)
            if other:
                rows.append((d, other["template_id"] or f"#{cid}",
                             (other["summary"] or "")[:60]))

    label = cap["template_id"] or f"#{capture_id}"
    if not rows:
        return f"{label} has no connected captures within {depth} hop(s)."

    lines = [f"Neighborhood of {label} — {len(rows)} capture(s) within {depth} hop(s):\n"]
    current_d = None
    for d, tid, summary in rows:
        if d != current_d:
            lines.append(f"  {d} hop(s) away:")
            current_d = d
        lines.append(f"    {tid}  {summary}")
    return "\n".join(lines)


# ── Tool: lint ────────────────────────────────────────────────────────────────

@mcp.tool()
def lint(stale_question_days: int = 30) -> str:
    """
    Knowledge base health check. Catches the ways a knowledge base silently
    rots: orphan captures nothing links to, superseded claims not closed
    out, unresolved contradictions, open questions going stale, and
    fragmented tags from normalization failures.

    Run it occasionally — a base that is never linted entrenches errors
    instead of correcting them.

    Args:
        stale_question_days: Age at which an unanswered ? question is
                             flagged (default 30).
    """
    with _db() as con:
        report = run_lint(con, stale_question_days=stale_question_days)

    lines = ["Knowledge Base Lint\n" + "─" * 40]

    orphans = report["orphans"]
    lines.append(f"\nOrphan captures (no connections at all): {len(orphans)}")
    for o in orphans[:10]:
        lines.append(f"  [{o['template_id'] or '#' + str(o['id'])}]  {(o['summary'] or '')[:60]}")
    if len(orphans) > 10:
        lines.append(f"  … and {len(orphans) - 10} more")
    if not orphans:
        lines.append("  (none)")

    stale = report["stale_claims"]
    lines.append(f"\nSuperseded but not closed out: {len(stale)}")
    for s in stale:
        lines.append(f"  [{s['template_id']}]  {(s['summary'] or '')[:60]}")
    if not stale:
        lines.append("  (none)")

    refutes = report["refutes_pairs"]
    lines.append(f"\nUnresolved contradictions (refutes, both sides current): {len(refutes)}")
    for p in refutes:
        lines.append(f"  {p['source_template']} refutes {p['target_template']} — decide which stands")
    if not refutes:
        lines.append("  (none)")

    questions = report["stale_questions"]
    lines.append(f"\nOpen questions older than {stale_question_days} days: {len(questions)}")
    for q in questions[:10]:
        lines.append(f"  [{q['template_id']}] ({q['created_at'][:10]})  ?{q['question']}")
    if len(questions) > 10:
        lines.append(f"  … and {len(questions) - 10} more")
    if not questions:
        lines.append("  (none)")

    singles = report["singleton_tags"]
    lines.append(f"\nTags used exactly once (possible fragmentation): {len(singles)}")
    if singles:
        lines.append("  " + "  ".join(f"{t['prefix']}{t['value']}" for t in singles[:15]))
        lines.append("  A near-duplicate of a common tag usually means a spelling variant "
                     "worth fixing via correct_ocr.")
    else:
        lines.append("  (none)")

    return "\n".join(lines)


# ── Tool: export_backup / import_backup ───────────────────────────────────────

@mcp.tool()
def export_backup(file_path: str = "") -> str:
    """
    Write the entire knowledge base to a versioned JSONL file (ksj-export-v1)
    — a real backup with a matching import path, not a one-way dump.

    Contains captures (all fields), tags, entities, and connections. The
    format is documented in docs/EXPORT_FORMAT.md in the ksj-mcp repo, so
    anything can consume it.

    Args:
        file_path: Where to write. Default: ksj-export-<date>.jsonl in the
                   KSJ data directory.
    """
    with _db() as con:
        text = export_jsonl(con)

    if file_path.strip():
        path = Path(file_path.strip())
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        path = _data_dir() / f"ksj-export-{stamp}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        return f"Could not write {path}: {e}"

    n_caps = text.count('"kind": "capture"')
    return (
        f"Backup written: {path}\n"
        f"  {n_caps} capture(s), {len(text.splitlines())} records, "
        f"{len(text) // 1024} KB\n"
        f"Restore with import_backup({str(path)!r})."
    )


@mcp.tool()
def import_backup(file_path: str) -> str:
    """
    Restore a ksj-export-v1 JSONL backup into the knowledge base.

    Additive and non-destructive: captures that collide with an existing
    (volume, template ID) are skipped, nothing is overwritten. User-asserted
    edges are restored; derived connections are rebuilt over the merged base.

    Args:
        file_path: Path to a file produced by export_backup.
    """
    path = Path(file_path.strip())
    if not path.exists():
        return f"File not found: {path}"

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Could not read {path}: {e}"

    with _db() as con:
        try:
            stats = import_jsonl(con, text)
        except (ValueError, json.JSONDecodeError) as e:
            return f"Import failed, nothing was written: {e}"
        rebuild = db_rebuild_connections(con)

    return (
        f"Import complete from {path.name}:\n"
        f"  Captures restored : {stats['captures']} "
        f"({stats['skipped']} skipped as already present)\n"
        f"  Tags              : {stats['tags']}\n"
        f"  Entities          : {stats['entities']}\n"
        f"  Asserted edges    : {stats['asserted_edges']}\n"
        f"Connection graph rebuilt over the merged base "
        f"({rebuild['edges']} edge(s) total)."
    )


# ── Tool: export_html ─────────────────────────────────────────────────────────

@mcp.tool()
def export_html(file_path: str = "") -> str:
    """
    Write a self-contained HTML view of the knowledge base — a bird's-eye
    browser for the whole journal that opens in any web browser, works
    offline, and needs no install.

    Four overview modes:
      Timeline    — every capture chronologically, with live search and
                    type / volume / journal-vs-AI filters; superseded
                    captures hidden behind a toggle. Each card also lists
                    its connections (mode 3) — click one to jump to the
                    connected capture.
      Index       — tags grouped by meaning (topics, dream themes, open
                    questions, insights, motifs, sensory details) plus the
                    entity register; every entry click-filters the timeline.
      Connections — (mode 3, built into every Timeline card, not a separate
                    tab) typed, directional links — references and asserted
                    relations always shown, tag/entity overlap only above
                    strength 2.0 so a well-tagged capture doesn't drown in
                    weak matches.
      Graph       — a slowly rotating globe of the whole connection graph:
                    the most-connected capture sits dead center of the
                    equator, everything else spirals outward by connection
                    rank. Dream Capture entries get their own smaller
                    nested inner globe, with any connection reaching the
                    main sphere shown as a distinct "rod". The spin is
                    bounded (settles on its own, never perpetual) — Reset
                    and every personalization toggle (globe size, show
                    superseded, journal/AI source, auto-rotate, force
                    labels) halt it immediately rather than letting it run
                    on underneath a control you just touched. Drag to spin
                    it manually, click a node to see its neighborhood and
                    jump to it, adjust the strength slider to reveal or
                    hide weaker tag/entity connections. A plain-language
                    summary above the graph — including the time span of
                    what's shown — describes what's currently visible
                    (capture/connection counts, clusters, isolated
                    captures, most-connected capture) and updates live as
                    the threshold changes.

    All data is inlined in the file: sharing or archiving the file shares a
    snapshot of the knowledge base.

    Args:
        file_path: Where to write. Default: ksj-view.html in the KSJ data
                   directory.
    """
    with _db() as con:
        data = collect_view_data(con)
        html = render_html(data)

    path = Path(file_path.strip()) if file_path.strip() else _data_dir() / "ksj-view.html"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    except OSError as e:
        return f"Could not write {path}: {e}"

    n = len(data["captures"])
    return (
        f"HTML view written: {path}\n"
        f"  {n} capture(s), {len(data['entities'])} entit(ies), "
        f"{len(html) // 1024} KB — open it in any browser.\n"
        f"Regenerate after new uploads to refresh the snapshot."
    )


# ── Tool: bulk_upload ─────────────────────────────────────────────────────────

@mcp.tool()
def bulk_upload(folder_path: str, force: bool = False, volume: int = 0) -> str:
    """
    Process all journal page photos in a folder at once.

    Finds every image file (JPG, PNG, TIFF, BMP, WebP) in the folder and runs
    the full upload pipeline on each one. Non-image files are skipped silently.
    Pages without a readable template ID are stored as UNIDENTIFIED rather
    than skipped. Pass volume=N when importing a second (or later) journal.

    Note: by default this path uses local Tesseract OCR, which performs
    poorly on cursive handwriting. For a handful of pages, share photos in
    chat and store them via manual_capture instead. For large handwritten
    imports, the user can enable cloud OCR with their own key
    (KSJ_OCR_BACKEND=azure — see README); it is off by default and every
    run states when it is active.

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
    lines = [
        _cloud_ocr_notice()
        + f"Bulk upload — {len(images)} image(s) found in {folder_path}\n{'─' * 50}"
    ]

    for img in images:
        result = _process_image(str(img), force=force, volume=volume)

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
        vols, scope_note = _read_scope(con)
        results = search_fts(
            con,
            query=query,
            tag_filter=tag_filter or None,
            date_from=date_from or None,
            date_to=date_to or None,
            volumes=vols,
        )

    if not results:
        return f"No captures found for query: {query!r}" + scope_note

    lines = [f"Found {len(results)} capture(s) for {query!r}:\n"]
    for r in results:
        tag_str = " ".join(f"{t['prefix']}{t['value']}" for t in r.get("tags", [])[:5])
        vol_str = f" vol {r['volume']}" if r.get("volume", 1) != 1 else ""
        lines.append(
            f"  [{r['template_id'] or 'UNIDENTIFIED'}]{vol_str} #{r['id']}  conf={r['confidence']:.0%}\n"
            f"    {r['summary'] or '(no summary)'}\n"
            f"    Tags: {tag_str or 'none'}\n"
            f"    Date: {r['created_at'][:10]}\n"
        )
    return "\n".join(lines) + scope_note


# ── Tool: find_connections ────────────────────────────────────────────────────

@mcp.tool()
def find_connections(capture_id: int, min_strength: float = 2.0, limit: int = 20) -> str:
    """
    Show connections for a capture: @-references first (deliberate
    assertions, both directions), then tag overlap ranked by IDF-weighted
    strength — shared rare tags count for much more than shared common ones.

    Args:
        capture_id:   The numeric ID returned by upload_capture or search_captures.
        min_strength: Minimum strength for tag-overlap edges (default 2.0 ≈
                      two ordinary shared tags or one rare one). References
                      are always shown. Lower it to see weaker links.
        limit:        Maximum connections listed (default 20). The total is
                      always reported.
    """
    with _db() as con:
        capture = get_capture(con, capture_id)
        if capture is None:
            return f"Capture #{capture_id} not found."
        vols, scope_note = _read_scope(con)
        connections = get_connections(con, capture_id, volumes=vols)

    label = capture["template_id"] or "UNIDENTIFIED"
    if not connections:
        return (
            f"No connections found for {label} (#{capture_id}).\n"
            "Upload more captures to discover relationships." + scope_note
        )

    always   = [c for c in connections if c["type"] in ("asserted", "reference")]
    overlaps = [c for c in connections if c["type"] not in ("asserted", "reference")]
    strong   = [c for c in overlaps if c["strength"] >= min_strength]
    shown    = (always + strong)[:limit]
    hidden_weak = len(overlaps) - len(strong)

    lines = [
        f"Connections for {label} (#{capture_id}) — "
        f"showing {len(shown)} of {len(connections)} total:\n"
    ]
    for c in shown:
        if c["type"] == "asserted":
            rel = c.get("relation") or "related to"
            dir_label = f"→ {rel}" if c["direction"] == "cites" else f"← {rel} by"
        elif c["type"] == "reference":
            dir_label = "→ cites" if c["direction"] == "cites" else "← cited by"
        elif c["type"] == "entity_overlap":
            dir_label = "↔ shares entities with"
        else:
            dir_label = "↔ shares tags with"
        vol_str = f" (vol {c['connected_volume']})" if c.get("connected_volume", 1) != 1 else ""
        superseded = "  [superseded]" if c.get("connected_valid_until") else ""
        note_str = f"\n    note: {c['note']}" if c.get("note") else ""
        lines.append(
            f"  {dir_label} {c['connected_template'] or 'UNIDENTIFIED'}{vol_str}{superseded}"
            f"  [{c['method'].replace('_', ' ')}]  strength={c['strength']:.1f}\n"
            f"    {c['connected_summary'] or '(no summary)'}{note_str}"
        )
    if hidden_weak > 0:
        lines.append(
            f"\n  … {hidden_weak} weaker tag-overlap connection(s) below "
            f"strength {min_strength:.1f} — pass min_strength=0 to see all."
        )
    return "\n".join(lines) + scope_note


# ── Tool: get_stats ───────────────────────────────────────────────────────────

@mcp.tool()
def get_stats() -> str:
    """
    Return an overview of your knowledge base: capture counts, top tags,
    open questions, key insights, and date range.
    """
    with _db() as con:
        vols, scope_note = _read_scope(con)
        stats = db_get_stats(con, volumes=vols)

    if stats["total_captures"] == 0:
        return "Your knowledge base is empty. Upload a journal photo to get started." + scope_note

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
        + scope_note
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

    # Plain markdown, deliberately: YAML frontmatter per capture, connections
    # under "## Related" as bare template IDs with relation labels — no
    # application-specific link syntax (§2.4c). Hand-written journal captures
    # and AI-extracted entries are kept in separate sections (§0.3/§1.11).
    with _db() as con:
        related_by_id = {c["id"]: get_connections(con, c["id"]) for c in captures}

    def _render(c: dict) -> list[str]:
        tags_str = ", ".join(f"{t['prefix']}{t['value']}" for t in c.get("tags", []))
        out = [
            "---",
            f"template_id: {c['template_id'] or 'unidentified'}",
            f"volume: {c.get('volume', 1)}",
            f"capture_id: {c['id']}",
            f"date: {c['created_at'][:10]}",
            f"source: {c.get('source', 'journal')}",
            f"confidence: {c['confidence']:.2f}",
            f"ocr_source: {'corrected' if c.get('corrected_ocr') else 'raw'}",
            f"tags: [{tags_str}]",
        ]
        if c.get("valid_until"):
            out.append(f"superseded: {c['valid_until'][:10]}")
        out += ["---", "", f"# {c['template_id'] or 'Unidentified'}  (#{c['id']})", ""]
        out.append(c["summary"] or "*(no summary)*")
        out.append("")

        for field, val in (c.get("content") or {}).items():
            if val and field != "tags_raw":
                out.append(f"**{field.replace('_', ' ').title()}:**")
                out.append(str(val))
                out.append("")

        related = related_by_id.get(c["id"]) or []
        if related:
            out.append("## Related")
            for r in related[:15]:
                rel_label = r.get("relation") or r["type"].replace("_", " ")
                direction = "" if r.get("direction") == "shared" else f" ({r['direction']})"
                out.append(f"- {r['connected_template'] or '#' + str(r['target_id'])}"
                           f" — {rel_label}{direction}")
            out.append("")
        return out

    journal = [c for c in captures if c.get("source", "journal") != "ai_extract"]
    ai      = [c for c in captures if c.get("source", "journal") == "ai_extract"]

    lines = ["# KSJ Knowledge Base Export", ""]
    lines.append(f"## Journal Captures ({len(journal)})" if journal else "## Journal Captures (0)")
    lines.append("")
    for c in journal:
        lines += _render(c)
    if ai:
        lines.append(f"## AI-Extracted Entries ({len(ai)})")
        lines.append("")
        for c in ai:
            lines += _render(c)

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


# ── Tool: surface_connections ─────────────────────────────────────────────────

@mcp.tool()
def surface_connections(
    syn_template_id: str,
    entry_ids: str = "",
    days: int = 0,
    depth: str = "standard",
) -> str:
    """
    Independently scan the Rapid Capture cluster behind a Synthesis page you
    have ALREADY written and photographed, then prepare a structured
    dialogue comparing what the scan found against what the page found.

    This runs AFTER a SYN page exists for the cluster — never before.
    Running it before the page is written would let the AI perform the
    cognitive act synthesis is meant to force (see
    ksj2_documents/SYN_companion_decision_optionB_2026-08-03.md). There is
    no override flag for this precondition — if no SYN page is found, the
    tool declines and points to suggest_synthesis() instead.

    The scan itself never reads the SYN page's own content before producing
    its connection map, so the Phase 2 comparison is meaningful rather than
    confirmatory. This tool does NOT write to the database — it prepares
    the scan data and dialogue instructions for Claude to run in this
    conversation. `commit_distillation()`, which will persist the outcome,
    is not implemented yet — do not attempt to call it.

    Trigger phrases: "Run surface_connections on SYN-004", "Compare my
    synthesis against an independent scan".

    Args:
        syn_template_id: The SYN page's template ID (e.g. "SYN-004").
                         Required — the tool declines if no SYN page with
                         this ID exists.
        entry_ids:       Optional comma-separated RC template IDs (e.g.
                         "RC-001,RC-047,RC-083") to scan explicitly.
                         Required when the SYN page's RC cluster can't be
                         resolved automatically — no matching tag cluster,
                         or more than one candidate cluster.
        days:            Optional — narrow the resolved cluster to RC
                         entries created in the last N days. 0 = no filter.
        depth:           "brief" | "standard" (default) | "deep" — how many
                         follow-up questions per tier to plan for in the
                         dialogue. The user can also say "more on this one"
                         for any single connection regardless of depth.

    Returns the RC cluster data, gap candidates, and full scan + dialogue
    instructions for Claude to execute.
    """
    syn_template_id = syn_template_id.strip().upper()
    if not syn_template_id:
        return 'Please provide the SYN page\'s template ID, e.g. surface_connections(syn_template_id="SYN-004").'

    depth = depth.strip().lower()
    if depth not in ("brief", "standard", "deep"):
        depth = "standard"

    with _db() as con:
        syn_cap = get_capture_by_template(con, syn_template_id, type_="SYN")
        if syn_cap is None:
            return (
                f"No Synthesis page found for {syn_template_id}.\n\n"
                "surface_connections compares an independent scan against synthesis "
                "you've already done — it runs after the SYN page, not before. "
                "suggest_synthesis() shows which clusters are ready to write."
            )

        # §5.3: resolve the RC cluster. Explicit entry_ids always win when given.
        explicit_ids = [t.strip().upper() for t in entry_ids.split(",") if t.strip()]
        rc_caps: list[dict] = []
        cluster_tag = None

        if explicit_ids:
            missing = []
            for tid in explicit_ids:
                cap = get_capture_by_template(con, tid, type_="RC")
                if cap is None:
                    missing.append(tid)
                else:
                    rc_caps.append(cap)
            if missing:
                return f"No RC capture found for: {', '.join(missing)}. Check the template IDs and try again."
        else:
            # Chain 1: consume suggest_synthesis's own clustering, never
            # reimplement it. min_size=1 here because we're matching an
            # EXISTING SYN page's tag, not gating readiness-to-write.
            clusters = get_rc_tag_clusters(con, min_size=1)
            matches = [c for c in clusters if syn_template_id in c["syn_templates"]]
            if not matches:
                return (
                    f"{syn_template_id} doesn't share a #tag with any current RC "
                    "cluster, so its cluster can't be resolved automatically. Call "
                    "surface_connections again with entry_ids naming the RC pages "
                    f"{syn_template_id} synthesizes."
                )
            if len(matches) > 1:
                tag_list = ", ".join(f"#{m['tag']} ({m['rc_count']} RC)" for m in matches)
                return (
                    f"{syn_template_id} matches more than one RC cluster: {tag_list}. "
                    "surface_connections needs an unambiguous cluster — call it again "
                    "with entry_ids naming the specific RC pages to scan."
                )
            cluster = matches[0]
            cluster_tag = cluster["tag"]
            for tid in cluster["rc_templates"]:
                cap = get_capture_by_template(con, tid, type_="RC")
                if cap:
                    rc_caps.append(cap)

        if days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            rc_caps = [c for c in rc_caps if c["created_at"] >= cutoff]

        if len(rc_caps) < 2:
            scope = "via entry_ids" if explicit_ids else f"tag #{cluster_tag}"
            window = f", last {days} days" if days > 0 else ""
            return (
                f"Only {len(rc_caps)} RC entry in scope after resolving the cluster "
                f"({scope}{window}) — surface_connections needs at least two to "
                "compare. Widen entry_ids or drop the days filter."
            )

        # §5.2: warn (don't block) if the SYN page's own transcription is
        # both uncorrected and low-confidence — Phase 2 compares against it,
        # and a bad transcription would produce false MISSED entries.
        ocr_warning = ""
        if not syn_cap.get("corrected_ocr") and syn_cap.get("confidence", 1.0) < 0.6:
            ocr_warning = (
                f"\n⚠ {syn_template_id}'s transcription is uncorrected and "
                f"low-confidence ({syn_cap.get('confidence', 0.0):.0%}). Consider "
                "correct_ocr() first — Phase 2's comparison reads this text, and a "
                "bad transcription can produce MISSED entries that are really just "
                "OCR errors.\n"
            )

        # Chain 2: find_unapplied composed across the cluster, deduped by the
        # uncited capture it names. No new gap logic — reuses the shipped
        # propagation-failure check as-is.
        gap_candidates: dict[int, dict] = {}
        for cap in rc_caps:
            for u in find_unapplied(con, cap["id"], limit=5):
                gap_candidates.setdefault(u["id"], u)

        rc_lines = []
        for cap in rc_caps:
            text = (cap.get("corrected_ocr") or cap["raw_ocr"] or "").strip()
            tag_str = " ".join(
                f"{t['prefix']}{t['display'] or t['value']}" for t in cap["tags"]
            ) or "(no tags)"
            rc_lines.append(
                f"### {cap['template_id']}  ({cap['created_at'][:10]})\n"
                f"Tags: {tag_str}\n{text}\n"
            )

        gap_lines = [
            f"  - {g['template_id']} (uncited elsewhere) — shared: {', '.join(g['shared'])}\n"
            f"    \"{(g['summary'] or '')[:100]}\""
            for g in sorted(gap_candidates.values(), key=lambda g: -len(g["shared"]))
        ]

        cluster_desc = (
            f"explicit entry_ids ({', '.join(c['template_id'] for c in rc_caps)})"
            if explicit_ids else f"RC cluster #{cluster_tag}"
        )
        syn_text = (syn_cap.get("corrected_ocr") or syn_cap["raw_ocr"] or "").strip()

    depth_guidance = {
        "brief":    "Ask at most one question per tier unless the user asks for more.",
        "standard": "Ask one to two questions per tier.",
        "deep":     "Ask up to three or four questions per tier, and proactively "
                    "check whether any single connection deserves more.",
    }[depth]

    return f"""## surface_connections — {syn_template_id}
Cluster: {cluster_desc} · {len(rc_caps)} RC entries{ocr_warning}

---

## PART A — RC cluster (blind scan input)

{chr(10).join(rc_lines)}

### Gap candidates (find_unapplied, composed across the cluster)
{chr(10).join(gap_lines) if gap_lines else "  (none found)"}

---

## PHASE 1 INSTRUCTIONS — do this before reading Part B below

Using ONLY the RC entries in Part A above — do not read Part B yet — produce
an independent connection map. Look for shared tags, recurring terms, echoed
action items, and unanswered questions across these entries. Score each
candidate connection:

  🟢 Strong     — multiple corroborating signals (tag overlap, explicit cross-refs)
  🟡 Developing — partial signal, plausible but unverified
  🔴 Seed       — a single weak signal, human-judgment-only

Print this once, above the map, verbatim:
> Tiers describe connection strength between entries — not the strength of
> the evidence within them.

Present this as the Phase 1 connection map before continuing to Part B.

---

## PART B — {syn_template_id} (for Phase 2 comparison only)

Date: {syn_cap['created_at'][:10]}
{syn_text}

---

## PHASE 2 INSTRUCTIONS — structured dialogue

Compare your Phase 1 map against what {syn_template_id} actually captured.
Conduct a dialogue with the user, one question at a time, in this order:
Green → Yellow → Red → Gaps (the gap candidates above, plus anything
{syn_template_id} shares a topic with but never explicitly cross-references).

Question forms — do not mix these up:
  🟢 Green  — OPEN:          "What, if anything, connects these?"
  🟡 Yellow — FORCED CHOICE: offer two concrete framings, ask which fits (or neither)
  🔴 Red    — BINARY:        "Genuine signal, or retire it?"
  Gaps      — OPEN:          ask what neither the scan nor the page addressed

Depth: {depth}. {depth_guidance} At any point the user can say "more on this
one" for a follow-up on a specific connection, regardless of depth.

STANDING RULE — ask, never propose: a question makes the user think; a
proposed interpretation to accept or reject makes you think, in their place.
Restate this rule to yourself before EVERY question, not just once at the
top — the drift toward proposing gets easier to fall into as a dialogue goes
on. Do not ask more than 3 consecutive follow-up questions on the same
connection — past that point it has become a negotiation over your reading
of it, not the user's.

The dialogue ends when the user says "done" or all questions are answered.

---

## OUTPUT — after the dialogue

Produce a revised connection map with FOUR categories:

  CONFIRMED (user agreed it's real)
  RETIRED   (user rejected it)
  DEFERRED  (user wants to revisit later)
  MISSED    (found by your scan, absent from {syn_template_id})

...and its mirror, which matters just as much:

  BEYOND THE SCAN (on {syn_template_id}, not derivable from tags/text — came
  from the user, not from any signal you could have found)

The distillation — what the comparison revealed, not the connection list or
the dialogue transcript — is the artifact worth keeping. `commit_distillation()`
is not implemented yet, so do not attempt to call it: present the revised
map and the distillation to the user directly, and note it's ready to be
stored once that tool ships."""


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
      - Synthesis activity (digitized SYN pages — descriptive only)
      - Template balance (which template types are unused)

    All KPIs count hand-written journal captures only; AI-extracted (AIEX)
    entries are excluded so they don't inflate your practice metrics.

    Returns a health score and specific, actionable recommendations.
    """
    with _db() as con:
        kpis = get_journal_kpis(con)

    if kpis["total"] == 0:
        return "Your knowledge base is empty. Upload a journal photo to get started."

    by_type = kpis["by_type"]
    recommendations = []
    score_penalties  = 0

    # ── Synthesis activity (descriptive, NOT scored) ───────────────────
    # Physical SYN pages are a deliberate analog practice and often never
    # reach the digital base, so the digitized RC:SYN ratio cannot measure
    # the actual practice. Report it without scoring against a target.
    ratio = kpis["synthesis_ratio"]
    rc    = by_type.get("RC", 0)
    syn   = by_type.get("SYN", 0)

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
        "Journal Health  (hand-written captures only)\n" + "─" * 40,
        f"  Health score : {score_bar}  {score}/{max_score}",
        "",
        "KPIs (last 4 weeks):",
        f"  Capture velocity  : {vel:.1f} / week",
        f"  Insight velocity  : {ins:.1f} / week",
        f"  Last Review       : {rev_str}",
        f"  Open questions    : {unanswered}",
        f"  Digitized RC:SYN  : {ratio_str}  (descriptive only — physical SYN"
        f" pages worked on paper don't reach this count)",
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
def list_by_tag(tag: str, prefix: str = "", role: str = "") -> str:
    """
    Browse all captures that carry a specific tag — no text query required.

    Use this to find every note related to a topic, source, question, or insight:
      list_by_tag("machine-learning")           → all captures with that tag
      list_by_tag("machine-learning", prefix="#") → only # topic tags
      list_by_tag("RC-012", prefix="@")          → captures referencing @RC-012
      list_by_tag("deadline", prefix="!")        → priority items
      list_by_tag("falling", role="motif")       → DC recurring motifs only

    The same prefix character means different things on Dream Capture pages
    (# theme, @ symbol, ! motif) than on RC/SYN/REV (# topic, @ reference,
    ! priority). Use *role* to select by meaning rather than character:
    topic, theme, reference, entity, priority, motif, question, insight,
    causal, sensory.

    Args:
        tag:    Tag value to look up (without the prefix character).
        prefix: Optional prefix character: #  @  !  ?  $  *  ->
        role:   Optional semantic role — the precise way to disambiguate
                DC vs RC/SYN/REV meanings.
    """
    if not tag.strip():
        return "Please provide a tag value to look up."

    with _db() as con:
        vols, scope_note = _read_scope(con)
        results = get_captures_by_tag(
            con, tag.strip(), prefix=prefix.strip(), role=role.strip(), volumes=vols
        )

    if not results:
        pfx_str = f"{prefix}{tag}" if prefix else tag
        role_str = f" (role={role})" if role else ""
        return f"No captures found with tag: {pfx_str!r}{role_str}" + scope_note

    # When a colliding prefix is queried without a role, report the split
    # rather than silently merging different meanings into one list (§1.10).
    role_counts: dict[str, int] = {}
    for r in results:
        role_counts[r.get("matched_role") or "untyped"] = \
            role_counts.get(r.get("matched_role") or "untyped", 0) + 1

    pfx_label = f"{prefix}{tag}" if prefix else tag
    lines = [f"Captures tagged {pfx_label!r}  ({len(results)} found):"]
    if not role and len(role_counts) > 1:
        split = ", ".join(f"{n} as {r}" for r, n in sorted(role_counts.items()))
        lines.append(
            f"  Note: this tag carries {len(role_counts)} different meanings here — {split}. "
            f"Pass role=... to narrow."
        )
    lines.append("")
    for r in results:
        tag_str = " ".join(f"{t['prefix']}{t['value']}" for t in r.get("tags", [])[:5])
        role_note = f"  ({r['matched_role']})" if r.get("matched_role") else ""
        vol_str = f" vol {r['volume']}" if r.get("volume", 1) != 1 else ""
        lines.append(
            f"  [{r['template_id'] or 'UNIDENTIFIED'}]{vol_str} #{r['id']}  "
            f"{r['created_at'][:10]}{role_note}\n"
            f"    {r['summary'] or '(no summary)'}\n"
            f"    Tags: {tag_str or 'none'}\n"
        )
    return "\n".join(lines) + scope_note


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

    # Sensory modality share: in how many dreams does each modality appear
    # at all (counted once per dream, including single occurrences)
    sensory_dreams: dict[str, int] = {}
    for d in dc_entries:
        seen_here = {t["value"] for t in d["tags"] if t["prefix"] == "*"}
        for v in seen_here:
            sensory_dreams[v] = sensory_dreams.get(v, 0) + 1
    sensory_dreams = dict(sorted(sensory_dreams.items(), key=lambda x: -x[1]))

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
        # Writer-FLAGGED recurrence — a symbol the dreamer marked !recurring
        # three times carries more analytic weight than one merely mentioned
        # five times. Kept separate from the counted frequencies above.
        lines.append("\nMotifs you flagged as recurring (!):")
        lines.append("  " + "  |  ".join(f"!{k} ×{v}" for k, v in list(recurring_motifs.items())[:10]))

    if sensory_dreams:
        n = len(dc_entries)
        lines.append("\nSensory modalities (share of dreams each appears in):")
        lines.append("  " + "  |  ".join(
            f"*{k} {v}/{n} ({v / n:.0%})" for k, v in list(sensory_dreams.items())[:10]
        ))

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

    # Fixed-shape context: both sections always render, with an explicit
    # "(none)" when empty — an omitted section is indistinguishable from
    # truncation downstream.
    rel_lines = []
    for r in related:
        tag_str = " ".join(f"{t['prefix']}{t['value']}" for t in r.get("tags", [])[:4])
        rel_lines.append(
            f"  - {r['template_id']} — \"{r['summary'][:80] or '(no summary)'}\""
            + (f"  |  {tag_str}" if tag_str else "")
        )
    related_block = (
        "\n### Potentially related existing entries:\n"
        + ("\n".join(rel_lines) if rel_lines else "  (none found)")
    )

    tag_block = f"\n### Active knowledge base tags:\n  {top_tags or '(none yet)'}"

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

Present the extraction as a structured review for user approval using EXACTLY
these four section headers, in this order, every one always present. If a
section is empty, write "None found." under it — NEVER omit a section, no
matter how long the conversation is:

1. **Insights** (each with tier, tags)
2. **Connections to Existing Entries**
3. **Open Questions**
4. **Action Items**

After the user approves, call `commit_aiex()` with the confirmed JSON:

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

            # Parse tags from list format: ["#topic", "@source", "bare-word"].
            # Normalized + role-assigned the same way extract_schema_tags does
            # (§1.6/§1.10), so AIEX tags show up in the Index view and role
            # filters exactly like journal-capture tags do.
            tags: list[dict] = []
            seen: set[tuple[str, str]] = set()

            for tag_str in tag_strings:
                tag_str = tag_str.strip()
                if len(tag_str) < 2:
                    continue
                if tag_str[0] in ('#', '@', '!', '?', '$', '*'):
                    prefix, raw_value = tag_str[0], tag_str[1:]
                else:
                    prefix, raw_value = '#', tag_str
                value = normalize_tag_value(raw_value)
                if not value:
                    continue
                key = (prefix, value)
                if key not in seen:
                    seen.add(key)
                    tags.append({
                        "prefix":  prefix,
                        "value":   value,
                        "display": raw_value,
                        "role":    assign_role(prefix, value, "AIEX"),
                    })

            # Also extract inline schema tags from the insight text itself
            for t in extract_schema_tags(text, "AIEX"):
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

    # Fixed-shape report, rendered server-side: every section always present,
    # empty sections say so explicitly. The report must not vary with
    # conversation state — relay it to the user as-is.
    _tier_emoji = {"Seed": "🟢", "Developing": "🔴", "Strong": "🟡"}

    lines = [
        f"AIEX Commit — {len(stored)} insight(s) stored\n{'─' * 40}",
        f"  Session : {session_focus or '(unspecified)'}",
        f"  Platform: {source_platform or '(unspecified)'}",
        f"  Date    : {date}",
        f"\nInsights ({len(stored)}):",
    ]
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

    lines.append(f"\n\nOpen questions ({len(open_questions)}):")
    if open_questions:
        for q in open_questions:
            lines.append(f"  ? {q}")
    else:
        lines.append("  (none recorded)")

    lines.append(f"\nAction items ({len(action_items)}):")
    if action_items:
        for item in action_items:
            if isinstance(item, dict):
                p = "!" if item.get("priority") == "!" else " "
                lines.append(f"  {p} {item.get('text', str(item))}")
            else:
                lines.append(f"    {item}")
    else:
        lines.append("  (none recorded)")

    lines.append(f"\nAll entries stored as type=AIEX (AI-Extracted flag).")
    return "\n".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
