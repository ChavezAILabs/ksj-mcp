# KSJ Export Format — `ksj-export-v1`

The `export_backup` tool writes the entire knowledge base as **JSONL** (one
JSON object per line). This is KSJ's interchange format: versioned,
documented, and round-trippable via `import_backup`. KSJ publishes this
format instead of integrating with any specific PKM product — write an
adapter to whatever you use.

## Record kinds

Every line has a `kind` field. The first line is always the header.

### `header`

```json
{"kind": "header", "schema_version": "ksj-export-v1", "exported_at": "2026-08-02T21:00:00+00:00"}
```

`schema_version` is checked on import; future format revisions bump it.

### `capture`

```json
{"kind": "capture", "id": 12, "type": "RC", "template_id": "RC-007",
 "page_suffix": null, "volume": 1, "date": "2026-06-14T09:30:00+00:00",
 "fields": {"first_impressions": "…", "key_points": "…"},
 "raw_ocr": "…", "corrected_ocr": null, "summary": "…",
 "confidence": 0.92, "image_path": "…", "source": "journal",
 "valid_from": "2026-06-14T09:30:00+00:00", "valid_until": null}
```

- `type`: `RC | SYN | REV | DC | AIEX | UNKNOWN`
- `template_id` is `null` for unidentified pages
- `source`: `journal` (hand-written) or `ai_extract` (AIEX entries)
- `valid_until` set = this capture was superseded (kept for history)
- `raw_ocr` is always the original read; `corrected_ocr` the fixed version

### `tag`

```json
{"kind": "tag", "capture_id": 12, "prefix": "#", "value": "machine-learning",
 "display": "Machine-Learning", "role": "topic"}
```

`role` is the canonical meaning derived from (prefix, template type):
`topic | theme | reference | entity | priority | motif | question | insight | causal | sensory`.

### `entity` / `capture_entity`

```json
{"kind": "entity", "id": 3, "name": "Veronica", "normalized": "veronica", "entity_kind": "person"}
{"kind": "capture_entity", "capture_id": 12, "entity_id": 3, "source": "extracted"}
```

`entity_kind`: `person | place | work | org | symbol | other`.
`source`: `extracted` (from tags/text) or `asserted` (added by hand).

### `edge`

```json
{"kind": "edge", "source": 14, "target": 12, "type": "asserted",
 "relation": "supersedes", "strength": 1.0, "method": "asserted",
 "note": "re-measured with the fixed rig", "asserted_by": "user"}
```

- `type`: `tag_overlap | entity_overlap | reference | asserted`
- `relation` is set only on asserted edges: `supersedes | refutes | narrows | supports`
- `asserted_by`: `user` (human assertion) or `derived` (computed)

## Import semantics

`import_backup` is **additive and non-destructive**:

- Captures colliding with an existing (volume, template ID) are skipped.
- Old `id` values are remapped; do not rely on them being stable.
- Only `asserted_by: "user"` edges are restored verbatim. Derived edges
  (tag/entity overlap, references) are recomputed over the merged base,
  so they stay consistent.

Restoring into an empty database reproduces the full base.
