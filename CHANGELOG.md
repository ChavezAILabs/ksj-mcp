# Changelog

## 3.8.0 — 2026-09-28

Two fixes from the 2026-09-25 test pass of 3.7.0: a bounded connection graph
with a fast rebuild (F1), and a section parser that reads the layouts people
and assistants actually write (F2). A one-time migration applies both to
existing journals.

### Bounded tag-overlap graph (F1)

- **Before:** any two captures sharing any tag were linked. On a 1,021-capture
  journal that was 347,747 `tag_overlap` edges — 67% of all possible pairs —
  so `find_connections`, `neighborhood` and `find_path` were mostly noise, and
  backups ran to 61 MB.
- **Tags that name the kind of note never create edges:** `$insight`,
  `?question`, `!priority`. Chosen by role, not prefix — on DC pages `!` is a
  dream motif, which is topical and still links.
- **Each capture keeps at most 25 tag edges** (`TAG_LINKS_PER_CAPTURE`).
  Candidates sharing a rare tag (on ≤ 10% of captures, floor 10) rank first;
  candidates sharing only common tags fill the remaining slots, strongest
  first, then nearest in time — so an entry tagged only `#rh-investigation`
  links to its neighbours in that thread. An edge exists when it is in either
  endpoint's list. A plain frequency cut-off was measured and rejected: at 10%
  it left 362 captures (35%) with no connections at all.
- **`rebuild_connections` is one pass and one transaction:** each unordered
  pair is computed once, rows are bulk-inserted, and other ksj processes see
  the old graph or the new one, never a half-built one. `insert_connection`
  uses `RETURNING id` instead of a follow-up query, and `build_connections`
  looks up target template IDs in one query.
- Same journal after the change: 17,170 edges (3.3% of pairs); rebuild
  **410 s → 6.3 s**; `find_connections` for AIEX-520 still ranks AIEX-512 and
  AIEX-513 at the top. Captures with no connections: 17 → 20 (the three new
  ones shared nothing but `$insight`).
- **`rebuild_connections` output** now breaks edges down by type (tag overlap,
  entity overlap, references, asserted). It previously counted asserted edges
  as "overlap".

### Section parsing (F2)

- **`Label: value` on one line is read.** The parser required a newline after
  every label, so assistant transcriptions (`First Impressions: …`) produced
  empty fields and empty summaries.
- **Sections end at the next label, reliably:** any known label for any
  template, metadata labels (Date, Source, Subject, Topic, Focus…), a label
  alone on its line ending in `:` ("Action Items:", "IMMEDIATE (this week):"),
  or an ALL-CAPS heading ("AI QUERIES", "NEXT STEPS"). The old end-of-section
  rule was disabled by its own case-insensitive flag, so sections without a
  colon bled into each other.
- Labels match only at the start of a line; `**Label:**` and `### Label` are
  accepted; bullets (`- Notes: x`) and prose that begins with a label word
  stay content.
- **A tag list is never a summary.** When no content field is found the
  summary is the page's first meaningful line.
- Placeholders such as `(none)` in Quick Questions or Tags no longer become
  tags.
- SYN pages: "Patterns discovered" / "Patterns identified" fill `patterns`.

### Migration

- `migrate_v38` runs once at startup (off the handshake path, after
  `init_db`): copies `captures.db` to `captures.db.bak-v38`, re-parses
  RC/SYN/REV/DC/ISO captures from `COALESCE(corrected_ocr, raw_ocr)`, and the
  server then rebuilds the graph. AIEX and WA entries, hand-asserted entities
  and user-asserted edges are untouched. On the 1,021-capture journal it took
  about 30 s on a Celeron N4120 under full load. Completion is recorded in the
  `settings` table (`migrated_v38`).

## 3.7.0 — 2026-09-24

Three workstreams: a fast `initialize` handshake, back/forward navigation in
the HTML view, and the Wild Art (WA) entry type with accept-by-default uploads.

### Fast `initialize` (cloud / Cowork sessions)

- **The server answers `initialize` and `tools/list` before it touches the
  capture store.** Opening the database, running migrations, and (after a v3
  migration) rebuilding the connection graph used to happen at import time,
  before the handshake. They now run in a background thread started when the
  process starts. Every tool waits for the store to be ready rather than
  failing.
- **A locked database no longer kills startup.** Before, if another process
  held a write lock (for example a second ksj instance mid-`bulk_upload`), the
  server waited out SQLite's 5 s busy timeout and then crashed before replying
  to `initialize`. Now the handshake completes normally, the background open
  retries for up to 2 minutes, and tool calls wait up to 30 s for a lock
  instead of 5 s.
- Measured on a 1,000-capture / 140k-edge store (Linux): `initialize` answered
  in about 1.05 s, both on a normal start and while another process held the
  database locked (where 3.6.2 crashed). The remaining time is Python startup
  plus importing the MCP SDK (~0.9 s) and no longer depends on store size.
  Installing with `--compile-bytecode` removes about 0.8 s more from the first
  launch after an install.
- Importing `ksj_mcp.server` (e.g. in tests) no longer opens or migrates
  `~/.ksj-mcp/captures.db`.
