"""
Self-contained HTML view of the knowledge base (§2.4a).

Overview modes ship in stages, gated only by the data each needs:
  Mode 1 — Timeline: chronological captures, client-side search + filters.
  Mode 2 — Index: tags grouped by semantic role, plus the entity register.
(Modes 3 — connection lists — and 4 — graph — come later.)

One .html file, all data inlined as JSON, vanilla JS/CSS, no network access,
no build step, opens in any browser. Everything user-authored is escaped
client-side; the embedded JSON escapes '</' so page text can never terminate
the script block.
"""

import json
import sqlite3
from datetime import datetime, timezone

from .database import get_active_volumes, get_current_volume


def collect_view_data(con: sqlite3.Connection) -> dict:
    """Everything modes 1–2 need, in one JSON-serializable dict."""
    captures = []
    for r in con.execute(
        """SELECT id, type, template_id, page_suffix, volume, summary, confidence,
                  content_json, raw_ocr, corrected_ocr, source, valid_until, created_at
           FROM captures ORDER BY created_at DESC"""
    ).fetchall():
        tags = [
            {"prefix": t["prefix"], "value": t["value"],
             "display": t["display"] or t["value"], "role": t["role"] or ""}
            for t in con.execute(
                "SELECT prefix, value, display, role FROM tags WHERE capture_id=?",
                (r["id"],),
            ).fetchall()
        ]
        captures.append({
            "id": r["id"],
            "type": r["type"],
            "template_id": r["template_id"] or "",
            "page_suffix": r["page_suffix"] or "",
            "volume": r["volume"],
            "summary": r["summary"] or "",
            "confidence": r["confidence"],
            "fields": {
                k: v for k, v in json.loads(r["content_json"]).items()
                if v and k != "tags_raw"
            },
            "text": r["corrected_ocr"] or r["raw_ocr"] or "",
            "corrected": bool(r["corrected_ocr"]),
            "source": r["source"],
            "superseded": bool(r["valid_until"]),
            "date": (r["created_at"] or "")[:10],
            "tags": tags,
        })

    entities = []
    for e in con.execute(
        "SELECT id, name, kind FROM entities ORDER BY name COLLATE NOCASE"
    ).fetchall():
        cap_ids = [
            row["capture_id"] for row in con.execute(
                "SELECT capture_id FROM capture_entities WHERE entity_id=?", (e["id"],)
            ).fetchall()
        ]
        entities.append({
            "id": e["id"], "name": e["name"], "kind": e["kind"],
            "capture_ids": cap_ids,
        })

    vols = get_active_volumes(con)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_volumes": vols,          # None = all
        "current_volume": get_current_volume(con),
        "captures": captures,
        "entities": entities,
    }


_ROLE_SECTIONS = [
    ("topic",    "Topics"),
    ("theme",    "Dream Themes"),
    ("question", "Open Questions"),
    ("insight",  "Key Insights"),
    ("priority", "Priorities"),
    ("motif",    "Recurring Motifs (writer-flagged)"),
    ("sensory",  "Sensory Details"),
    ("causal",   "Cause → Effect"),
]

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>KSJ Knowledge Base</title>
<style>
:root {
  --bg: #f7f7f5; --card: #ffffff; --ink: #1c2427; --muted: #67737a;
  --line: #e2e2de; --accent: #0e7c92; --accent-soft: #e3f2f5; --chip: #eef0ee;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #10181b; --card: #182327; --ink: #e4e9eb; --muted: #8fa0a8;
    --line: #253238; --accent: #4cc3d9; --accent-soft: #143540; --chip: #223035;
  }
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 880px; margin: 0 auto; padding: 24px 16px 80px; }
header h1 { font-size: 1.35rem; letter-spacing: .01em; }
header .meta { color: var(--muted); font-size: .82rem; margin-top: 4px; }
.scope { margin-top: 6px; font-size: .82rem; color: var(--accent); }
.tabs { display: flex; gap: 8px; margin: 18px 0 12px; }
.tabs button { border: 1px solid var(--line); background: var(--card); color: var(--ink);
  padding: 7px 18px; border-radius: 999px; cursor: pointer; font-size: .9rem; }
.tabs button.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.toolbar { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px; align-items: center; }
.toolbar input[type=search], .toolbar select { border: 1px solid var(--line);
  background: var(--card); color: var(--ink); padding: 7px 10px; border-radius: 8px;
  font-size: .88rem; }
.toolbar input[type=search] { flex: 1 1 200px; }
.toolbar label { font-size: .82rem; color: var(--muted); display: flex; gap: 5px; align-items: center; }
.activetag { display: none; margin-bottom: 12px; font-size: .86rem; }
.activetag span { background: var(--accent-soft); color: var(--accent);
  padding: 4px 12px; border-radius: 999px; }
.activetag button { border: none; background: none; color: var(--accent);
  cursor: pointer; font-size: .86rem; text-decoration: underline; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px 16px; margin-bottom: 12px; }
