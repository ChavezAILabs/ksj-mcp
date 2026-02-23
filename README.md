# KSJ MCP Server

**Knowledge Synthesis Journal v2.0 — AI companion**

Turn your handwritten journal photos into a searchable, AI-powered knowledge base — privately, on your own machine.

> "Works great on paper. Magical with AI."

---

## What it does

The KSJ MCP server connects your physical journal to an AI assistant via the **Model Context Protocol (MCP)** — an open standard for linking AI models to local tools and data.

Photograph a journal page, upload it, and your AI assistant can:

- Search across everything you've ever written
- Find connections between ideas (shared tags, `@` references)
- Surface your open questions, key insights, and breakthroughs
- Export your knowledge base as Markdown or JSON

**All processing is local.** No cloud. No subscription. Your notes stay on your machine.

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

After installing, restart your terminal and AI client so the updated PATH is picked up.

### Step 3 — Install uv

`uvx` (used in Step 3) is part of **uv**, a fast Python package manager. Install it once:

| Platform | Command |
|----------|---------|
| **Windows** | `winget install astral-sh.uv` or [download from astral.sh/uv](https://astral.sh/uv) |
| **macOS/Linux** | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

Verify with `uv --version` in a terminal before continuing.

### Step 4 — Register the server

**Claude Desktop config file location:**

| Platform | Path |
|----------|------|
| **Windows** | `%APPDATA%\Claude\claude_desktop_config.json` |
| **macOS/Linux** | `~/.config/claude/claude_desktop_config.json` |

Add the following block (copy exactly — no path to set):

```json
{
  "mcpServers": {
    "ksj": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/ChavezAILabs/ksj-mcp",
        "ksj-mcp"
      ]
    }
  }
}
```

`uvx` downloads and runs the server automatically — nothing else to install.

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

**Server not appearing in tools panel**
Check that `uv` is installed (`uv --version` in a terminal) and that the path in your config file is correct. On Windows, use forward slashes or escaped backslashes in the JSON.

---

## Data location

All your captures are stored locally. The exact path depends on how you run the server:

**Via uvx (recommended install):**

| Platform | Path |
|----------|------|
| **Windows** | `%APPDATA%\uv\tools\ksj-mcp\data\` |
| **macOS/Linux** | `~/.local/share/uv/tools/ksj-mcp/data/` |

**Files:**
```
data/captures.db     (SQLite database — all your captures and tags)
data/images/         (copies of uploaded journal photos)
```

Your data is never sent anywhere. The `data/` directory is `.gitignore`d and stays on your machine.

---

## License

MIT — free to use, modify, and share.

Created by **Chavez AI Labs LLC**
paul@chavezailabs.com
*"Personal knowledge operating system for the AI age"*
