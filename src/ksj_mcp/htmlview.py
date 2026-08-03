"""
Self-contained HTML view of the knowledge base (§2.4a).

Overview modes ship in stages, gated only by the data each needs:
  Mode 1 — Timeline: chronological captures, client-side search + filters.
  Mode 2 — Index: tags grouped by semantic role, plus the entity register.
  Mode 3 — Connections: every capture card lists its typed, directional
           edges as clickable links (no spatial layout, just adjacency).
  Mode 4 — Graph: force-directed visualization, gated on edge quality and
           typed edges (server 3.0/3.1) so it never renders a hairball.

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
.toolbar input[type=range] { width: 110px; accent-color: var(--accent); }
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
  font-size: .76rem; color: var(--muted); margin-left: auto; }
.legend .sep { color: var(--line); }
.legend .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
  margin-right: 4px; vertical-align: middle; }
.legend .line { display: inline-block; width: 16px; height: 2px; margin-right: 4px;
  vertical-align: middle; background: var(--muted); }
.dot.t-RC { background: var(--type-rc); }
.dot.t-SYN { background: var(--type-syn); }
.dot.t-REV { background: var(--type-rev); }
.dot.t-DC { background: var(--type-dc); }
.dot.t-AIEX { background: var(--accent); }
.line.e-reference { background: var(--accent); }
.line.e-asserted { background: var(--edge-negative); }
.line.e-entity_overlap { background: var(--accent); opacity: .5; }
.line.e-tag_overlap { background: var(--muted); opacity: .5; }
#view-graph { position: relative; }
#graph-svg { width: 100%; height: 64vh; min-height: 360px; display: block;
  border: 1px solid var(--line); border-radius: 12px; background: var(--card); touch-action: none; }
.arrowhead { fill: var(--muted); }
.node circle.body { stroke: var(--card); stroke-width: 1.5; cursor: pointer; }
.node.t-RC circle.body { fill: var(--type-rc); }
.node.t-SYN circle.body { fill: var(--type-syn); }
.node.t-REV circle.body { fill: var(--type-rev); }
.node.t-DC circle.body { fill: var(--type-dc); }
.node.t-AIEX circle.body { fill: var(--accent); }
.node.superseded circle.body { opacity: .5; stroke-dasharray: 2 2; }
.node.src-ai circle.body { stroke: var(--accent); stroke-width: 2; }
.node .node-label { font-size: 9px; fill: var(--ink); pointer-events: none; user-select: none; }
.node.dimmed { opacity: .15; }
.node:hover circle.body { stroke: var(--ink); stroke-width: 2.5; }
.edge { fill: none; stroke: var(--muted); stroke-opacity: .3; stroke-width: 1; }
.edge.e-reference { stroke: var(--accent); stroke-opacity: .8; stroke-width: 1.6; }
.edge.e-asserted { stroke-width: 2; stroke: var(--muted); }
.edge.e-asserted.e-rel-supersedes { stroke: var(--edge-negative); stroke-dasharray: 6 3; }
.edge.e-asserted.e-rel-refutes { stroke: var(--edge-negative); stroke-dasharray: 2 3; stroke-width: 1.4; }
.edge.e-asserted.e-rel-supports { stroke: var(--edge-positive); }
.edge.e-asserted.e-rel-narrows { stroke: var(--muted); }
.edge.e-entity_overlap { stroke: var(--accent); stroke-opacity: .45; stroke-width: 1.3; }
.edge.e-tag_overlap { stroke: var(--muted); stroke-opacity: .3; stroke-width: 1; }
.edge.dimmed { stroke-opacity: .08 !important; }
.graph-tooltip { position: absolute; pointer-events: none; background: var(--ink); color: var(--bg);
  font-size: .76rem; padding: 4px 8px; border-radius: 6px; transform: translate(-50%, -130%);
  white-space: nowrap; z-index: 5; }
.graph-panel { position: absolute; top: 12px; right: 12px; max-width: 260px; background: var(--card);
  border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px;
  box-shadow: 0 4px 18px rgba(0,0,0,.16); font-size: .85rem; z-index: 6; }
.graph-panel h3 { font-size: .92rem; margin-bottom: 4px; }
.graph-panel .meta2 { color: var(--muted); font-size: .76rem; margin-bottom: 6px; }
.graph-panel button { margin-top: 8px; width: 100%; }
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
  </div>
  <div class="toolbar" id="graph-toolbar" style="display:none">
    <label>min tag/entity strength
      <input type="range" id="gminstrength" min="0" max="6" step="0.5" value="2"
        oninput="onGraphStrengthChange()">
      <span id="gminstrength-val">2.0</span>
    </label>
    <button class="btn" onclick="resetGraphLayout()">Reset layout</button>
    <div class="legend">
      <span><i class="dot t-RC"></i>RC</span>
      <span><i class="dot t-SYN"></i>SYN</span>
      <span><i class="dot t-REV"></i>REV</span>
      <span><i class="dot t-DC"></i>DC</span>
      <span><i class="dot t-AIEX"></i>AIEX</span>
      <span class="sep">|</span>
      <span><i class="line e-reference"></i>reference</span>
      <span><i class="line e-asserted"></i>asserted</span>
      <span><i class="line e-entity_overlap"></i>entity</span>
      <span><i class="line e-tag_overlap"></i>tag overlap</span>
    </div>
  </div>
  <div class="activetag" id="activetag"></div>
  <main>
    <div id="view-timeline"></div>
    <div id="view-index" style="display:none"></div>
    <div id="view-graph" style="display:none">
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
      <div id="graph-tooltip" class="graph-tooltip" hidden></div>
      <div id="graph-panel" class="graph-panel" hidden></div>
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
  if (m === 'graph') ensureGraphInit();
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
    <div class="chips">${chips}</div>
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
  if (state.mode === 'timeline') renderTimeline(document.getElementById('view-timeline'));
  else if (state.mode === 'index') renderIndex(document.getElementById('view-index'));
  // graph mode keeps its own persistent DOM (see ensureGraphInit) — no
  // innerHTML replacement here, that would blow away node positions.
}

// ---- mode 4: force-directed graph ----
//
// A small hand-written simulation (no CDN dependency — "opens in any
// browser, no network" rules that out). Weak tag-overlap edges are
// excluded by default (adjustable via the strength slider), matching the
// server's own traversal rule (connections.py _edge_map) — a graph where
// every capture connects to hundreds of others is a hairball, not a graph.

function filterGraphEdges(edges, minStrength) {
  return edges.filter(e => e.type !== 'tag_overlap' || e.strength >= minStrength);
}

function computeDegrees(nodeIds, edges) {
  const deg = new Map(nodeIds.map(id => [id, 0]));
  for (const e of edges) {
    deg.set(e.source, (deg.get(e.source) || 0) + 1);
    deg.set(e.target, (deg.get(e.target) || 0) + 1);
  }
  return deg;
}

function linkDistance(e) {
  const base = e.type === 'asserted' ? 90 : e.type === 'reference' ? 110
             : e.type === 'entity_overlap' ? 140 : 170;
  return Math.max(base - (e.strength || 1) * 8, 40);
}
function linkStrengthFor(e) {
  return (e.type === 'asserted' || e.type === 'reference') ? 0.12
       : e.type === 'entity_overlap' ? 0.09 : 0.05;
}

const SIM = { charge: -900, centerStrength: 0.02, velocityDecay: 0.82, collidePad: 4 };

// One physics tick. Pure with respect to its inputs except that it mutates
// node positions/velocities in place (the standard force-simulation
// pattern) — kept dependency-free so it can be unit-exercised directly.
function simTick(nodes, links, cx, cy, alpha) {
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const a = nodes[i], b = nodes[j];
      let dx = b.x - a.x, dy = b.y - a.y;
      let dist2 = dx * dx + dy * dy;
      if (dist2 < 0.01) { dx = (Math.random() - 0.5) * 0.1; dy = (Math.random() - 0.5) * 0.1; dist2 = dx * dx + dy * dy; }
      const dist = Math.sqrt(dist2);
      const force = SIM.charge * alpha / dist2;
      const fx = (dx / dist) * force, fy = (dy / dist) * force;
      if (a.fx === undefined) { a.vx -= fx; a.vy -= fy; }
      if (b.fx === undefined) { b.vx += fx; b.vy += fy; }
      const minDist = (a.r || 6) + (b.r || 6) + SIM.collidePad;
      if (dist < minDist) {
        const overlap = (minDist - dist) * 0.5 * alpha;
        const ox = (dx / dist) * overlap, oy = (dy / dist) * overlap;
        if (a.fx === undefined) { a.x -= ox; a.y -= oy; }
        if (b.fx === undefined) { b.x += ox; b.y += oy; }
      }
    }
  }
  for (const l of links) {
    const a = l._s, b = l._t;
    if (!a || !b) continue;
    let dx = b.x - a.x, dy = b.y - a.y;
    let dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
    const target = linkDistance(l);
    const strength = linkStrengthFor(l) * alpha;
    const diff = (dist - target) / dist * strength;
    const ox = dx * diff, oy = dy * diff;
    if (a.fx === undefined) { a.vx += ox; a.vy += oy; }
    if (b.fx === undefined) { b.vx -= ox; b.vy -= oy; }
  }
  for (const n of nodes) {
    if (n.fx !== undefined) { n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; continue; }
    n.vx += (cx - n.x) * SIM.centerStrength * alpha;
    n.vy += (cy - n.y) * SIM.centerStrength * alpha;
    n.vx *= SIM.velocityDecay;
    n.vy *= SIM.velocityDecay;
    n.x += n.vx;
    n.y += n.vy;
  }
}

const GRAPH_W = 900, GRAPH_H = 560, GRAPH_CX = GRAPH_W / 2, GRAPH_CY = GRAPH_H / 2;
let graphInitialized = false;
let graphNodes = [], graphSimLinks = [];
let graphAlpha = 1, graphAlphaTarget = 0, graphRunning = false;
let graphSelected = null;

function svgEl(tag, attrs) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

function randomPos() {
  const angle = Math.random() * Math.PI * 2;
  const radius = 60 + Math.random() * 180;
  return { x: GRAPH_CX + Math.cos(angle) * radius, y: GRAPH_CY + Math.sin(angle) * radius };
}

function buildGraphData(minStrength) {
  const filteredEdges = filterGraphEdges(DATA.edges, minStrength);
  const degrees = computeDegrees(DATA.captures.map(c => c.id), filteredEdges);
  const prevById = new Map(graphNodes.map(n => [n.id, n]));
  graphNodes = DATA.captures.map(c => {
    const prev = prevById.get(c.id);
    const deg = degrees.get(c.id) || 0;
    const r = Math.max(5, Math.min(16, 5 + deg * 1.3));
    if (prev) { prev.r = r; prev.deg = deg; return prev; }
    const p = randomPos();
    return { id: c.id, x: p.x, y: p.y, vx: 0, vy: 0, r, deg, cap: c };
  });
  const nodeById = new Map(graphNodes.map(n => [n.id, n]));
  graphSimLinks = filteredEdges
    .map(e => ({ ...e, _s: nodeById.get(e.source), _t: nodeById.get(e.target) }))
    .filter(l => l._s && l._t);
}

function relationClass(e) {
  return e.type === 'asserted' && e.relation ? ` e-rel-${e.relation}` : '';
}

function renderGraphStructure() {
  const edgesG = document.getElementById('graph-edges');
  const nodesG = document.getElementById('graph-nodes');
  edgesG.innerHTML = '';
  for (const l of graphSimLinks) {
    const directional = l.type === 'reference' || l.type === 'asserted';
    const line = svgEl('line', { class: `edge e-${l.type}${relationClass(l)}` });
    if (directional) line.setAttribute('marker-end', 'url(#arrow)');
    edgesG.appendChild(line);
  }
  nodesG.innerHTML = '';
  const showLabels = graphNodes.length <= 40;
  for (const n of graphNodes) {
    const c = n.cap;
    const g = svgEl('g', {
      class: `node t-${c.type}${c.superseded ? ' superseded' : ''}${c.source === 'ai_extract' ? ' src-ai' : ''}`,
      'data-id': c.id,
    });
    g.appendChild(svgEl('circle', { class: 'body', r: n.r }));
    if (showLabels) {
      const text = svgEl('text', { class: 'node-label', x: n.r + 3, y: 3 });
      text.textContent = label(c);
      g.appendChild(text);
    }
    g.addEventListener('pointerdown', ev => onNodePointerDown(ev, n));
    g.addEventListener('pointerenter', () => showGraphTooltip(n));
    g.addEventListener('pointerleave', hideGraphTooltip);
    nodesG.appendChild(g);
  }
  renderGraphPositions();
}

function renderGraphPositions() {
  const edgeEls = document.getElementById('graph-edges').children;
  for (let i = 0; i < graphSimLinks.length; i++) {
    const l = graphSimLinks[i], el = edgeEls[i];
    el.setAttribute('x1', l._s.x); el.setAttribute('y1', l._s.y);
    el.setAttribute('x2', l._t.x); el.setAttribute('y2', l._t.y);
  }
  const nodeEls = document.getElementById('graph-nodes').children;
  for (let i = 0; i < graphNodes.length; i++) {
    nodeEls[i].setAttribute('transform', `translate(${graphNodes[i].x},${graphNodes[i].y})`);
  }
}

function kickGraphSim(a) {
  graphAlpha = Math.max(graphAlpha, a);
  if (!graphRunning) { graphRunning = true; requestAnimationFrame(stepGraphSim); }
}
function stepGraphSim() {
  graphAlpha += (graphAlphaTarget - graphAlpha) * 0.05;
  simTick(graphNodes, graphSimLinks, GRAPH_CX, GRAPH_CY, Math.max(graphAlpha, 0));
  renderGraphPositions();
  if (graphAlpha > 0.001 || graphAlphaTarget > 0) {
    requestAnimationFrame(stepGraphSim);
  } else {
    graphRunning = false;
  }
}

function ensureGraphInit() {
  if (graphInitialized) return;
  graphInitialized = true;
  const host = document.getElementById('view-graph');
  if (!DATA.captures.length) {
    host.innerHTML = '<div class="empty">No captures yet — nothing to graph.</div>';
    return;
  }
  buildGraphData(2.0);
  wireGraphInteraction();
  if (prefersReducedMotion) {
    for (let i = 0; i < 300; i++) {
      simTick(graphNodes, graphSimLinks, GRAPH_CX, GRAPH_CY, Math.max(1 - i / 300, 0));
    }
    graphAlpha = 0;
    renderGraphStructure();
  } else {
    renderGraphStructure();
    kickGraphSim(1);
  }
}

function onGraphStrengthChange() {
  const v = parseFloat(document.getElementById('gminstrength').value);
  document.getElementById('gminstrength-val').textContent = v.toFixed(1);
  buildGraphData(v);
  clearGraphSelection();
  renderGraphStructure();
  graphAlphaTarget = 0.5;
  kickGraphSim(0.5);
}

function resetGraphLayout() {
  for (const n of graphNodes) {
    delete n.fx; delete n.fy;
    const p = randomPos();
    n.x = p.x; n.y = p.y; n.vx = 0; n.vy = 0;
  }
  clearGraphSelection();
  graphAlphaTarget = 0;
  kickGraphSim(1);
}

// ---- graph interaction: hover tooltip, click-to-inspect, drag ----

let dragNode = null, dragStart = null, dragMoved = false;

function svgPoint(evt) {
  const svg = document.getElementById('graph-svg');
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX; pt.y = evt.clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  const p = pt.matrixTransform(ctm.inverse());
  return { x: p.x, y: p.y };
}

function onNodePointerDown(ev, n) {
  ev.stopPropagation();
  dragNode = n; dragMoved = false;
  dragStart = svgPoint(ev);
  n.fx = n.x; n.fy = n.y;
  graphAlphaTarget = 0.4;
  kickGraphSim(0.4);
  document.getElementById('graph-svg').setPointerCapture(ev.pointerId);
}
function onSvgPointerMove(ev) {
  if (!dragNode) return;
  const p = svgPoint(ev);
  if (Math.abs(p.x - dragStart.x) > 2 || Math.abs(p.y - dragStart.y) > 2) dragMoved = true;
  dragNode.fx = p.x; dragNode.fy = p.y;
}
function onSvgPointerUp() {
  if (!dragNode) return;
  graphAlphaTarget = 0;
  if (!dragMoved) selectGraphNode(dragNode);
  dragNode = null;
}
function onSvgBackgroundClick(ev) {
  if (ev.target.id === 'graph-svg') clearGraphSelection();
}

function wireGraphInteraction() {
  const svg = document.getElementById('graph-svg');
  svg.addEventListener('pointermove', onSvgPointerMove);
  svg.addEventListener('pointerup', onSvgPointerUp);
  svg.addEventListener('click', onSvgBackgroundClick);
}

function selectGraphNode(n) {
  graphSelected = n.id;
  const neighborIds = new Set([n.id]);
  for (const l of graphSimLinks) {
    if (l._s.id === n.id) neighborIds.add(l._t.id);
    if (l._t.id === n.id) neighborIds.add(l._s.id);
  }
  for (const el of document.getElementById('graph-nodes').children) {
    el.classList.toggle('dimmed', !neighborIds.has(Number(el.getAttribute('data-id'))));
  }
  const edgeEls = document.getElementById('graph-edges').children;
  for (let i = 0; i < graphSimLinks.length; i++) {
    const l = graphSimLinks[i];
    edgeEls[i].classList.toggle('dimmed', l._s.id !== n.id && l._t.id !== n.id);
  }
  showGraphPanel(n);
}
function clearGraphSelection() {
  graphSelected = null;
  for (const el of document.getElementById('graph-nodes').children) el.classList.remove('dimmed');
  for (const el of document.getElementById('graph-edges').children) el.classList.remove('dimmed');
  hideGraphPanel();
}

function showGraphPanel(n) {
  const c = n.cap;
  const panel = document.getElementById('graph-panel');
  panel.hidden = false;
  panel.innerHTML = `
    <h3>${esc(label(c))}</h3>
    <div class="meta2">${esc(c.type)} · vol ${c.volume} · ${esc(c.date)} · ${n.deg} connection(s)</div>
    <div>${esc((c.summary || '').slice(0, 140)) || '<i>(no summary)</i>'}</div>
    <button class="btn" onclick="gotoCapture(${c.id})">Open in Timeline →</button>
  `;
}
function hideGraphPanel() { document.getElementById('graph-panel').hidden = true; }

function showGraphTooltip(n) {
  if (graphNodes.length <= 40) return; // static labels already visible
  const tip = document.getElementById('graph-tooltip');
  tip.hidden = false;
  tip.textContent = label(n.cap);
  const svg = document.getElementById('graph-svg');
  const rect = svg.getBoundingClientRect();
  tip.style.left = (n.x * (rect.width / GRAPH_W)) + 'px';
  tip.style.top = (n.y * (rect.height / GRAPH_H)) + 'px';
}
function hideGraphTooltip() { document.getElementById('graph-tooltip').hidden = true; }

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
