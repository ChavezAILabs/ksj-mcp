# KSJ MCP Server

<p align="center">
  <a href="https://www.amazon.com/dp/B0GPW5WBZL">
    <img src="docs/cover.jpg" alt="Knowledge Synthesis Journal v2.0 cover" width="265" height="342">
  </a>
</p>

**Knowledge Synthesis Journal v2.0 — AI companion**

**Current release: ksj-mcp v3.9.0** · built on **MCP SDK v2.0.0**

Turn your handwritten journal photos into a searchable, AI-powered knowledge base — privately, on your own machine.

**Get the journal:** [Knowledge Synthesis Journal v2.0 on Amazon](https://www.amazon.com/dp/B0GPW5WBZL)

---

## Contents

- [What it does](#what-it-does)
- [AI platform support](#ai-platform-support)
- [Setup (3 steps)](#setup-3-steps)
- [Usage](#usage)
- [Available tools](#available-tools)
- [Schema tag system](#schema-tag-system)
- [Wild Art: nothing is ever rejected](#wild-art-nothing-is-ever-rejected)
- [Multiple journals (volumes)](#multiple-journals-volumes)
- [Troubleshooting](#troubleshooting)
- [Data location](#data-location)
- [License](#license)

---

## What it does

The KSJ MCP server connects your knowledge — handwritten or digital — to an AI assistant via the **Model Context Protocol (MCP)** — an open standard for linking AI models to local tools and data.

### Physical journal → knowledge base

Photograph a journal page, show it to your AI assistant, and it can:

- Search across everything you've ever written
- Find connections between ideas (shared tags, `@` references)
- Surface your open questions, key insights, and breakthroughs
- Export your knowledge base as Markdown or JSON

**How pages get in — two paths:**

1. **Assistant vision (recommended).** Share the photo in chat, let your
   assistant read the handwriting, confirm the transcription, and it stores the
   page with `manual_capture`. Modern AI vision is dramatically more accurate on
   handwriting than traditional OCR — this is the normal workflow.
2. **Local OCR (optional).** `upload_capture` and `bulk_upload` run
   [Tesseract](#optional-offline-ocr-tesseract) on your machine. Fully offline,
   but Tesseract struggles badly with cursive handwriting — best for printed or
   very neat text.

Either way, a bad read is never permanent: `correct_ocr` replaces a stored
capture's text and re-runs parsing, tags, and connections, while the original
read is preserved. And nothing you upload is ever rejected — a page that can't
be filed normally lands in [Wild Art](#wild-art-nothing-is-ever-rejected)
for review instead.

### AI research sessions → structured insights

Spend an hour going deep on a topic with an AI assistant and most of that thinking vanishes when the chat ends. `extract_insights` fixes that — paste or pipe a session transcript and the server extracts what matters:

- Novel hypotheses and seed ideas
- Unexpected connections between concepts
- Open questions worth pursuing
- Decisions made and action items

Each insight is confidence-scored (🟢 Seed / 🔴 Developing / 🟡 Strong) and shown to you for review before anything is written to the database. Approved entries are stored alongside your journal captures with full tag support, so AI-extracted insights surface in searches, connection graphs, and synthesis suggestions alongside your handwritten notes.

### AI companions — an independent check on what you already wrote

Three pairs of tools go a step further than search and connections: each runs
an independent AI pass against a page **you've already written by hand**,
then walks you through what it found before anything gets stored. Same shape
every time — **scan → structured dialogue → your approval → a separate
AI-Extracted entry.** Your original page is never rewritten.

- **Synthesis.** `surface_connections` re-derives connections across the RC
  cluster behind a SYN page — blind to what the page itself says — then
  compares its independent read against yours: what you both found, what it
  caught that you missed, what you saw that no tag overlap could have
  surfaced. `commit_distillation` stores what the comparison revealed once
  you approve it, linked to the SYN page with a `distills` edge.
- **Review.** `audit_knowledge_status` checks a claimed Knowledge Status
  (Solid / Mastered) against real evidence still sitting in the journal —
  open questions and uncited insights on that topic. `commit_assessment`
  records the outcome with an `assesses` edge; your REV page's claimed
  status is never rewritten — a real status change only ever happens on a
  future hand-written page.
- **Dream Capture.** `dream_correlation` reports plain co-occurrence between
  dream entries and your waking entries — deliberately labeled
  *co-occurrence, not correlation*, with the window size, match count, and
  base rate always shown, since a small journal can make anything look
  meaningful. `bridge_dream_research` builds on it with a dialogue about
  what a dream's symbols mean to you, and `commit_observation` stores the
  outcome with an `observes` edge — called an *observation*, not an
  inference, because that's what a journal this size can actually support.

Every one of these runs **after** the physical page exists, never before —
running the check first would let the AI perform the thinking the physical
practice exists to force. None has an override flag for that precondition,
and the dialogues themselves are built to ask, not propose: a question makes
you think; a suggested answer makes the AI think, in your place.

**Local by default.** Storage, search, and connections all live in a SQLite
database on your machine — nothing is synced or hosted anywhere. When your AI
assistant reads a journal photo with vision, that image is handled by your
assistant's platform like any other chat attachment; the local Tesseract OCR
path keeps everything on-machine. Optional cloud OCR for bulk imports exists
but is **off unless you explicitly enable it** with your own key.

---

## AI platform support

This server uses **MCP (Model Context Protocol)**, an open standard with growing support across AI platforms and developer tools.

**Currently supported:**
- **Claude Desktop** (free) — full MCP support, recommended for getting started

**Other MCP-compatible clients** (Cursor, VS Code + GitHub Copilot, and others) can connect using the same config — check your client's MCP documentation for setup details.

**Using ChatGPT, Gemini, or another platform?**
Use the `export_captures` tool to dump your knowledge base as Markdown or JSON, then paste it into your AI assistant of choice. Full native MCP support for additional platforms is on the roadmap as the ecosystem grows.

**Protocol compliance:** ksj-mcp runs on the official Python MCP SDK v2.0.0 over the stdio transport. The server is dual-era: it answers the classic `initialize` handshake (negotiated up to protocol revision `2025-11-25`), and a client that opens with the `2026-07-28` per-request envelope gets the modern era, including `server/discover`. Which era is used is the client's choice. Since v3.7.0 the server answers the handshake before it opens your knowledge base, so startup time no longer grows with the size of your journal. Since v3.9.0 the `ksj-mcp` command is a small launcher that answers the handshake from a cache written by the previous run (`handshake-cache.json` in the data directory) while the full server loads, so ksj connects even on a slow or busy machine; the first start after an install or upgrade fills the cache. Set `KSJ_FAST_HANDSHAKE=0` to run the server directly. (MCP is versioned by dated spec release, not semantic version — "MCP SDK v2.0.0" above refers to the SDK package's own version number, not the protocol revision.)

---

## Setup (3 steps)

No OCR software needed — your AI assistant reads the pages. (Want fully
offline OCR too? See [Optional: offline OCR](#optional-offline-ocr-tesseract)
after setup.)

### Step 1 — Install an MCP-compatible AI client

The fastest way to get started is **Claude Desktop** (free at claude.ai/download).

For other MCP clients, consult their documentation for how to register a local MCP server, then use the config in Step 3.

### Step 2 — Install uv and the KSJ server

**uv** is a fast Python package manager used to install and run the KSJ server.

**Install uv:**

| Platform | Command |
|----------|---------|
| **Windows** | `winget install astral-sh.uv` or [download from astral.sh/uv](https://astral.sh/uv) |
| **macOS/Linux** | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

Verify with `uv --version` in a terminal before continuing.

**Install the KSJ server** (run once in a terminal):

```bash
uv tool install --from git+https://github.com/ChavezAILabs/ksj-mcp ksj-mcp
```

This installs `ksj-mcp` as a persistent command on your machine. Git must be installed for this step (Windows: [Git for Windows](https://git-scm.com/download/win)).

Verify with `uv tool list` — it should list `ksj-mcp` with a version number.

**To update later:**
```bash
uv tool install --reinstall --compile-bytecode --from git+https://github.com/ChavezAILabs/ksj-mcp ksj-mcp
```
`--reinstall` matters: a plain `uv tool install` silently does nothing when the
version number hasn't changed. `--compile-bytecode` precompiles the server and
its libraries at install time, so the first launch afterwards isn't slowed by
compiling them (measured ~0.8 s faster on Linux; more on Windows). Fully quit
and reopen your AI client afterwards, then ask it to run `get_version`.

### Step 3 — Register the server

**Claude Desktop config file location:**

| Platform | Path |
|----------|------|
| **Windows** | `%APPDATA%\Claude\claude_desktop_config.json` |
| **macOS/Linux** | `~/.config/claude/claude_desktop_config.json` |

Claude Desktop launches MCP servers with a limited `PATH`, so a bare
`"ksj-mcp"` command often won't resolve even though it works fine in a
terminal — use the full path to the binary `uv tool install` created in
Step 2 instead:

| Platform | Typical binary path |
|----------|------|
| **Windows** | `C:\Users\<you>\.local\bin\ksj-mcp.exe` |
| **macOS/Linux** | `~/.local/bin/ksj-mcp` (expand `~` to the full path, e.g. `/Users/<you>/.local/bin/ksj-mcp`) |

Add the following block (Windows example shown — swap in your macOS/Linux path if applicable):

```json
{
  "mcpServers": {
    "ksj": {
      "command": "C:\\Users\\<you>\\.local\\bin\\ksj-mcp.exe"
    }
  }
}
```

Save and restart your AI client. You should see **ksj** listed in the tools/integrations panel.

### Optional: offline OCR (Tesseract)

Only needed if you want `upload_capture` / `bulk_upload` to read photos fully
on-machine instead of via your assistant's vision. Fair warning: Tesseract
performs poorly on cursive handwriting — printed or very neat text works best.

| Platform | Command |
|----------|---------|
| **Windows** | Download the installer from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki) — check "Add to PATH" during install |
| **macOS** | `brew install tesseract` |
| **Linux** | `sudo apt install tesseract-ocr` |

After installing, restart your AI client so the updated PATH is picked up.

> **Windows note:** If you skip "Add to PATH", the server will still auto-detect Tesseract at the default install location (`C:\Program Files\Tesseract-OCR\`).

### Optional: cloud OCR for bulk imports

**Off by default — nothing leaves your machine unless you turn this on.**

Importing a whole folder of handwritten pages with `bulk_upload` is the one
place local Tesseract really hurts: cursive comes out as noise, page after
page. If you have a large backlog, you can point the server at your own
**Azure Document Intelligence** resource (~9% word error rate on handwriting
vs ~95% for Tesseract):

```json
{
  "mcpServers": {
    "ksj": {
      "command": "C:\\Users\\<you>\\.local\\bin\\ksj-mcp.exe",
      "env": {
        "KSJ_OCR_BACKEND": "azure",
        "KSJ_AZURE_ENDPOINT": "https://<your-resource>.cognitiveservices.azure.com",
        "KSJ_AZURE_KEY": "<your-key>"
      }
    }
  }
}
```

(Use the `command` path from [Step 3](#step-3--register-the-server) for your platform.)

What this means for your data: each uploaded image is sent to **your own**
Azure resource (your subscription, your key, Azure's data terms) for text
extraction. Nothing else is sent anywhere, and your knowledge base stays
local either way. Every upload's output states plainly when cloud OCR is
active. Remove `KSJ_OCR_BACKEND` to return to fully local processing.

For a handful of pages, skip all of this — sharing the photo in chat and
letting your assistant read it is free and just as accurate.

---

## Usage

Once connected, talk to your AI assistant naturally.

**Capturing pages (recommended flow):**
> *[share a photo of the page in chat]* "Read this journal page and add it to my knowledge base"

> "Here's RC-007 — transcribe it, show me what you read, then store it"

**Capturing via local OCR (optional, needs Tesseract):**
> "Upload my journal photo from /Users/me/Desktop/RC-001.jpg"

> "Process all the photos in my /Desktop/journal-scans folder"

**Fixing a bad read:**
> "Capture #12's text is wrong — here's the corrected transcription: …"

**Searching & browsing:**
> "Search my notes for ideas about spaced repetition"

> "Show me everything tagged #machine-learning"

> "What are my open questions about calculus?"

> "Show me everything connected to RC-015"

**Synthesis & review:**
> "Which topics am I ready to synthesize into a SYN page?"

> "Show me my breakthrough timeline"

> "How is my understanding of #linear-algebra progressing?"

> "Run surface_connections on SYN-004" → independent scan of the RC cluster
> behind it, then a dialogue comparing what it found against what you wrote

> "Audit REV-008 against the evidence" → checks its claimed Knowledge Status
> against open questions and uncited insights still sitting on that topic

**Dream Capture:**
> "What symbols and themes keep appearing in my dreams?"

> "Show me all my dream entries from this month"

> "Does #flying show up near any of my waking entries?" → plain co-occurrence
> counts, always with the window, match count, and base rate shown

> "Bridge DC-005 to my research" → checks for cross-domain echo, then asks
> what the dream's symbols mean to you (never proposes an interpretation)

**Export & health:**
> "Export all captures tagged #ai as Markdown"

> "Generate a study deck from my open questions"

> "How's my journal practice looking?"

> "Give me a browsable view of my whole knowledge base" → writes a self-contained
> `.html` file — timeline (with date-range search and a 25-at-a-time load-more),
> tag/entity index, per-capture connection lists, and an ego-centric connection
> graph (click a tag cluster or a capture to see its local neighborhood, click
> any neighbor to recenter) — you can open in any browser, no server or install
> required. Both the in-page **← Back** button and your browser's Back button
> step back through the views you visited, and a reload reopens the same view.

**Wild Art & loose captures:**
> *[share a photo of a napkin sketch]* "Add this napkin sketch as a loose capture"

> "What's waiting in Wild Art?" → `list_by_tag(entry_type="WA")`

> "WA-003 is actually RC-021 — file it" → `promote_capture`

> "SYN-006 develops the napkin idea in WA-004 — link them"

---

## Available tools

37 tools. All 36 tools that existed at v3.6.0 were individually exercised (real-data and bad-input cases) as part of the v3.6.0 ship-readiness pass. One scaling issue was found and fixed during the pass: `export_study_deck` on a very large knowledge base could join far too many connected insights into a single flashcard — now ranked by connection strength and capped.

### Journal tools

| Tool | What it does |
|------|-------------|
| `get_version` | Report the running ksj-mcp, mcp, pydantic, and Python versions — confirms an install or upgrade actually took effect |
| `manual_capture` | Store a page your assistant transcribed with vision — the primary capture path. Never rejects: a page it can't file lands in Wild Art. Also takes 3-D isometric pages (`ISO-001`) and loose captures (`wa_reason="loose_capture"`) |
| `upload_capture` | OCR a journal photo locally (Tesseract), parse the template, store it, highlight strongest connection — never rejects (see Wild Art) |
| `promote_capture` | File a Wild Art entry under its proper type (RC / SYN / REV / DC / ISO). Refuses an occupied page ID instead of overwriting or renumbering; keeps the WA history |
| `correct_ocr` | Replace a stored capture's text with a corrected transcription — re-parses tags and connections, preserves the original |
| `identify_capture` | Assign or fix a stored capture's template ID (for a Wild Art entry it runs `promote_capture`) |
| `bulk_upload` | Process a whole folder of photos at once (local OCR) — pages it can't file are kept as Wild Art, not skipped |
| `set_volume` | Multiple journals: set which book new captures go into and which books search sees |
| `assert_entity` | Link a named entity (person, place, work, dream symbol) to a capture |
| `assert_connection` | Assert that one capture supersedes / refutes / narrows / supports / distills / assesses / observes / develops another (`develops`: a journal entry that works out an idea first scribbled in a loose capture) — superseded claims are kept in history but leave current search |
| `rebuild_connections` | Re-derive the connection graph from current tags and text (asserted edges are never touched) |
| `find_path` | Shortest chain of connections between two captures |
| `neighborhood` | Everything within N hops of a capture — its local knowledge cluster |
| `lint` | Health check: orphan captures, un-closed superseded claims, unresolved contradictions, stale open questions, fragmented tags |
| `export_backup` | Full knowledge base to a versioned JSONL file ([format doc](docs/EXPORT_FORMAT.md)) |
| `import_backup` | Restore a JSONL backup — additive, nothing overwritten |
| `export_html` | Self-contained, offline HTML view — timeline with date search and load-more, tag/entity index, per-capture connection lists, and an ego-centric connection graph; in-page and browser Back both step through views; footer records versions, export time, and counts. Opens in any browser |
| `search_captures` | Full-text search with optional tag, date, entry type, and Wild Art reason filters |
| `list_by_tag` | Browse captures by tag or prefix — or by entry type / Wild Art reason, e.g. the whole Wild Art queue |
| `find_connections` | Show tag-overlap and `@`-reference connections for a capture |
| `get_stats` | Overview: counts, top tags, open questions, insights, date range, Wild Art split into "needs attention" and loose captures |
| `export_captures` | Dump your knowledge base as Markdown or JSON |
| `suggest_synthesis` | Find RC topic clusters ready to become a SYN entry |
| `surface_connections` | Independently scan the RC cluster behind a SYN page you've already written, then run a structured comparison dialogue — runs after the page exists, never before; no DB write |
| `commit_distillation` | Store the confirmed outcome of a `surface_connections` dialogue as an AIEX entry, linked to its SYN page with an asserted `distills` edge |
| `export_study_deck` | Export `?` questions as a portable CSV study deck (Anki, Quizlet, Notion, etc.) |
| `journal_health` | KPI dashboard + coaching: velocity, synthesis ratio, review cadence, open questions |
| `get_breakthroughs` | All SYN entries chronologically — your complete breakthrough timeline |
| `dream_patterns` | Recurring symbols, emotions, motifs, and themes across DC pages |
| `dream_correlation` | Co-occurrence between DC entries and RC/REV entries sharing a tag, within a day window — descriptive only: always reports the window, match count, and base rate, never claims "correlation" or significance |
| `knowledge_progress` | Track Needs Work → Solid → Mastered progression from REV entries |
| `audit_knowledge_status` | Independently check a REV page's claimed status against evidence (open questions, uncited insights), then run a structured dialogue over anything that doesn't line up — runs after the page exists, never before; no DB write |
| `commit_assessment` | Store the confirmed outcome of an `audit_knowledge_status` dialogue as an AIEX entry, linked to its REV page with an asserted `assesses` edge — never changes the REV page's own claimed status |
| `bridge_dream_research` | Independently check a DC page for cross-domain echo (via `dream_correlation`) and prepare a dialogue over what its symbols mean to you — runs after the page exists, never before; no DB write |
| `commit_observation` | Store the confirmed outcome of a `bridge_dream_research` dialogue as an AIEX entry, linked to its DC page with an asserted `observes` edge — never changes the DC page's own dream narrative |

### AI session tools

| Tool | What it does |
|------|-------------|
| `extract_insights` | Prepare an AI research session for insight extraction — loads knowledge-base context, no DB write |
| `commit_aiex` | Store the reviewed, confirmed insights as AIEX entries after your approval |

---

## Schema tag system

Use these prefixes anywhere on your journal pages — the server extracts them automatically.

**RC, SYN, REV pages:**

| Prefix | Meaning | Example |
|--------|---------|---------|
| `#` | Topic / domain | `#machine-learning` |
| `@` | Source / reference | `@RC-012` |
| `!` | Priority / urgency | `!deadline` |
| `?` | Open question | `?why-does-this-work` |
| `$` | Key insight | `$breakthrough` |
| `A→B` | Cause / effect | `study→retention` |

**DC (Dream Capture) pages** use a dream-specific variant:

| Prefix | Meaning | Example |
|--------|---------|---------|
| `#` | Dream theme | `#flying` |
| `@` | Symbol or character | `@the-old-house` |
| `!` | Recurring motif | `!falling` |
| `*` | Sensory detail | `*cold-wind` |

Three things the server does with these automatically:

- **Roles.** The same character means different things on DC pages than on
  RC/SYN/REV (`!` is priority on RC, a recurring motif on DC). The server
  stores the *meaning* alongside the character, so browsing by tag can
  distinguish them — ask for "priority items" vs "dream motifs".
- **Entities.** An `@` value that isn't a template ID (`@Veronica`,
  `@the-old-house`) becomes a named entity — searchable across every capture
  and every journal volume. Dream symbols and story characters are the same
  kind of object.
- **Tag bubbles.** Anything written inside the printed tag bubbles counts as
  a tag, with or without the `#`. `DOG MAN`, `Dog-Man`, and `DOG-MAN` all
  normalize to the same tag.

---

## Wild Art: nothing is ever rejected

Named for the L.A. Times "Wild Art" feature: the best slice-of-life shots that
belonged to no assignment but still ran in the paper.

Any page the server can't file normally is stored as a **Wild Art (WA)**
entry (`WA-001`, `WA-002`, …) instead of being rejected. Its text, tags,
photo, and connections are kept exactly as for any other page, and the reason
is recorded:

| Reason | When |
|--------|------|
| `id_conflict` | The page ID is already taken in that volume. The existing page is untouched, and the WA entry records the ID it was read as and which capture holds it |
| `unrecognized_template` | No template ID could be read |
| `ocr_low_confidence` | The ID was only read loosely from a low-confidence OCR pass |
| `validation_error` | E.g. an explicit template ID that doesn't parse |
| `loose_capture` | On purpose: anything off-journal, like a napkin sketch, sticky note, whiteboard photo, or the back of a receipt. No volume or page ID needed |
| `manual` / `other` | Filed as Wild Art by hand / anything else |

- **Review:** `list_by_tag(entry_type="WA")`, optionally with `wa_reason=…`.
  `get_stats` and `journal_health` report real failures ("needs attention")
  separately from loose captures ("no action needed").
- **File it:** `promote_capture(capture_id, "RC", "RC-021")` moves it to its
  proper type and keeps `promoted_from` and the original reason. If the page ID
  is already taken, nothing changes: you choose a different ID or volume. IDs
  are never renumbered automatically.
- **Link a scribble:** when a journal entry develops a loose capture's idea,
  `assert_connection(entry, scribble, "develops")` links them and keeps the
  scribble as the source.

**3-D isometric pages** (not dot-grid) have their own type, **ISO**
(`ISO-001`, …). Store them with `manual_capture(template_id="ISO-001")`,
transcribing the subject line, labels, and notes around the drawing. They
show up under their own type in search, Index, and the Timeline filters.

---

## Multiple journals (volumes)

Finished a journal and started a second one? The new book starts over at
RC-001 — that's expected. Each physical journal is a **volume**, and volume 2
continues volume 1's knowledge base: search spans all volumes and
cross-volume connections are normal.

When you start a new book, say so once:

> "I'm starting my second journal" → the assistant runs `set_volume(current_volume=2)`

Or write the volume on the page itself (e.g. `V2` next to the template ID),
or pass `volume=2` on a single upload. If an upload collides with an existing
page ID, it's kept as Wild Art (`id_conflict`) and the assistant asks what it is:
a page from a new journal (`promote_capture(…, volume=2)`), a misread ID
(`promote_capture` with the right ID), or a cleaner re-capture of the same page
(re-upload with `force=True`). Nothing is ever silently overwritten, and a page
is never filed into the wrong volume to get around a conflict.

---

## Troubleshooting

**"Tesseract OCR is not installed"**
You called `upload_capture`/`bulk_upload`, which need the optional local OCR engine. Either install Tesseract ([Optional: offline OCR](#optional-offline-ocr-tesseract)) and restart your AI client — or skip it entirely: share the photo in chat and ask your assistant to read and store the page instead.

**"Stored as Wild Art … unrecognized_template"** (or UNIDENTIFIED, for pages stored before v3.7)
The template ID couldn't be read from the photo, but the page and its text were stored anyway, so nothing is lost. Tell your assistant the correct ID ("that's RC-007") and it will file it with `promote_capture` (or `identify_capture` for a pre-3.7 UNIDENTIFIED page). Sloppy or unpadded IDs (`RC-7`, `RC-OO2`, a stray letter after the number) are read automatically with a confirmation note.

**OCR got the text wrong**
Ask your assistant to fix it with `correct_ocr` — give it the capture number and the corrected text. The original read is preserved, and tags and connections are rebuilt from the correction.

**"Stored as Wild Art … id_conflict — RC-001 already exists"**
The page ID is already taken, so the new page was kept as Wild Art and the original is untouched. If it's a cleaner retake of the same page and you want it to replace the original, upload with `force=True`:
> "Upload /path/to/RC-001.jpg with force=True"

If it's a page from a new journal, or its ID was misread, file it with `promote_capture` instead.

**"Server transport closed unexpectedly" / server not starting**
Run `uv tool list` in a terminal — it should list `ksj-mcp` with a version number. If it's missing, re-run the install command from Step 2. If it's installed, the issue is likely the Claude Desktop config — double-check it is valid JSON and that `command` is the **full path** to the `ksj-mcp` binary (see [Step 3](#step-3--register-the-server)), not just `"ksj-mcp"`.

**Server not appearing in tools panel**
Confirm `uv tool list` shows `ksj-mcp` installed, verify the config file is valid JSON, and restart Claude Desktop after saving any config changes. Once it's connected, ask your assistant to use the `get_version` tool — that confirms the server is actually running and reachable, not just installed.

---

## Data location

All your captures are stored locally in `~/.ksj-mcp/`:

| Platform | Path |
|----------|------|
| **Windows** | `C:\Users\<you>\.ksj-mcp\` |
| **macOS/Linux** | `~/.ksj-mcp/` |

**Files:**
```
~/.ksj-mcp/captures.db     (SQLite database — all your captures and tags)
~/.ksj-mcp/images/         (copies of uploaded journal photos)
```

Your data is never sent anywhere and persists across updates. Schema
upgrades run automatically in the background right after the server starts,
and tools wait for them to finish. Before a schema change, your database is
backed up in the same folder: to `captures.db.bak-v3` before the first 3.0
start, and to `captures.db.bak-v37` before the first 3.7 start.

**Custom location:** Set the `KSJ_DATA_DIR` environment variable in your config to store data elsewhere:

```json
{
  "mcpServers": {
    "ksj": {
      "command": "C:\\Users\\<you>\\.local\\bin\\ksj-mcp.exe",
      "env": {
        "KSJ_DATA_DIR": "C:\\Users\\you\\Documents\\ksj-data"
      }
    }
  }
}
```

(Use the `command` path from [Step 3](#step-3--register-the-server) for your platform.)

---

## License

MIT — free to use, modify, and share.

Created by **Chavez AI Labs LLC**
paul@chavezailabs.com

**Get the journal:** [Knowledge Synthesis Journal v2.0](https://www.amazon.com/dp/B0GPW5WBZL) (Amazon)
