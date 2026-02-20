"""
Template-aware field parsers for KSJ journal pages.

Each parser receives the raw OCR text from a journal page and returns a
normalized dict of the structured fields found on that template.

Template IDs and their right-page sections:
  RC  — First Impressions / Key Points / tags
  SYN — Breakthrough / Patterns / Connections / tags
  REV — Process Notes / Observations / tags
  DC  — Dream Narrative / Symbols / Emotions / tags
"""

import re
from typing import Any

# ── Schema tag extraction ─────────────────────────────────────────────────────

# Matches:  #topic  @source  !priority  ?question  $insight
_INLINE_TAG = re.compile(
    r'(?<!\w)'                      # not preceded by word char
    r'([#@!?$])'                    # prefix
    r'([\w][\w\-\.\/]*)',           # value (letters, digits, hyphen, dot, slash)
    re.UNICODE,
)

# Matches:  A→B  A->B  (cause-effect arrows)
_ARROW_TAG = re.compile(
    r'([\w\-\.]+)'                  # left side
    r'\s*(?:→|->)\s*'
    r'([\w\-\.]+)',                 # right side
    re.UNICODE,
)


def extract_schema_tags(text: str) -> list[dict[str, str]]:
    """
    Extract all schema-prefixed tags from *text*.

    Returns a list of dicts: [{"prefix": "#", "value": "machine-learning"}, ...]
    Arrow tags are stored as: {"prefix": "->", "value": "A->B"}
    """
    tags: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for m in _INLINE_TAG.finditer(text):
        prefix, value = m.group(1), m.group(2).lower()
        key = (prefix, value)
        if key not in seen:
            seen.add(key)
            tags.append({"prefix": prefix, "value": value})

    for m in _ARROW_TAG.finditer(text):
        left, right = m.group(1).lower(), m.group(2).lower()
        value = f"{left}->{right}"
        key = ("->", value)
        if key not in seen:
            seen.add(key)
            tags.append({"prefix": "->", "value": value})

    return tags


# ── Section splitter helper ───────────────────────────────────────────────────

def _extract_section(text: str, *headers: str) -> str:
    """
    Extract text between a section header and the next header or end of string.
    Case-insensitive. Returns the first matching section, stripped.
    """
    for header in headers:
        pattern = re.compile(
            rf'(?i){re.escape(header)}\s*[:\-]?\s*\n(.*?)(?=\n[A-Z][A-Z ]+[:\-]|\Z)',
            re.DOTALL,
        )
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return ""


def _build_summary(fields: dict[str, Any], max_len: int = 200) -> str:
    """Build a one-line summary from the most informative field."""
    for key in ("first_impressions", "breakthrough", "process_notes", "dream_narrative"):
        val = fields.get(key, "").strip()
        if val:
            return val[:max_len].replace("\n", " ")
    # Fallback: join non-empty fields
    parts = [v for v in fields.values() if isinstance(v, str) and v.strip()]
    return " | ".join(parts)[:max_len]


# ── Per-template parsers ──────────────────────────────────────────────────────

def parse_rc(text: str) -> dict[str, Any]:
    """Parse a Rapid Capture (RC) page."""
    return {
        "first_impressions": _extract_section(text, "first impressions", "impressions"),
        "key_points": _extract_section(text, "key points", "key point", "points"),
        "tags_raw": _extract_section(text, "tags", "tag"),
    }


def parse_syn(text: str) -> dict[str, Any]:
    """Parse a Synthesis (SYN) page."""
    return {
        "breakthrough": _extract_section(text, "breakthrough", "★ breakthrough", "★"),
        "patterns": _extract_section(text, "patterns", "pattern"),
        "connections_raw": _extract_section(text, "connections", "connection"),
        "tags_raw": _extract_section(text, "tags", "tag"),
    }


_STATUS_PATTERN = re.compile(
    r'\b(needs?\s+work|solid|mastered)\b',
    re.IGNORECASE,
)
_STATUS_SECTION = re.compile(
    r'(?i)knowledge\s+status\s*[:\-]?\s*(.+?)(?=\n|$)'
)
_STATUS_NORMALIZE = {
    "needs work": "Needs Work",
    "need work":  "Needs Work",
    "solid":      "Solid",
    "mastered":   "Mastered",
}


def _extract_knowledge_status(text: str) -> str:
    """
    Extract the Knowledge Status value (Needs Work / Solid / Mastered) from
    REV page OCR text.

    Checks:
      1. "Knowledge Status: Solid" style label + value on the same line
      2. Any occurrence of the three status terms as a fallback
    Returns one of "Needs Work", "Solid", "Mastered", or "" if not found.
    """
    # Try label + value first
    m = _STATUS_SECTION.search(text)
    if m:
        candidate = m.group(1).strip().lower()
        for key, normalized in _STATUS_NORMALIZE.items():
            if key in candidate:
                return normalized

    # Fallback: first occurrence of a status keyword anywhere in the text
    m = _STATUS_PATTERN.search(text)
    if m:
        return _STATUS_NORMALIZE.get(m.group(0).lower().replace("needs ", "needs ").strip(), "")

    return ""


def parse_rev(text: str) -> dict[str, Any]:
    """Parse a Review (REV) page."""
    return {
        "process_notes":    _extract_section(text, "process notes", "process", "notes"),
        "observations":     _extract_section(text, "observations", "observation"),
        "knowledge_status": _extract_knowledge_status(text),
        "tags_raw":         _extract_section(text, "tags", "tag"),
    }


def parse_dc(text: str) -> dict[str, Any]:
    """Parse a Dream Capture (DC) page."""
    return {
        "dream_narrative": _extract_section(text, "narrative", "dream narrative", "dream"),
        "symbols": _extract_section(text, "symbols", "symbol"),
        "emotions": _extract_section(text, "emotions", "emotion"),
        "tags_raw": _extract_section(text, "tags", "tag"),
    }


# ── Dispatcher ────────────────────────────────────────────────────────────────

_PARSERS = {
    "RC":  parse_rc,
    "SYN": parse_syn,
    "REV": parse_rev,
    "DC":  parse_dc,
}


def parse_template(template_type: str, raw_text: str) -> dict[str, Any]:
    """
    Parse *raw_text* using the appropriate template parser.

    Returns a dict with:
      - parsed content fields
      - "summary": auto-generated one-liner
      - "tags": list of schema tag dicts
    """
    parser = _PARSERS.get(template_type.upper())
    if parser is None:
        # Unknown template — return raw text as a single field
        fields: dict[str, Any] = {"raw": raw_text}
    else:
        fields = parser(raw_text)

    # Extract schema tags from the entire raw text (catches tags anywhere on the page)
    tags = extract_schema_tags(raw_text)
    summary = _build_summary(fields)

    return {
        "fields": fields,
        "summary": summary,
        "tags": tags,
    }
