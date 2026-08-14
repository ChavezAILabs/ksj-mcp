"""
Self-contained HTML view of the knowledge base (§2.4a).

Overview modes ship in stages, gated only by the data each needs:
  Mode 1 — Timeline: chronological captures, client-side search + filters.
  Mode 2 — Index: tags grouped by semantic role, plus the entity register.
  Mode 3 — Connections: every capture card lists its typed, directional
           edges as clickable links (no spatial layout, just adjacency).
  Mode 4 — Graph: an ego-centric local graph, two layers. Landing view is
           a cluster/tag overview — captures grouped into bubbles by tag,
           theme, or entity, bubble size = capture count; this stays
           legible at any database size because it never renders more than
           one screen of aggregates. Clicking a bubble (or a capture from
           Timeline/Index/search) drills into a fixed radial layout: the
           clicked item sits centered, its direct connections (or, for a
           cluster, its member captures) arranged evenly around it.
           Clicking any neighbor recenters the view on it and redraws; a
           Back button retraces the history stack, bottoming out at the
           landing view. High-degree nodes are capped (a "+N more" node
           expands the ring on click) rather than degrading into overlap.
           No physics simulation and no rotation — this replaced an
           earlier rotating-globe design that rendered the whole dataset
           at once.

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
    """Everything modes 1–4 need, in one JSON-serializable dict."""
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

    # One row per edge (not per capture-perspective) — modes 3 and 4 both
    # derive what they need from this single list client-side, so the
    # payload never duplicates edge data per endpoint.
    edges = [
        {
            "source": row["source_id"],
            "target": row["target_id"],
            "type": row["type"],
            "relation": row["relation"],
            "strength": round(row["strength"], 2) if row["strength"] is not None else 1.0,
            "note": row["note"],
        }
        for row in con.execute(
            "SELECT source_id, target_id, type, relation, strength, note "
            "FROM connections ORDER BY id"
        ).fetchall()
    ]

    vols = get_active_volumes(con)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_volumes": vols,          # None = all
        "current_volume": get_current_volume(con),
        "captures": captures,
        "entities": entities,
        "edges": edges,
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
  --type-rc: #b7791f; --type-syn: #7c5cbf; --type-rev: #4a6f96; --type-dc: #96477c;
  --edge-negative: #b23a4a; --edge-positive: #3f8f6b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #10181b; --card: #182327; --ink: #e4e9eb; --muted: #8fa0a8;
    --line: #253238; --accent: #4cc3d9; --accent-soft: #143540; --chip: #223035;
    --type-rc: #e0ab52; --type-syn: #a68ee6; --type-rev: #82a8cf; --type-dc: #d391b8;
    --edge-negative: #e08795; --edge-positive: #6bc79a;
  }
}
:root[data-theme="dark"] {
  --bg: #10181b; --card: #182327; --ink: #e4e9eb; --muted: #8fa0a8;
  --line: #253238; --accent: #4cc3d9; --accent-soft: #143540; --chip: #223035;
  --type-rc: #e0ab52; --type-syn: #a68ee6; --type-rev: #82a8cf; --type-dc: #d391b8;
  --edge-negative: #e08795; --edge-positive: #6bc79a;
}
:root[data-theme="light"] {
  --bg: #f7f7f5; --card: #ffffff; --ink: #1c2427; --muted: #67737a;
  --line: #e2e2de; --accent: #0e7c92; --accent-soft: #e3f2f5; --chip: #eef0ee;
  --type-rc: #b7791f; --type-syn: #7c5cbf; --type-rev: #4a6f96; --type-dc: #96477c;
  --edge-negative: #b23a4a; --edge-positive: #3f8f6b;
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 960px; margin: 0 auto; padding: 24px 16px 80px; }
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
.toolbar label { font-size: .82rem; color: var(--muted); display: flex; gap: 6px; align-items: center; }
.btn { border: 1px solid var(--line); background: var(--card); color: var(--ink);
  padding: 7px 14px; border-radius: 8px; cursor: pointer; font-size: .85rem; }
.btn:hover { border-color: var(--accent); color: var(--accent); }
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
.chip-graph { background: none; border: 1px solid var(--line); color: var(--muted); }
.chip-graph:hover { background: var(--accent-soft); color: var(--accent); border-color: var(--accent); }
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
.idx-item { display: inline-flex; align-items: center; border: 1px solid var(--line);
  background: var(--card); border-radius: 8px; overflow: hidden; font-size: .86rem; }
.idx-item:hover { border-color: var(--accent); }
.idx-main { border: none; background: none; color: var(--ink); padding: 6px 10px;
  cursor: pointer; font-size: inherit; }
.idx-main:hover { color: var(--accent); }
.idx-graph { border: none; border-left: 1px solid var(--line); background: none;
  color: var(--muted); padding: 6px 9px; cursor: pointer; font-size: .8rem; }
.idx-graph:hover { color: var(--accent); background: var(--accent-soft); }
.idx-item .n { color: var(--muted); font-size: .76rem; margin-left: 6px; }
.idx-item .kind { color: var(--muted); font-size: .74rem; margin-left: 6px; font-style: italic; }
.empty { color: var(--muted); font-style: italic; padding: 24px 0; }
.load-more { text-align: center; padding: 10px 0 24px; }
footer { margin-top: 40px; color: var(--muted); font-size: .78rem; }

/* mode 3 — connections */
.connections { margin-top: 10px; }
.connections h4 { font-size: .72rem; text-transform: uppercase; letter-spacing: .05em;
  color: var(--muted); margin-bottom: 6px; }
.conn-row { display: flex; align-items: baseline; gap: 6px; width: 100%; text-align: left;
  background: none; border: none; border-top: 1px solid var(--line); padding: 6px 0;
  cursor: pointer; color: var(--ink); font-size: .84rem; }
.conn-row:first-child { border-top: none; }
.conn-row:hover { color: var(--accent); }
.conn-arrow { color: var(--muted); }
.conn-verb { color: var(--muted); font-style: italic; white-space: nowrap; }
.conn-target { font-weight: 600; white-space: nowrap; }
.conn-summary { color: var(--muted); overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; flex: 1; }
.conn-note { font-size: .78rem; color: var(--muted); padding-left: 20px; margin-bottom: 4px; }
.conn-more { font-size: .78rem; color: var(--muted); padding-top: 6px; }
.conn-empty { font-size: .82rem; color: var(--muted); font-style: italic; }
@keyframes ksjFlash { 0% { box-shadow: 0 0 0 3px var(--accent); } 100% { box-shadow: 0 0 0 3px transparent; } }
.card.flash { animation: ksjFlash 1.1s ease-out; }
@media (prefers-reduced-motion: reduce) {
  .card.flash { animation: none; box-shadow: 0 0 0 3px var(--accent); }
}

/* mode 4 — graph */
.legend { display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
  font-size: .76rem; color: var(--muted); margin: 10px 0 0; }
.legend .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
  margin-right: 4px; vertical-align: middle; }
.dot.t-RC { background: var(--type-rc); }
.dot.t-SYN { background: var(--type-syn); }
.dot.t-REV { background: var(--type-rev); }
.dot.t-DC { background: var(--type-dc); }
.dot.t-AIEX { background: var(--accent); }
#view-graph { position: relative; }
.graph-caption { color: var(--muted); font-size: .8rem; line-height: 1.5; margin-bottom: 10px; }
.graph-caption strong { color: var(--ink); }
.graph-back { margin-bottom: 10px; }
.bubbles { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
.bubble { border: 1px solid var(--line); background: var(--card); border-radius: 999px;
  cursor: pointer; color: var(--ink); display: flex; flex-direction: column;
  align-items: center; justify-content: center; text-align: center; padding: 6px;
  overflow: hidden; }
.bubble:hover { border-color: var(--accent); color: var(--accent); }
.bubble .bn { font-size: .82rem; overflow-wrap: anywhere; }
.bubble .bc { font-size: .72rem; color: var(--muted); }
#graph-svg { width: 100%; height: 60vh; min-height: 420px; display: block;
  border: 1px solid var(--line); border-radius: 12px; background: var(--card); }
.arrowhead { fill: var(--muted); }
.ego-node { cursor: pointer; }
.ego-node circle.body { stroke: var(--card); stroke-width: 1.5; }
.ego-node.t-RC circle.body { fill: var(--type-rc); }
.ego-node.t-SYN circle.body { fill: var(--type-syn); }
.ego-node.t-REV circle.body { fill: var(--type-rev); }
.ego-node.t-DC circle.body { fill: var(--type-dc); }
.ego-node.t-AIEX circle.body { fill: var(--accent); }
.ego-node.cluster circle.body { fill: var(--accent); }
.ego-node.more { cursor: pointer; }
.ego-node.more circle.body { fill: var(--chip); stroke: var(--muted); }
.ego-node.superseded circle.body { opacity: .5; stroke-dasharray: 2 2; }
.ego-node.src-ai circle.body { stroke: var(--accent); stroke-width: 2; }
.ego-node:hover circle.body { stroke: var(--ink); stroke-width: 2.5; }
.ego-label { font-size: 10px; color: var(--ink); text-align: center; line-height: 1.25;
  overflow: hidden; overflow-wrap: anywhere; pointer-events: none; font-family: inherit; }
.ego-edge { stroke: var(--muted); stroke-opacity: .5; stroke-width: 1.4; fill: none; }
.ego-edge.e-reference { stroke: var(--accent); stroke-opacity: .85; stroke-width: 1.8; }
.ego-edge.e-asserted { stroke-width: 2; }
.ego-edge.e-asserted.e-rel-supersedes { stroke: var(--edge-negative); stroke-dasharray: 6 3; }
.ego-edge.e-asserted.e-rel-refutes { stroke: var(--edge-negative); stroke-dasharray: 2 3; }
.ego-edge.e-asserted.e-rel-supports { stroke: var(--edge-positive); }
.ego-edge.e-entity_overlap { stroke: var(--accent); stroke-opacity: .5; }
.ego-edge.e-tag_overlap { stroke: var(--muted); stroke-opacity: .4; }
.ego-panel { text-align: center; margin-top: 12px; }
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
    <button id="tab-graph" onclick="setMode('graph')">Graph</button>
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
    <label>from <input type="date" id="fdatefrom" onchange="render()"></label>
    <label>to <input type="date" id="fdateto" onchange="render()"></label>
  </div>
  <div class="toolbar" id="graph-toolbar" style="display:none">
    <label><input type="checkbox" id="gshowsup" onchange="onGraphFilterChange()"> show superseded</label>
    <select id="gsource" onchange="onGraphFilterChange()">
      <option value="">Journal + AI</option>
      <option value="journal">Journal only</option>
      <option value="ai_extract">AI-extracted only</option>
    </select>
  </div>
  <div class="activetag" id="activetag"></div>
  <main>
    <div id="view-timeline"></div>
    <div id="view-index" style="display:none"></div>
    <div id="view-graph" style="display:none">
      <div class="graph-caption">
        <strong>How to read this:</strong> start with the cluster overview below — bubble
        size is how many captures share that tag. Click a bubble, or "view in graph" on any
        capture, to open its local connections: the clicked item sits centered, its direct
        connections (or a cluster's member captures) arranged around it. Click any neighbor
        to recenter. Use Back to retrace your steps.
      </div>
      <div id="graph-back" class="graph-back" style="display:none">
        <button class="btn" onclick="graphGoBack()">← Back</button>
      </div>
      <div id="graph-landing"></div>
      <div id="graph-ego" style="display:none">
        <div id="ego-empty" class="empty" style="display:none"></div>
        <svg id="graph-svg" viewBox="0 0 900 560" preserveAspectRatio="xMidYMid meet"
          aria-label="Connection graph">
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
              markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0,0 L10,5 L0,10 z" class="arrowhead"></path>
            </marker>
          </defs>
          <g id="graph-edges"></g>
          <g id="graph-nodes"></g>
        </svg>
        <div class="ego-panel">
          <button class="btn" id="ego-open-timeline">Open in Timeline →</button>
        </div>
        <div id="graph-legend" class="legend"></div>
      </div>
    </div>
  </main>
  <footer>Generated by ksj-mcp · local file, no network · data as of <span id="gen"></span></footer>
</div>
<script id="ksj-data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('ksj-data').textContent);
const ROLE_SECTIONS = __ROLE_SECTIONS__;
const byId = new Map(DATA.captures.map(c => [c.id, c]));
const prefersReducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
let state = { mode: 'timeline', tag: null, entity: null };
const TIMELINE_PAGE_SIZE = 25;
state.timelineLimit = TIMELINE_PAGE_SIZE;
let graphView = { center: null };  // null = landing (cluster/tag overview)
let graphHistory = [];             // stack of prior centers (null | {type,...})
let egoShowCount = 0;               // how many neighbors/members to render (reset per center)
let graphShowSuperseded = false;
let graphSourceFilter = '';

const esc = s => String(s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = c => c.template_id ? c.template_id + (c.page_suffix || '') : 'UNIDENTIFIED #' + c.id;

function setMode(m) {
  state.mode = m;
  for (const id of ['timeline', 'index', 'graph']) {
    document.getElementById('tab-' + id).classList.toggle('active', m === id);
  }
  document.getElementById('toolbar').style.display = m === 'timeline' ? 'flex' : 'none';
  document.getElementById('graph-toolbar').style.display = m === 'graph' ? 'flex' : 'none';
  document.getElementById('view-timeline').style.display = m === 'timeline' ? '' : 'none';
  document.getElementById('view-index').style.display = m === 'index' ? '' : 'none';
  document.getElementById('view-graph').style.display = m === 'graph' ? '' : 'none';
  if (m !== 'timeline') document.getElementById('activetag').style.display = 'none';
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

// Jump to a capture from anywhere (a connection link, a graph node): reset
// the timeline filters so the target is guaranteed visible, then scroll to
// and briefly highlight its card.
function gotoCapture(id) {
  document.getElementById('q').value = '';
  document.getElementById('ftype').value = '';
  document.getElementById('fvol').value = '';
  document.getElementById('fsrc').value = '';
  document.getElementById('fdatefrom').value = '';
  document.getElementById('fdateto').value = '';
  const cap = byId.get(id);
  if (cap && cap.superseded) document.getElementById('fsup').checked = true;
  state.tag = null; state.entity = null;
  setMode('timeline');
  requestAnimationFrame(() => {
    const el = document.getElementById('cap-' + id);
    if (el) {
      el.scrollIntoView({ behavior: prefersReducedMotion ? 'auto' : 'smooth', block: 'center' });
      el.classList.add('flash');
      setTimeout(() => el.classList.remove('flash'), 1200);
    }
  });
}

// Deep-link entry points into the graph tab (from a capture card, or an
// Index tag/entity chip) — both start a fresh navigation, so any prior
// drill-in history is discarded rather than appended to.
function gotoGraph(id) {
  graphHistory = [];
  egoShowCount = EGO_NEIGHBOR_CAP;
  graphView = { center: { type: 'capture', id } };
  setMode('graph');
}
function gotoGraphCluster(role, value, display) {
  graphHistory = [];
  egoShowCount = EGO_NEIGHBOR_CAP;
  graphView = { center: { type: 'cluster', role, value, display } };
  setMode('graph');
}

// True once any search/filter control is off its default — at that point the
// timeline shows its full (unpaginated) match set rather than the recent-N
// window, since a filtered result set is already bounded by the filter itself.
function hasActiveFilters() {
  return !!document.getElementById('q').value.trim()
    || !!document.getElementById('ftype').value
    || !!document.getElementById('fvol').value
    || !!document.getElementById('fsrc').value
    || !!document.getElementById('fdatefrom').value
    || !!document.getElementById('fdateto').value
    || !!state.tag || !!state.entity;
}

function filtered() {
  const q = document.getElementById('q').value.trim().toLowerCase();
  const ftype = document.getElementById('ftype').value;
  const fvol = document.getElementById('fvol').value;
  const fsrc = document.getElementById('fsrc').value;
  const fsup = document.getElementById('fsup').checked;
  const dfrom = document.getElementById('fdatefrom').value;
  const dto = document.getElementById('fdateto').value;
  return DATA.captures.filter(c => {
    if (!fsup && c.superseded) return false;
    if (ftype && c.type !== ftype) return false;
    if (fvol && String(c.volume) !== fvol) return false;
    if (fsrc && c.source !== fsrc) return false;
    if (dfrom && c.date < dfrom) return false;
    if (dto && c.date > dto) return false;
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

// ---- mode 3: connection lists — pure helpers (no DOM), then rendering ----

function edgeDirection(e, capId) {
  if (e.type !== 'reference' && e.type !== 'asserted') return 'shared';
  return e.source === capId ? 'cites' : 'cited_by';
}
function otherEnd(e, capId) { return e.source === capId ? e.target : e.source; }
function edgesFor(capId) {
  return DATA.edges.filter(e => e.source === capId || e.target === capId);
}

const STRONG_OVERLAP = 2.0;
const RELATION_PASSIVE = {
  supersedes: 'superseded by', refutes: 'refuted by',
  narrows: 'narrowed by', supports: 'supported by',
  distills: 'distilled by', assesses: 'assessed by',
  observes: 'observed by',
};

function connectionRows(capId) {
  const all = edgesFor(capId);
  const priority = all.filter(e => e.type === 'asserted' || e.type === 'reference');
  const overlap = all.filter(e => e.type !== 'asserted' && e.type !== 'reference');
  const strongOverlap = overlap.filter(e => e.strength >= STRONG_OVERLAP);
  const weakCount = overlap.length - strongOverlap.length;
  const rank = e => e.type === 'asserted' ? 0 : e.type === 'reference' ? 1
              : e.type === 'entity_overlap' ? 2 : 3;
  const shown = priority.concat(strongOverlap)
    .sort((a, b) => rank(a) - rank(b) || b.strength - a.strength)
    .slice(0, 12);
  return { shown, weakCount };
}

function connectionRowHtml(e, capId) {
  const otherId = otherEnd(e, capId);
  const other = byId.get(otherId);
  if (!other) return '';
  const dir = edgeDirection(e, capId);
  let verb;
  if (e.type === 'asserted') {
    verb = dir === 'cited_by' ? (RELATION_PASSIVE[e.relation] || `${e.relation} by`) : e.relation;
  } else if (e.type === 'reference') {
    verb = dir === 'cited_by' ? 'cited by' : 'cites';
  } else if (e.type === 'entity_overlap') {
    verb = 'shares entities with';
  } else {
    verb = 'shares tags with';
  }
  const arrow = dir === 'cited_by' ? '←' : dir === 'cites' ? '→' : '↔';
  const sup = other.superseded ? '<span class="badge sup">superseded</span>' : '';
  const noteRow = e.note ? `<div class="conn-note">note: ${esc(e.note)}</div>` : '';
  return `<button class="conn-row" onclick="gotoCapture(${otherId})">
      <span class="conn-arrow">${arrow}</span>
      <span class="conn-verb">${esc(verb)}</span>
      <span class="conn-target">${esc(label(other))}</span>${sup}
      <span class="conn-summary">${esc((other.summary || '').slice(0, 70))}</span>
    </button>${noteRow}`;
}

function connectionsSectionHtml(capId) {
  const { shown, weakCount } = connectionRows(capId);
  if (!shown.length && !weakCount) return '<div class="conn-empty">No connections yet.</div>';
  let html = shown.map(e => connectionRowHtml(e, capId)).join('');
  if (weakCount > 0) {
    html += `<div class="conn-more">+ ${weakCount} weaker tag/entity connection(s) `
      + `below strength ${STRONG_OVERLAP.toFixed(1)}</div>`;
  }
  return html;
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
  return `<article class="card${c.superseded ? ' superseded' : ''}" id="cap-${c.id}">
    <div class="top"><span class="tid">${esc(label(c))}</span>${badges}
      <span class="date">${esc(c.date)}</span></div>
    <div class="summary">${esc(c.summary) || '<i>(no summary)</i>'}</div>
    <div class="chips">${chips}
      <button class="chip chip-graph" onclick="gotoGraph(${c.id})" title="View connections graph">view in graph →</button>
    </div>
    <div class="connections">
      <h4>Connections</h4>
      ${connectionsSectionHtml(c.id)}
    </div>
    <details><summary>fields &amp; ${textLabel}</summary>
      <div class="fields">${fields || '<i>(no parsed fields)</i>'}</div>
      <div class="rawtext">${esc(c.text) || '(empty)'}</div>
    </details>
  </article>`;
}

function loadMoreTimeline() {
  state.timelineLimit += TIMELINE_PAGE_SIZE;
  render();
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

  // Unfiltered browsing shows only the most recent N (captures already
  // arrive newest-first); any active search/filter shows its full match set
  // unpaginated, since that set is already bounded by the filter itself.
  const windowed = hasActiveFilters();
  const shown = windowed ? caps : caps.slice(0, state.timelineLimit);
  const remaining = windowed ? 0 : caps.length - shown.length;

  if (!caps.length) {
    el.innerHTML = '<div class="empty">No captures match the current filters.</div>';
    return;
  }
  let html = shown.map(captureCard).join('');
  if (remaining > 0) {
    html += `<div class="load-more">
      <button class="btn" onclick="loadMoreTimeline()">Load ${Math.min(remaining, TIMELINE_PAGE_SIZE)} more (${remaining} remaining)</button>
    </div>`;
  }
  el.innerHTML = html;
}

function renderIndex(el) {
  let html = '';
  const ents = DATA.entities.filter(e => e.capture_ids.length > 0);
  if (ents.length) {
    html += `<div class="idx-section"><h2>Entities <small>· people, places, works, symbols</small></h2>
      <div class="idx-list">` + ents.map(e =>
        `<span class="idx-item">
          <button class="idx-main" onclick="setEntity(${e.id})">
            ${esc(e.name)}<span class="kind">${esc(e.kind)}</span>
            <span class="n">×${e.capture_ids.length}</span></button>
          <button class="idx-graph" onclick="gotoGraphCluster('entity', ${e.id}, '${esc(e.name)}')"
            title="View in graph">⟡</button>
        </span>`).join('') +
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
        `<span class="idx-item">
          <button class="idx-main" onclick="setTag('${esc(v)}', '${esc(role)}')">
            ${esc(d.display)}<span class="n">×${d.n}</span></button>
          <button class="idx-graph" onclick="gotoGraphCluster('${esc(role)}', '${esc(v)}', '${esc(d.display)}')"
            title="View in graph">⟡</button>
        </span>`).join('') +
      '</div></div>';
  }
  el.innerHTML = html || '<div class="empty">Nothing indexed yet — upload some captures.</div>';
}

function render() {
  if (state.mode === 'timeline') renderTimeline(document.getElementById('view-timeline'));
  else if (state.mode === 'index') renderIndex(document.getElementById('view-index'));
  else if (state.mode === 'graph') renderGraphView();
}

// ---- mode 4: ego-centric graph, two layers ----
//
// Landing view: tag/role/entity cluster bubbles (reuses the same counting
// logic as renderIndex), sized by capture count. Drill-in view: a single
// fixed radial layout — the clicked capture or cluster sits centered,
// direct connections (or, for a cluster, member captures) arranged evenly
// around it. Clicking a neighbor recenters; Back retraces graphHistory.
// No physics, no rotation — every render is a plain, static SVG built from
// the already-embedded DATA.edges/DATA.captures (this is a static export,
// there's no live DB to query further).
//
// No fixed strength floor on tag-overlap edges: real KSJ data shows
// per-capture connection strength varies enormously (checked against the
// live 900+ capture corpus — many individual captures' strongest edge
// falls well under any single global cutoff that would exclude noise for
// OTHER captures), so a fixed threshold silently emptied the ring for
// plenty of real captures. Ranking by strength and capping at
// EGO_NEIGHBOR_CAP already does the noise-control job per node, which a
// blanket floor can't.

const GRAPH_W = 900, GRAPH_H = 560, GRAPH_CX = GRAPH_W / 2, GRAPH_CY = GRAPH_H / 2;
const EGO_RING_R = 200, EGO_CENTER_R = 44, EGO_NEIGHBOR_R = 30;
const EGO_NEIGHBOR_CAP = 8;
const TYPE_LABELS = { RC: 'Rapid Capture', SYN: 'Synthesis', REV: 'Review', DC: 'Dream Capture', AIEX: 'AI-Extracted' };

function svgEl(tag, attrs) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

// Personalization: which captures are currently eligible to appear at all.
function graphVisibleCaptures() {
  return DATA.captures.filter(c =>
    (graphShowSuperseded || !c.superseded) &&
    (!graphSourceFilter || c.source === graphSourceFilter)
  );
}

function relationClass(e) {
  return e.type === 'asserted' && e.relation ? ` e-rel-${e.relation}` : '';
}

function truncateLabel(s, maxLen) {
  return s.length > maxLen ? s.slice(0, maxLen - 1) + '…' : s;
}

// Direct connection edges for a capture, ranked the same way mode 3 ranks
// them (asserted/reference first, then strength), restricted to whatever's
// currently visible and above the tag-overlap noise floor.
function egoNeighbors(capId) {
  const visibleIds = new Set(graphVisibleCaptures().map(c => c.id));
  if (!visibleIds.has(capId)) return [];
  const rank = e => e.type === 'asserted' ? 0 : e.type === 'reference' ? 1
              : e.type === 'entity_overlap' ? 2 : 3;
  const edges = edgesFor(capId).filter(e => visibleIds.has(otherEnd(e, capId)));
  return edges
    .map(e => ({ e, other: byId.get(otherEnd(e, capId)) }))
    .filter(x => x.other)
    .sort((a, b) => rank(a.e) - rank(b.e) || b.e.strength - a.e.strength
      || (b.other.date || '').localeCompare(a.other.date || ''));
}

// A cluster's "neighbors" are its member captures (tag/role or entity
// match) — membership, not connection edges.
function clusterMembers(role, value) {
  const visible = graphVisibleCaptures();
  if (role === 'entity') {
    const ent = DATA.entities.find(e => e.id === value);
    if (!ent) return [];
    const idSet = new Set(ent.capture_ids);
    return visible.filter(c => idSet.has(c.id));
  }
  return visible.filter(c => c.tags.some(t => t.role === role && t.value === value));
}

// ---- landing view: cluster bubbles, same grouping as renderIndex ----

function graphClusters() {
  const visible = graphVisibleCaptures();
  const visibleIds = new Set(visible.map(c => c.id));
  const sections = [];
  const ents = DATA.entities
    .map(e => ({ ...e, capture_ids: e.capture_ids.filter(id => visibleIds.has(id)) }))
    .filter(e => e.capture_ids.length > 0);
  if (ents.length) {
    sections.push({ title: 'Entities', items: ents.map(e => ({
      role: 'entity', value: e.id, display: e.name, count: e.capture_ids.length })) });
  }
  for (const [role, title] of ROLE_SECTIONS) {
    const counts = {};
    for (const c of visible) {
      for (const t of c.tags) {
        if (t.role !== role) continue;
        (counts[t.value] ||= { display: t.prefix + t.display, n: 0 }).n++;
      }
    }
    const items = Object.entries(counts).sort((a, b) => b[1].n - a[1].n)
      .map(([v, d]) => ({ role, value: v, display: d.display, count: d.n }));
    if (items.length) sections.push({ title, items });
  }
  return sections;
}

function bubbleSize(count, maxCount) {
  const t = maxCount > 0 ? count / maxCount : 0;
  return Math.round(50 + t * 90); // 50..140px
}

function renderGraphLanding(el) {
  const sections = graphClusters();
  if (!sections.length) {
    el.innerHTML = '<div class="empty">Nothing to graph yet — upload some captures.</div>';
    return;
  }
  let html = '';
  for (const sec of sections) {
    const maxCount = Math.max(...sec.items.map(i => i.count));
    html += `<div class="idx-section"><h2>${esc(sec.title)} <small>· ${sec.items.length}</small></h2>
      <div class="bubbles">` + sec.items.map(i => {
        const size = bubbleSize(i.count, maxCount);
        const valueLit = typeof i.value === 'number' ? i.value : `'${esc(String(i.value))}'`;
        return `<button class="bubble" style="width:${size}px;height:${size}px"
          onclick="egoRecenterCluster('${esc(i.role)}', ${valueLit}, '${esc(i.display)}')">
          <span class="bn">${esc(i.display)}</span><span class="bc">×${i.count}</span></button>`;
      }).join('') + '</div></div>';
  }
  el.innerHTML = html;
}

// ---- drill-in (ego) view ----

function renderGraphView() {
  document.getElementById('graph-back').style.display = graphHistory.length ? '' : 'none';
  const landing = document.getElementById('graph-landing');
  const ego = document.getElementById('graph-ego');
  if (!graphView.center) {
    landing.style.display = '';
    ego.style.display = 'none';
    renderGraphLanding(landing);
    return;
  }
  landing.style.display = 'none';
  ego.style.display = '';
  renderEgoGraph(graphView.center);
}

function graphGoBack() {
  egoShowCount = EGO_NEIGHBOR_CAP;
  graphView = { center: graphHistory.length ? graphHistory.pop() : null };
  renderGraphView();
}

function egoRecenter(center) {
  graphHistory.push(graphView.center);
  egoShowCount = EGO_NEIGHBOR_CAP;
  graphView = { center };
  renderGraphView();
}
// Landing-bubble clicks stay inside the graph tab's own navigation (push
// history, so Back returns to the landing view) — unlike gotoGraphCluster,
// which is for entry points OUTSIDE the graph tab and always starts fresh.
function egoRecenterCluster(role, value, display) {
  egoRecenter({ type: 'cluster', role, value, display });
}

function onGraphFilterChange() {
  graphShowSuperseded = document.getElementById('gshowsup').checked;
  graphSourceFilter = document.getElementById('gsource').value;
  renderGraphView();
}

function renderEgoEmpty(msg) {
  document.getElementById('graph-nodes').innerHTML = '';
  document.getElementById('graph-edges').innerHTML = '';
  document.getElementById('ego-open-timeline').style.display = 'none';
  document.getElementById('graph-legend').innerHTML = '';
  const emptyEl = document.getElementById('ego-empty');
  emptyEl.textContent = msg;
  emptyEl.style.display = '';
}

function updateEgoPanelButton(center) {
  const btn = document.getElementById('ego-open-timeline');
  if (center.type === 'capture' && byId.get(center.id)) {
    btn.style.display = '';
    btn.onclick = () => gotoCapture(center.id);
  } else {
    btn.style.display = 'none';
  }
}

function renderGraphLegend(caps) {
  const types = [...new Set(caps.filter(Boolean).map(c => c.type))].sort();
  document.getElementById('graph-legend').innerHTML = types.map(t =>
    `<span><i class="dot t-${esc(t)}"></i>${esc(TYPE_LABELS[t] || t)}</span>`).join('');
}

function egoLabelForeignObject(r, text, maxLen) {
  const width = Math.max(70, r * 2.6);
  const fo = svgEl('foreignObject', { x: -width / 2, y: r + 4, width, height: 30 });
  const div = document.createElement('div');
  div.className = 'ego-label';
  div.textContent = truncateLabel(text, maxLen);
  fo.appendChild(div);
  return fo;
}

function buildEgoNode(cap, x, y, r, onClick) {
  const g = svgEl('g', {
    class: `ego-node t-${cap.type}${cap.superseded ? ' superseded' : ''}${cap.source === 'ai_extract' ? ' src-ai' : ''}`,
    transform: `translate(${x},${y})`,
  });
  g.appendChild(svgEl('circle', { class: 'body', r }));
  g.appendChild(egoLabelForeignObject(r, label(cap), 30));
  g.addEventListener('click', onClick);
  return g;
}

function buildEgoCenterNode(cls, display) {
  const g = svgEl('g', { class: `ego-node ${cls}`, transform: `translate(${GRAPH_CX},${GRAPH_CY})` });
  g.appendChild(svgEl('circle', { class: 'body', r: EGO_CENTER_R }));
  g.appendChild(egoLabelForeignObject(EGO_CENTER_R, display, 46));
  return g;
}

function buildEgoMoreNode(count, x, y) {
  const g = svgEl('g', { class: 'ego-node more', transform: `translate(${x},${y})` });
  g.appendChild(svgEl('circle', { class: 'body', r: EGO_NEIGHBOR_R }));
  g.appendChild(egoLabelForeignObject(EGO_NEIGHBOR_R, `+${count} more`, 30));
  // Grows by one more ring's worth per click (like Timeline's Load More),
  // never jumps straight to "show all" — real KSJ captures can carry
  // hundreds of tag-overlap edges, and a ring with hundreds of nodes at
  // once is exactly the illegible overlap this cap exists to prevent.
  g.addEventListener('click', () => { egoShowCount += EGO_NEIGHBOR_CAP; renderGraphView(); });
  return g;
}

function renderEgoGraph(center) {
  document.getElementById('ego-empty').style.display = 'none';
  let centerDisplay, centerCls, itemsAll;

  if (center.type === 'capture') {
    const centerCap = byId.get(center.id);
    if (!centerCap) { renderEgoEmpty('That capture is not visible under the current filters.'); return; }
    centerDisplay = label(centerCap);
    centerCls = `t-${centerCap.type}`;
    itemsAll = egoNeighbors(center.id).map(x => ({ cap: x.other, edge: x.e }));
  } else {
    centerDisplay = center.display;
    centerCls = 'cluster';
    itemsAll = clusterMembers(center.role, center.value)
      .slice().sort((a, b) => (b.date || '').localeCompare(a.date || ''))
      .map(c => ({ cap: c, edge: null }));
  }

  updateEgoPanelButton(center);

  const nodesG = document.getElementById('graph-nodes');
  const edgesG = document.getElementById('graph-edges');
  nodesG.innerHTML = ''; edgesG.innerHTML = '';

  const shown = itemsAll.slice(0, egoShowCount);
  const moreCount = Math.max(0, itemsAll.length - egoShowCount);
  const slots = shown.length + (moreCount > 0 ? 1 : 0);

  shown.forEach((item, i) => {
    const angle = (2 * Math.PI * i) / slots - Math.PI / 2;
    const x = GRAPH_CX + EGO_RING_R * Math.cos(angle);
    const y = GRAPH_CY + EGO_RING_R * Math.sin(angle);
    const edgeClass = item.edge ? `ego-edge e-${item.edge.type}${relationClass(item.edge)}` : 'ego-edge';
    const line = svgEl('line', { class: edgeClass, x1: GRAPH_CX, y1: GRAPH_CY, x2: x, y2: y });
    if (item.edge && (item.edge.type === 'reference' || item.edge.type === 'asserted')) {
      line.setAttribute('marker-end', 'url(#arrow)');
    }
    edgesG.appendChild(line);
    nodesG.appendChild(buildEgoNode(item.cap, x, y, EGO_NEIGHBOR_R,
      () => egoRecenter({ type: 'capture', id: item.cap.id })));
  });

  if (moreCount > 0) {
    const angle = (2 * Math.PI * shown.length) / slots - Math.PI / 2;
    const x = GRAPH_CX + EGO_RING_R * Math.cos(angle);
    const y = GRAPH_CY + EGO_RING_R * Math.sin(angle);
    edgesG.appendChild(svgEl('line', { class: 'ego-edge', x1: GRAPH_CX, y1: GRAPH_CY, x2: x, y2: y }));
    nodesG.appendChild(buildEgoMoreNode(moreCount, x, y));
  }

  nodesG.appendChild(buildEgoCenterNode(centerCls, centerDisplay));
  renderGraphLegend([center.type === 'capture' ? byId.get(center.id) : null, ...shown.map(s => s.cap)]);
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