- Protocol: unchanged. The SDK 2.0.0 server is dual-era over stdio. It answers
  the classic `initialize` handshake (up to `2025-11-25`) and, when a client's
  first request carries the `2026-07-28` envelope, the modern era including
  `server/discover` (verified). The client decides which era to use.

### export.html navigation

- **Back works.** Every view change (tab switch, tag or entity filter, jump to
  a capture, graph drill-in, recenter) is one entry in a single navigation
  history, mirrored into the URL hash (`#timeline/capture/12`,
  `#graph/cluster/topic/ml`, …). The new in-page **← Back** button and the
  browser's Back button both step back through views inside the page. The
  browser's button leaves the page only from the first view. Returning to a
  Timeline view restores its filters and scroll position.
- **Reload / shared links** reopen the view named in the hash.
- The Graph tab's separate Back stack is replaced by the shared history.
- **Footer metadata:** ksj-mcp, mcp, pydantic, and Python versions, the export
  time, and capture (by type), entity, and connection counts.
- **False empty states in the Graph tab fixed.** "View in graph" on a
  superseded capture, or one the graph's source filter excludes, used to
  show "not visible under the current filters". The filter is now relaxed to
  show it. A capture whose neighbours are all filtered out now says how many
  connections are hidden instead of drawing an empty ring. (The Timeline
  cards' "No connections yet" was checked on every card of a 1,000-capture
  export and did not reproduce.)
- The `export_html` tool description is rewritten for the current view: the
  ego-centric graph, navigation, and footer. The old rotating-globe wording
  is gone.
- WA and ISO are always in the Timeline type filter. A Wild Art reason filter
  ("needs attention", "loose captures", or a specific reason) appears when
  type = WA. WA cards show their reason, the page ID they were read as, and a
  link to the capture they conflict with. Loose captures show "no volume".

### Wild Art (WA) and ISO pages

- **Accept by default.** `upload_capture`, `bulk_upload`, and `manual_capture`
  never reject a page. A page that fails classification, validation, or ID
  assignment is stored as a Wild Art entry (`WA-001`, …) with text, tags,
  image, and connections kept as for any other page. The reason is recorded
  as `wa_reason`: `id_conflict`, `unrecognized_template`,
  `ocr_low_confidence`, `validation_error`, `loose_capture`, `manual`, or
  `other`, with free-text `wa_detail`. For `id_conflict` the entry also
  records `claimed_page_id` and `conflicts_with`, and it keeps its real
  volume. It is never moved to another volume as a workaround, and the
  existing capture is never touched. `force=True` still replaces on purpose.
- `ocr_low_confidence` applies only when the page ID itself was read loosely
  (OCR-confusion-normalized) from an OCR pass below 60% confidence. A strictly
  read ID on a low-confidence page is filed normally, with the usual warning.
- An OCR failure on one specific image now keeps the photo as WA `other`.
  Only a missing file or a missing/misconfigured OCR engine still returns an
  error without storing anything.
- **Loose captures:** `wa_reason="loose_capture"` (plus e.g.
  `wa_detail="napkin"`) on `upload_capture` / `manual_capture` stores
  off-journal material with no volume and no page ID. Volume read-scope
  filters never hide loose captures.
- **New tool `promote_capture`** files a WA entry as RC, SYN, REV, DC, or ISO
  in place. It keeps the capture's `#id`, image, and asserted links, re-parses
  it as the target type, and records `promoted_from` (the WA ID) and
  `promoted_at` while keeping `wa_reason` / `wa_detail`. If the page ID is
  taken, **nothing changes** and the conflict is reported with the choices.
  `force=True` is documented as dangerous: it moves the current holder to
  Wild Art (nothing deleted) and gives the ID to the promoted entry. WA
  numbers are never reused. `identify_capture` on a WA entry runs the
  promotion.
- **New `develops` relation** for `assert_connection`: a journal entry that
  develops an idea first scribbled in a loose capture. Shown in export.html
  as "developed by".
- **ISO: 3-D isometric grid pages** are a first-class type
  (`manual_capture(template_id="ISO-001")`). They have their own parser
  (subject / notes / tags; same-line values accepted) and appear under their
  own type in search, list, stats, Index, and Timeline. `@ISO-NNN` and
  `@WA-NNN` work as references. OCR detects ISO IDs only in strict `ISO-001`
  form, so "ISO 400" in page text is never read as a page ID.
- **Visibility:** `list_by_tag` and `search_captures` take `entry_type` and
  `wa_reason` (with an empty tag or query they list the queue). `get_stats`
  and `journal_health` report Wild Art split into "needs attention" and
  "loose captures". `journal_health` recommends reviewing the queue but does
  not lower the score for it.
- **Schema migration (`migrate_v37`)** is additive. It copies `captures.db`
  to `captures.db.bak-v37` first, then rebuilds the captures table with the
  same ids and values. The rebuild is needed because SQLite can only change a
  CHECK constraint that way. It adds `ISO`/`WA` to the type constraint, makes
  `volume` nullable, adds the WA columns (all empty for existing rows), and
  adds a unique index on WA IDs. Tags, connections, entities, and the
  full-text index are untouched. It was verified on a 1,000-capture store
  written by 3.6.2: every capture, tag, and edge was identical before and
  after. `export_backup` / `import_backup` carry the new fields.