.card.superseded { opacity: .55; }
.card .top { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.tid { font-weight: 600; color: var(--accent); }
.badge { font-size: .7rem; padding: 2px 8px; border-radius: 999px;
  background: var(--chip); color: var(--muted); }
.badge.src-ai { background: var(--accent-soft); color: var(--accent); }
.badge.sup { background: #8b2635; color: #fff; }
.date { color: var(--muted); font-size: .8rem; margin-left: auto; }
.summary { margin-top: 6px; }
.chips { margin-top: 8px; display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: .78rem; background: var(--chip); border-radius: 999px;
  padding: 2px 10px; cursor: pointer; border: none; color: var(--ink); }
.chip:hover { background: var(--accent-soft); color: var(--accent); }
details { margin-top: 10px; }
details summary { cursor: pointer; color: var(--muted); font-size: .82rem; }
.fields { margin-top: 8px; }
.fields h4 { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); margin: 10px 0 2px; }
.fields div, .rawtext { white-space: pre-wrap; overflow-wrap: anywhere; font-size: .88rem; }
.rawtext { background: var(--bg); border-radius: 8px; padding: 10px; margin-top: 8px; }
.idx-section { margin-bottom: 26px; }
.idx-section h2 { font-size: 1rem; margin-bottom: 8px; }
.idx-section h2 small { color: var(--muted); font-weight: 400; }
.idx-list { display: flex; flex-wrap: wrap; gap: 8px; }
.idx-item { border: 1px solid var(--line); background: var(--card); border-radius: 8px;
  padding: 6px 12px; cursor: pointer; font-size: .86rem; color: var(--ink); }
.idx-item:hover { border-color: var(--accent); color: var(--accent); }
.idx-item .n { color: var(--muted); font-size: .76rem; margin-left: 6px; }
.idx-item .kind { color: var(--muted); font-size: .74rem; margin-left: 6px; font-style: italic; }
.empty { color: var(--muted); font-style: italic; padding: 24px 0; }
footer { margin-top: 40px; color: var(--muted); font-size: .78rem; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Knowledge Synthesis Journal</h1>
    <div class="meta" id="meta"></div>
    <div class="scope" id="scope"></div>
  </header>
  <div class="tabs">
    <button id="tab-timeline" class="active" onclick="setMode('timeline')">Timeline</button>
    <button id="tab-index" onclick="setMode('index')">Index</button>
  </div>
  <div class="toolbar" id="toolbar">
    <input type="search" id="q" placeholder="Search captures…" oninput="render()">
    <select id="ftype" onchange="render()"><option value="">All types</option></select>
    <select id="fvol" onchange="render()"><option value="">All volumes</option></select>
    <select id="fsrc" onchange="render()">
      <option value="">Journal + AI</option>
      <option value="journal">Journal only</option>
      <option value="ai_extract">AI-extracted only</option>
    </select>
    <label><input type="checkbox" id="fsup" onchange="render()"> show superseded</label>
  </div>
  <div class="activetag" id="activetag"></div>
  <main id="view"></main>
  <footer>Generated by ksj-mcp · local file, no network · data as of <span id="gen"></span></footer>
</div>
<script id="ksj-data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('ksj-data').textContent);
const ROLE_SECTIONS = __ROLE_SECTIONS__;
let state = { mode: 'timeline', tag: null, entity: null };

const esc = s => String(s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = c => c.template_id ? c.template_id + (c.page_suffix || '') : 'UNIDENTIFIED #' + c.id;

function setMode(m) {
  state.mode = m;
  document.getElementById('tab-timeline').classList.toggle('active', m === 'timeline');
  document.getElementById('tab-index').classList.toggle('active', m === 'index');
  document.getElementById('toolbar').style.display = m === 'timeline' ? 'flex' : 'none';
  render();
}

function setTag(value, roleName) {
  state.tag = { value, roleName };
  state.entity = null;
  setMode('timeline');
}
function setEntity(id) {
  state.entity = DATA.entities.find(e => e.id === id) || null;
  state.tag = null;
  setMode('timeline');
}
function clearTag() { state.tag = null; state.entity = null; render(); }

function filtered() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const ftype = document.getElementById('ftype').value;
  const fvol = document.getElementById('fvol').value;
  const fsrc = document.getElementById('fsrc').value;
  const fsup = document.getElementById('fsup').checked;
  return DATA.captures.filter(c => {
    if (!fsup && c.superseded) return false;
    if (ftype && c.type !== ftype) return false;
    if (fvol && String(c.volume) !== fvol) return false;
    if (fsrc && c.source !== fsrc) return false;
    if (state.tag && !c.tags.some(t => t.value === state.tag.value)) return false;
    if (state.entity && !state.entity.capture_ids.includes(c.id)) return false;
    if (q) {
      const hay = (label(c) + ' ' + c.summary + ' ' + c.text + ' '
        + c.tags.map(t => t.prefix + t.value + ' ' + t.display).join(' ')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

function captureCard(c) {
  const badges = [
    `<span class="badge">${esc(c.type)}</span>`,
    `<span class="badge">vol ${c.volume}</span>`,
    c.source === 'ai_extract' ? '<span class="badge src-ai">AI-extracted</span>' : '',
    c.superseded ? '<span class="badge sup">superseded</span>' : '',
  ].join(' ');
  const chips = c.tags.map(t =>
    `<button class="chip" title="${esc(t.role || 'tag')}"
      onclick="setTag('${esc(t.value)}', '${esc(t.role)}')">${esc(t.prefix + t.display)}</button>`
  ).join('');
  const fields = Object.entries(c.fields).map(([k, v]) =>
    `<h4>${esc(k.replace(/_/g, ' '))}</h4><div>${esc(v)}</div>`).join('');
  const textLabel = c.corrected ? 'corrected transcription' : 'raw text';
  return `<article class="card${c.superseded ? ' superseded' : ''}">
    <div class="top"><span class="tid">${esc(label(c))}</span>${badges}
      <span class="date">${esc(c.date)}</span></div>
    <div class="summary">${esc(c.summary) || '<i>(no summary)</i>'}</div>
    <div class="chips">${chips}</div>
    <details><summary>fields &amp; ${textLabel}</summary>
      <div class="fields">${fields || '<i>(no parsed fields)</i>'}</div>
      <div class="rawtext">${esc(c.text) || '(empty)'}</div>
    </details>
  </article>`;
}

function renderTimeline(el) {
  const caps = filtered();
  const at = document.getElementById('activetag');
  if (state.tag) {
    at.style.display = 'block';
    at.innerHTML = `<span>filtered to tag: ${esc(state.tag.value)}` +
      (state.tag.roleName ? ` (${esc(state.tag.roleName)})` : '') +
      `</span> <button onclick="clearTag()">clear</button>`;
  } else if (state.entity) {
    at.style.display = 'block';
    at.innerHTML = `<span>filtered to entity: ${esc(state.entity.name)} (${esc(state.entity.kind)})</span>
      <button onclick="clearTag()">clear</button>`;
  } else { at.style.display = 'none'; }
  el.innerHTML = caps.length
    ? caps.map(captureCard).join('')
    : '<div class="empty">No captures match the current filters.</div>';
}

function renderIndex(el) {
  let html = '';
  const ents = DATA.entities.filter(e => e.capture_ids.length > 0);
  if (ents.length) {
    html += `<div class="idx-section"><h2>Entities <small>· people, places, works, symbols</small></h2>
      <div class="idx-list">` + ents.map(e =>
        `<button class="idx-item" onclick="setEntity(${e.id})">
          ${esc(e.name)}<span class="kind">${esc(e.kind)}</span>
          <span class="n">×${e.capture_ids.length}</span></button>`).join('') +
      '</div></div>';
  }
  for (const [role, title] of ROLE_SECTIONS) {
    const counts = {};
    for (const c of DATA.captures) {
      if (c.superseded) continue;
      for (const t of c.tags) {
        if (t.role !== role) continue;
        (counts[t.value] ||= { display: t.prefix + t.display, n: 0 }).n++;
      }
    }
    const items = Object.entries(counts).sort((a, b) => b[1].n - a[1].n);
    if (!items.length) continue;
    html += `<div class="idx-section"><h2>${esc(title)} <small>· ${items.length}</small></h2>
      <div class="idx-list">` + items.map(([v, d]) =>
        `<button class="idx-item" onclick="setTag('${esc(v)}', '${esc(role)}')">
          ${esc(d.display)}<span class="n">×${d.n}</span></button>`).join('') +
      '</div></div>';
  }
  el.innerHTML = html || '<div class="empty">Nothing indexed yet — upload some captures.</div>';
}

function render() {
  const el = document.getElementById('view');
  if (state.mode === 'timeline') renderTimeline(el); else renderIndex(el);
}

(function init() {
  const caps = DATA.captures;
  document.getElementById('meta').textContent =
    `${caps.length} capture(s) · ${DATA.entities.length} entit(ies) · current writing volume: ${DATA.current_volume}`;
  document.getElementById('gen').textContent = DATA.generated_at.slice(0, 16).replace('T', ' ');
  const scope = document.getElementById('scope');
  scope.textContent = DATA.active_volumes === null
    ? ''
    : `Server read scope is volume(s) ${DATA.active_volumes.join(', ')} — this file contains ALL volumes; use the volume filter below.`;
  const types = [...new Set(caps.map(c => c.type))].sort();
  const ftype = document.getElementById('ftype');
  for (const t of types) ftype.add(new Option(t, t));
  const vols = [...new Set(caps.map(c => c.volume))].sort((a, b) => a - b);
  const fvol = document.getElementById('fvol');
  for (const v of vols) fvol.add(new Option('volume ' + v, String(v)));
  render();
})();
</script>
</body>
</html>
"""


def render_html(data: dict) -> str:
    """Render collected view data into the self-contained HTML page."""
    # '</' must never appear literally inside the JSON script block — page
    # text containing '</script>' would otherwise terminate it.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    sections = json.dumps(_ROLE_SECTIONS)
    return _TEMPLATE.replace("__DATA__", payload).replace("__ROLE_SECTIONS__", sections)
