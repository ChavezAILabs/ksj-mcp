"""
Template-aware field parsers for KSJ journal pages.

Each parser receives the raw OCR text from a journal page and returns a
normalized dict of the structured fields found on that template.

Template IDs and their right-page sections:
  RC  — First Impressions / Key Points / tags
  SYN — Breakthrough / Patterns / Connections / tags
  REV — Process Notes / Observations / tags
  DC  — Dream Narrative / Symbols / Emotions / tags
  ISO — 3-D isometric grid page: Subject / Notes / tags (mostly drawing)
  WA  — Wild Art: no template of its own; parsed with the page type it was
        read as (if any) so promotion loses nothing, else kept as raw text
"""

import re
from typing import Any

# ── Schema tag extraction ─────────────────────────────────────────────────────

# Matches:  #topic  @source  !priority  ?question  $insight  *sensory (DC)
_INLINE_TAG = re.compile(
    r'(?<!\w)'                      # not preceded by word char
    r'([#@!?$*])'                   # prefix (* = DC sensory tag)
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

# @-values that look like a template ID are references; anything else is a
# named entity (dream symbol, person, place, work). The DC template shipped
# this convention on paper first — @symbol — and it generalizes.
_TEMPLATE_ID_VALUE = re.compile(r'^(RC|SYN|REV|DC|ISO|WA|AIEX)-?\d{1,4}[a-z]?$', re.IGNORECASE)


def normalize_tag_value(value: str) -> str:
    """
    Canonical tag form: casefold, collapse whitespace/underscores to a single
    hyphen, collapse hyphen runs. "DOG MAN", "Dog-Man", and "DOG-MAN" all
    normalize to "dog-man". The original string belongs in the tag's
    "display" field.
    """
    v = value.casefold().strip()
    v = re.sub(r'[\s_]+', '-', v)
    v = re.sub(r'-{2,}', '-', v)
    return v.strip('-')


def assign_role(prefix: str, value: str, template_type: str = "") -> str:
    """
    Canonical semantic role for a tag, derived from (prefix, template_type).

    The same prefix character means different things on DC pages than on
    RC/SYN/REV — the paper cannot change, so the meaning is recorded
    server-side. The literal prefix is always stored as written.
    """
    is_dc = template_type.upper() == "DC"
    if prefix == "#":
        return "theme" if is_dc else "topic"
    if prefix == "@":
        return "reference" if _TEMPLATE_ID_VALUE.match(value) else "entity"
    if prefix == "!":
        return "motif" if is_dc else "priority"
    if prefix == "?":
        return "question"
    if prefix == "$":
        return "insight"
    if prefix == "->":
        return "causal"
    if prefix == "*":
        return "sensory"
    return "topic"


def extract_schema_tags(text: str, template_type: str = "") -> list[dict[str, str]]:
    """
    Extract all schema-prefixed tags from *text*.

    Returns a list of dicts:
      [{"prefix": "#", "value": "machine-learning",
        "display": "Machine-Learning", "role": "topic"}, ...]
    Arrow tags are stored as: {"prefix": "->", "value": "a->b", ...}

    *template_type* drives role assignment for DC-variant prefixes; when
    omitted, the RC/SYN/REV meanings are used.
    """
    tags: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(prefix: str, raw_value: str) -> None:
        value = normalize_tag_value(raw_value)
        if not value:
            return
        key = (prefix, value)
        if key not in seen:
            seen.add(key)
            tags.append({
                "prefix":  prefix,
                "value":   value,
                "display": raw_value,
                "role":    assign_role(prefix, value, template_type),
            })

    for m in _INLINE_TAG.finditer(text):
        prefix = m.group(1)
        # '*' is both an OCR-noise artifact and the markdown emphasis
        # character. A '*' that is part of *emphasis* or **bold** is not a
        # sensory tag: skip when the match is bounded by another '*'.
        if prefix == "*":
            before = text[m.start() - 1] if m.start() > 0 else ""
            after  = text[m.end()] if m.end() < len(text) else ""
            if before == "*" or after == "*":
                continue
        _add(prefix, m.group(2))

    for m in _ARROW_TAG.finditer(text):
        _add("->", f"{m.group(1)}->{m.group(2)}")

    return tags


# ── Section splitter helper ───────────────────────────────────────────────────

# Every label that can open a section on any template, plus the metadata
# labels written above the sections (Date, Source, Subject...). A section
# ends at the next line that starts with ANY of these, so a stray metadata
# line never leaks into the field before it.
_KNOWN_HEADERS: tuple[str, ...] = (
    # RC
    "first impressions", "impressions", "key points", "key point", "points",
    "quick questions",
    # SYN
    "★ breakthrough", "breakthrough", "★", "patterns discovered",
    "patterns identified", "patterns", "pattern",
    "connections", "connection",
    # REV
    "process notes", "process", "observations", "observation",
    "knowledge status",
    # DC
    "dream narrative", "narrative", "dream", "symbols", "symbol",
    "emotions", "emotion", "current events", "yesterday",
    # ISO
    "subject", "title", "notes", "note", "description", "caption",
    # All templates (Action Items is written on RC pages even though the
    # parser doesn't keep it as a field; it must still end Key Points)
    "tags", "tag", "action items", "action item", "insights",
    # Metadata labels
    "date", "source(s)", "sources", "source", "topic", "session focus", "focus",
    "volume", "page",
)

# Optional markdown decoration before a label: "**Key Points:**", "### Tags".
# Bullets ("-", "•") are deliberately excluded — "- Notes: x" is a list item.
_LABEL_LEAD = r'[ \t]*(?:[*_#>]+[ \t]*)?'


def _label_regex(header: str) -> str:
    """
    Regex for *header* used as a label at the start of a line.

    A word label must be followed by a delimiter (":", "-", "–", "—") or the
    end of the line, so prose that merely begins with the word ("Points to
    consider...") is not a label. Symbol labels ("★") need no delimiter.
    """
    word = re.escape(header)
    if header[0].isalnum():
        tail = r'[ \t]*(?:[*_]+[ \t]*)?(?:[:\-–—][ \t]*(?:[*_]+[ \t]*)?|(?=\n|\Z))'
    else:
        tail = r'[ \t]*(?:[:\-–—][ \t]*)?'
    return rf'{_LABEL_LEAD}{word}{tail}'


# A section also ends at a heading line of its own — a short label ending in
# ":" with nothing after it ("Action Items:", "Patterns identified:",
# "IMMEDIATE (this week):", "**Insights:**"). Hand-written pages carry many
# labels no parser knows; this is what keeps them out of the section above.
# A line with content after its colon ("Phase 4 - Grade -2: (27,3)") is not
# a heading.
_HEADING_LINE = r'[ \t]*(?:[*_#>]+[ \t]*)?[\w$?★][^\n:]{0,60}:[ \t]*(?:[*_]+[ \t]*)?(?=\n|\Z)'

# Hand-written headings are often ALL CAPS with no colon ("AI QUERIES",
# "NEXT STEPS"). Letters only — a line with digits ("IGP24 = 56TH") is content.
_CAPS_HEADING_LINE = r"(?-i:[ \t]*[A-Z][A-Z &/()'’-]{3,40}[ \t]*(?=\n|\Z))"

_ANY_LABEL = re.compile(
    r'(?im)^(?:'
    + "|".join(_label_regex(h) for h in sorted(_KNOWN_HEADERS, key=len, reverse=True))
    + r'|' + _HEADING_LINE
    + r'|' + _CAPS_HEADING_LINE
    + r')'
)


def _extract_section(text: str, *headers: str) -> str:
    """
    Extract the content of the section introduced by the first of *headers*
    found as a line-start label (case-insensitive).

    Content may start on the label's own line ("Key Points: a, b") or on the
    lines below it, and runs until the next line that starts with any known
    label (_KNOWN_HEADERS) or the end of the text. Returns "" when no header
    is found.
    """
    text = text.replace("\r\n", "\n")
    for header in headers:
        m = re.search(rf'(?im)^{_label_regex(header)}', text)
        if not m:
            continue
        rest = text[m.end():]
        nl = rest.find("\n")
        stop = _ANY_LABEL.search(rest, nl + 1) if nl != -1 else None
        return (rest[:stop.start()] if stop else rest).strip()
    return ""


# Fields that list tags or links — never a summary of the page.
_NON_SUMMARY_FIELDS = {"tags_raw", "connections_raw"}

_TEMPLATE_ID_LINE = re.compile(r'^(RC|SYN|REV|DC|ISO|WA|AIEX)-?\d{1,4}[a-z]?\b', re.IGNORECASE)


def _first_content_line(raw_text: str) -> str:
    """First line of *raw_text* that says something: not blank, not a bare
    template ID, not a metadata/section label. Markdown heading and bold
    markers are stripped."""
    for line in raw_text.replace("\r\n", "\n").split("\n"):
        s = line.strip()
        if not s or _ANY_LABEL.match(line):
            continue
        s = re.sub(r'^[#>*_\s]+', '', s).replace("**", "").strip()
        if not s or (_TEMPLATE_ID_LINE.match(s) and len(s) <= 12):
            continue
        return s
    return ""


def _build_summary(fields: dict[str, Any], raw_text: str = "", max_len: int = 200) -> str:
    """
    Build a one-line summary from the most informative field.

    Order: the template's lead field (First Impressions, Breakthrough, ...),
    then any other content field, then the first meaningful line of the page.
    Tag and connection lists are never used — a list of tags is not a summary.
    """
    for key in ("first_impressions", "breakthrough", "process_notes", "dream_narrative",
                "subject"):
        val = fields.get(key, "").strip()
        if val:
            return val[:max_len].replace("\n", " ")
    parts = [v for k, v in fields.items()
             if k not in _NON_SUMMARY_FIELDS and isinstance(v, str) and v.strip()]
    if parts:
        return " | ".join(parts)[:max_len].replace("\n", " ")
    return _first_content_line(raw_text)[:max_len]


# ── Per-template parsers ──────────────────────────────────────────────────────

def parse_rc(text: str) -> dict[str, Any]:
    """Parse a Rapid Capture (RC) page."""
    return {
        "first_impressions": _extract_section(text, "first impressions", "impressions"),
        "key_points": _extract_section(text, "key points", "key point", "points"),
        "quick_questions": _extract_section(text, "quick questions"),
        "tags_raw": _extract_section(text, "tags", "tag"),
    }


def parse_syn(text: str) -> dict[str, Any]:
    """Parse a Synthesis (SYN) page."""
    return {
        "breakthrough": _extract_section(text, "breakthrough", "★ breakthrough", "★"),
        "patterns": _extract_section(text, "patterns", "pattern", "patterns discovered",
                                     "patterns identified"),
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
        # §1.4a: the 2026-04 print revision renamed this section from
        # "Yesterday" to "Current Events" — parse the current label, fall
        # back to the retired one so the one pre-revision capture still
        # parses. No alias table needed; _extract_section already tries
        # headers in order.
        "current_events": _extract_section(text, "current events", "yesterday"),
        "tags_raw": _extract_section(text, "tags", "tag"),
    }


def _extract_inline(text: str, *labels: str) -> str:
    """Value written on the same line as its label ("Subject: exploded view")."""
    for label in labels:
        m = re.search(rf'(?im)^\s*{re.escape(label)}\s*[:\-]\s*(\S.*)$', text)
        if m:
            return m.group(1).strip()
    return ""


def parse_iso(text: str) -> dict[str, Any]:
    """
    Parse a 3-D isometric grid (ISO) page. These pages are mostly drawing —
    the text is whatever labels, subject line, and notes accompany it. A
    subject is usually one line, so same-line values count too.
    """
    return {
        "subject": (_extract_section(text, "subject", "title")
                    or _extract_inline(text, "subject", "title")),
        "notes":   (_extract_section(text, "notes", "note", "description", "caption")
                    or _extract_inline(text, "notes", "note", "description", "caption")),
        "tags_raw": _extract_section(text, "tags", "tag") or _extract_inline(text, "tags", "tag"),
    }


# ── Dispatcher ────────────────────────────────────────────────────────────────

_PARSERS = {
    "RC":  parse_rc,
    "SYN": parse_syn,
    "REV": parse_rev,
    "DC":  parse_dc,
    "ISO": parse_iso,
}


_PLACEHOLDERS = {"none", "n/a", "na", "nil", "tbd", ""}


def _positional_tags(
    fields: dict[str, Any],
    template_type: str,
    seen: set[tuple[str, str]],
) -> list[dict[str, str]]:
    """
    Tags implied by *where* content was written, not by a prefix character.

    - Content in the Tags section (the printed tag bubbles) is a tag whether
      or not the user wrote the '#' — the bubble is positional evidence.
    - Content in Quick Questions is a '?' question whether or not the
      character was written.
    """
    extra: list[dict[str, str]] = []

    def _add(prefix: str, raw: str) -> None:
        if raw.strip("()[]-. ").casefold() in _PLACEHOLDERS:
            return  # "(none)", "n/a" — an empty section, not a tag
        value = normalize_tag_value(raw)
        if not value or (prefix, value) in seen:
            return
        seen.add((prefix, value))
        extra.append({
            "prefix":  prefix,
            "value":   value,
            "display": raw,
            "role":    assign_role(prefix, value, template_type),
        })

    # Tag bubbles: split on separators; a single space stays inside one tag
    # ("DOG MAN" is one bubble, not two tags).
    for cand in re.split(r'[\n,;|•]+|\s{2,}', fields.get("tags_raw", "") or ""):
        cand = cand.strip()
        if not cand or cand[0] in "#@!?$*":
            continue  # prefixed content is handled by the inline extractor
        if not re.search(r'\w{2,}', cand):
            continue  # OCR noise / stray marks
        if len(cand) > 40 or len(cand.split()) > 4:
            continue  # prose that leaked into the section, not a tag
        _add("#", cand)

    # Quick Questions: each line is a question.
    for line in (fields.get("quick_questions", "") or "").splitlines():
        line = line.strip().lstrip("?").strip()
        if not re.search(r'\w{2,}', line):
            continue
        _add("?", line)

    return extra


def parse_template(template_type: str, raw_text: str) -> dict[str, Any]:
    """
    Parse *raw_text* using the appropriate template parser.

    Returns a dict with:
      - parsed content fields
      - "summary": auto-generated one-liner
      - "tags": list of schema tag dicts (prefix, value, display, role)
    """
    parser = _PARSERS.get(template_type.upper())
    if parser is None:
        # Unknown template — return raw text as a single field
        fields: dict[str, Any] = {"raw": raw_text}
    else:
        fields = parser(raw_text)

    # Extract schema tags from the entire raw text (catches tags anywhere on the page)
    tags = extract_schema_tags(raw_text, template_type)
    seen = {(t["prefix"], t["value"]) for t in tags}
    tags.extend(_positional_tags(fields, template_type, seen))
    summary = _build_summary(fields, raw_text)

    return {
        "fields": fields,
        "summary": summary,
        "tags": tags,
    }
