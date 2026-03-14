# KSJ MCP Server

**Knowledge Synthesis Journal v2.0 — AI companion**

Turn your handwritten journal photos into a searchable, AI-powered knowledge base — privately, on your own machine.

> "Works great on paper. Magical with AI."

**Get the journal:** [Knowledge Synthesis Journal v2.0 on Amazon](https://www.amazon.com/dp/B0GPW5WBZL)

---

## What it does

The KSJ MCP server connects your knowledge — handwritten or digital — to an AI assistant via the **Model Context Protocol (MCP)** — an open standard for linking AI models to local tools and data.

### Physical journal → knowledge base

Photograph a journal page, upload it, and your AI assistant can:

- Search across everything you've ever written
- Find connections between ideas (shared tags, `@` references)
- Surface your open questions, key insights, and breakthroughs
- Export your knowledge base as Markdown or JSON

### AI research sessions → structured insights

Spend an hour going deep on a topic with an AI assistant and most of that thinking vanishes when the chat ends. `extract_ai_insights` fixes that — paste or pipe a session transcript and the server extracts what matters:

- Novel hypotheses and seed ideas
- Unexpected connections between concepts
- Open questions worth pursuing
- Decisions made and action items

Each insight is confidence-scored (🟢 Seed / 🔴 Developing / 🟡 Strong) and shown to you for review before anything is written to the database. Approved entries are stored alongside your journal captures with full tag support, so AI-extracted insights surface in searches, connection graphs, and synthesis suggestions alongside your handwritten notes.

**All processing is local.** Your notes stay on your machine.

---

## AI Platform Support

This server uses **MCP (Model Context Protocol)**, an open standard with growing support across AI platforms and developer tools.

**Currently supported:**
- **Claude Desktop** (free) — full MCP support, recommended for getting started

**Other MCP-compatible clients** (Cursor, VS Code + GitHub Copilot, and others) can connect using the same config — check your client's MCP documentation for setup details.

**Using ChatGPT, Gemini, or another platform?**
Use the `export_captures` tool to dump your knowledge base as Markdown or JSON, then paste it into your AI assistant of choice. Full native MCP support for additional platforms is on the roadmap as the ecosystem grows.

---

## Setup (4 steps)

### Step 1 — Install an MCP-compatible AI client

The fastest way to get started is **Claude Desktop** (free at claude.ai/download).

For other MCP clients, consult their documentation for how to register a local MCP server, then use the config in Step 4.

### Step 2 — Install Tesseract OCR

Tesseract reads the text from your journal photos. It must be installed separately.

| Platform | Command |
|----------|---------|
| **Windows** | Download the installer from [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki) — check "Add to PATH" during install |
| **macOS** | `brew install tesseract` |
| **Linux** | `sudo apt install tesseract-ocr` |

After installing, restart your AI client so the updated PATH is picked up.

> **Windows note:** If you skip "Add to PATH", the server will still auto-detect Tesseract at the default install location (`C:\Program Files\Tesseract-OCR\`). Adding to PATH is recommended but not required.

### Step 3 — Install uv and the KSJ server

**uv** is a fast Python package manager used to install and run the KSJ server.

**Install uv:**

| Platform | Command |
|----------|---------|
| **Windows** | `winget install astral-sh.uv` or [download from astral.sh/uv](https://astral.sh/uv) |
| **macOS/Linux** | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

Verify with `uv --version` in a terminal before continuing.

**Install the KSJ server** (run once in a terminal):

```
uv tool install --from git+https://github.com/ChavezAILabs/ksj-mcp ksj-mcp
```

This installs `ksj-mcp` as a persistent command on your machine. Git must be installed for this step (Windows: [Git for Windows](https://git-scm.com/download/win)).

Verify with `ksj-mcp --help` — if it shows a help message, the install worked.

**To update later:**
```
uv tool upgrade ksj-mcp
```

### Step 4 — Register the server

**Claude Desktop config file location:**

| Platform | Path |
|----------|------|
| **Windows** | `%APPDATA%\Claude\claude_desktop_config.json` |
| **macOS/Linux** | `~/.config/claude/claude_desktop_config.json` |

Add the following block:

```json
{
  "mcpServers": {
    "ksj": {
      "command": "ksj-mcp"
    }
  }
}
```

Save and restart your AI client. You should see **ksj** listed in the tools/integrations panel.

---

## Usage

Once connected, talk to your AI assistant naturally.

**Uploading:**
> "Upload my journal photo from /Users/me/Desktop/RC-001.jpg"

> "Process all the photos in my /Desktop/journal-scans folder"

**Searching & browsing:**
> "Search my notes for ideas about spaced repetition"

> "Show me everything tagged #machine-learning"

> "What are my open questions about calculus?"

> "Show me everything connected to RC-015"

**Synthesis & review:**
> "Which topics am I ready to synthesize into a SYN page?"

> "Show me my breakthrough timeline"

> "How is my understanding of #linear-algebra progressing?"

**Dream Capture:**
> "What symbols and themes keep appearing in my dreams?"

> "Show me all my dream entries from this month"

**Export & health:**
> "Export all captures tagged #ai as Markdown"

> "Generate a study deck from my open questions"

> "How's my journal practice looking?"

---

## Available tools

### Journal tools

| Tool | What it does |
|------|-------------|
| `upload_capture` | OCR a journal photo, parse the template, store it, highlight strongest connection |
| `bulk_upload` | Process a whole folder of photos at once |
| `search_captures` | Full-text search with optional tag and date filters |
| `list_by_tag` | Browse all captures with a given tag or prefix |
| `find_connections` | Show tag-overlap and `@`-reference connections for a capture |
| `get_stats` | Overview: counts, top tags, open questions, insights, date range |
| `export_captures` | Dump your knowledge base as Markdown or JSON |
| `suggest_synthesis` | Find RC topic clusters ready to become a SYN entry |
| `export_study_deck` | Export `?` questions as a portable CSV study deck (Anki, Quizlet, Notion, etc.) |
| `journal_health` | KPI dashboard + coaching: velocity, synthesis ratio, review cadence, open questions |
| `get_breakthroughs` | All SYN entries chronologically — your complete breakthrough timeline |
| `dream_patterns` | Recurring symbols, emotions, motifs, and themes across DC pages |
| `knowledge_progress` | Track Needs Work → Solid → Mastered progression from REV entries |

### AI session tools

| Tool | What it does |
|------|-------------|
| `extract_ai_insights` | Extract confidence-scored insights from an AI research session transcript — with user review before any DB write |

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

---

## Troubleshooting

**"Tesseract OCR is not installed"**
Install Tesseract (Step 2 above) and restart your AI client.

**"Could not detect a template ID"**
Make sure the template number (RC-001, SYN-001, etc.) is clearly visible in the photo. Try better lighting or a closer shot.

**"RC-001 already exists in your knowledge base"**
You're re-uploading a page that's already stored. To replace it with the new photo (e.g. after a cleaner retake), ask your AI assistant to upload with `force=True`:
> "Upload /path/to/RC-001.jpg with force=True"

**"Server transport closed unexpectedly" / server not starting**
Run `ksj-mcp --help` in a terminal. If that works, the issue is with the Claude Desktop config — double-check it is valid JSON with `"command": "ksj-mcp"`. If `ksj-mcp` is not found, re-run the install command from Step 3.

**Server not appearing in tools panel**
Confirm `ksj-mcp --help` works in a terminal, verify the config file is valid JSON, and restart Claude Desktop after saving any config changes.

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

Your data is never sent anywhere and persists across updates.

**Custom location:** Set the `KSJ_DATA_DIR` environment variable in your config to store data elsewhere:

```json
{
  "mcpServers": {
    "ksj": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/ChavezAILabs/ksj-mcp", "ksj-mcp"],
      "env": {
        "KSJ_DATA_DIR": "C:\\Users\\you\\Documents\\ksj-data"
      }
    }
  }
}
```

---

## License

MIT — free to use, modify, and share.

Created by **Chavez AI Labs LLC**
paul@chavezailabs.com
*"Personal knowledge operating system for the AI age"*

**Get the journal:** [Knowledge Synthesis Journal v2.0](https://www.amazon.com/dp/B0GPW5WBZL) (Amazon)
