"""
Tests for ksj_mcp.templates — parsers, tag extraction, knowledge_status.
"""

import pytest

from ksj_mcp.templates import (
    extract_schema_tags,
    parse_rc,
    parse_syn,
    parse_rev,
    parse_dc,
    parse_iso,
    parse_template,
    _extract_knowledge_status,
)


# ── extract_schema_tags ───────────────────────────────────────────────────────

def _has_tag(tags, prefix, value):
    return any(t["prefix"] == prefix and t["value"] == value for t in tags)


class TestExtractSchemaTags:
    def test_hash_tag(self):
        tags = extract_schema_tags("Learning #machine-learning today")
        assert _has_tag(tags, "#", "machine-learning")

    def test_at_tag(self):
        tags = extract_schema_tags("See also @RC-012 for context")
        assert _has_tag(tags, "@", "rc-012")

    def test_exclamation_tag(self):
        tags = extract_schema_tags("!urgent deadline approaching")
        assert _has_tag(tags, "!", "urgent")

    def test_star_tag_dc_sensory(self):
        tags = extract_schema_tags("*cold-wind rushing past")
        assert _has_tag(tags, "*", "cold-wind")

    def test_question_tag(self):
        tags = extract_schema_tags("?how-does-this-scale")
        assert _has_tag(tags, "?", "how-does-this-scale")

    def test_dollar_tag(self):
        tags = extract_schema_tags("$key-insight about attention")
        assert _has_tag(tags, "$", "key-insight")

    def test_arrow_tag_unicode(self):
        tags = extract_schema_tags("input→output transformation")
        values = [t["value"] for t in tags]
        assert "input->output" in values
        assert any(t["prefix"] == "->" for t in tags)

    def test_arrow_tag_ascii(self):
        tags = extract_schema_tags("cause->effect demonstrated here")
        values = [t["value"] for t in tags]
        assert "cause->effect" in values

    def test_deduplication(self):
        tags = extract_schema_tags("#ml concepts #ml applications")
        ml_tags = [t for t in tags if t["prefix"] == "#" and t["value"] == "ml"]
        assert len(ml_tags) == 1

    def test_case_folding(self):
        tags = extract_schema_tags("#MachineLearning and #machinelearning")
        ml_tags = [t for t in tags if t["prefix"] == "#" and t["value"] == "machinelearning"]
        assert len(ml_tags) == 1

    def test_multiple_types(self):
        text = "#topic @RC-001 ?question $insight !priority"
        tags = extract_schema_tags(text)
        prefixes = {t["prefix"] for t in tags}
        assert prefixes == {"#", "@", "?", "$", "!"}

    def test_empty_string(self):
        assert extract_schema_tags("") == []

    def test_no_tags(self):
        assert extract_schema_tags("plain text with no tags here") == []

    def test_tag_not_preceded_by_word_char(self):
        # "#tag" in middle of word should NOT match
        tags = extract_schema_tags("a#tag")
        assert not any(t["value"] == "tag" and t["prefix"] == "#" for t in tags)

    def test_dots_and_slashes_in_value(self):
        tags = extract_schema_tags("#topic.subtopic and #path/to/thing")
        values = [t["value"] for t in tags]
        assert "topic.subtopic" in values
        assert "path/to/thing" in values


# ── _extract_knowledge_status ─────────────────────────────────────────────────

class TestExtractKnowledgeStatus:
    def test_label_solid(self):
        text = "Knowledge Status: Solid\nsome other content"
        assert _extract_knowledge_status(text) == "Solid"

    def test_label_mastered(self):
        text = "Knowledge Status: Mastered"
        assert _extract_knowledge_status(text) == "Mastered"

    def test_label_needs_work(self):
        text = "Knowledge Status: Needs Work"
        assert _extract_knowledge_status(text) == "Needs Work"

    def test_label_case_insensitive(self):
        text = "knowledge status: solid"
        assert _extract_knowledge_status(text) == "Solid"

    def test_fallback_standalone_keyword(self):
        text = "Overall I think this is Solid now"
        assert _extract_knowledge_status(text) == "Solid"

    def test_fallback_mastered(self):
        text = "I have mastered this concept"
        assert _extract_knowledge_status(text) == "Mastered"

    def test_fallback_needs_work(self):
        text = "Still needs work on edge cases"
        assert _extract_knowledge_status(text) == "Needs Work"

    def test_not_found_returns_empty(self):
        assert _extract_knowledge_status("no status here") == ""

    def test_label_takes_priority(self):
        # Explicit label says Solid; a "Mastered" word appears later — label wins
        text = "Knowledge Status: Solid\nMastered the prereqs though"
        assert _extract_knowledge_status(text) == "Solid"


# ── Per-template parsers ──────────────────────────────────────────────────────

class TestParseRC:
    SAMPLE = (
        "RC-001\n"
        "First Impressions:\n"
        "Attention mechanism is fascinating\n"
        "Key Points:\n"
        "- Q, K, V matrices\n"
        "- Scaled dot-product\n"
        "Tags:\n"
        "#transformers #attention @paper-2017\n"
    )

    def test_first_impressions_extracted(self):
        result = parse_rc(self.SAMPLE)
        assert "fascinating" in result["first_impressions"]

    def test_key_points_extracted(self):
        result = parse_rc(self.SAMPLE)
        assert "Q, K, V" in result["key_points"]

    def test_tags_raw_extracted(self):
        result = parse_rc(self.SAMPLE)
        assert "#transformers" in result["tags_raw"]

    def test_missing_section_returns_empty_string(self):
        result = parse_rc("RC-001\nSome free-form text\n")
        assert result["first_impressions"] == ""


class TestParseSYN:
    SAMPLE = (
        "SYN-001\n"
        "Breakthrough:\n"
        "Attention = memory retrieval\n"
        "Patterns:\n"
        "Seen in multiple architectures\n"
        "Connections:\n"
        "@RC-001 @RC-005\n"
        "Tags:\n"
        "#attention $key-insight\n"
    )

    def test_breakthrough_extracted(self):
        result = parse_syn(self.SAMPLE)
        assert "memory retrieval" in result["breakthrough"]

    def test_patterns_extracted(self):
        result = parse_syn(self.SAMPLE)
        assert "multiple architectures" in result["patterns"]

    def test_connections_raw_extracted(self):
        result = parse_syn(self.SAMPLE)
        assert "@RC-001" in result["connections_raw"]

    def test_tags_raw_extracted(self):
        result = parse_syn(self.SAMPLE)
        assert "#attention" in result["tags_raw"]


class TestParseREV:
    SAMPLE = (
        "REV-001\n"
        "Process Notes:\n"
        "Reviewed 3 weeks of captures\n"
        "Observations:\n"
        "Cluster around #transformers\n"
        "Knowledge Status: Solid\n"
        "Tags:\n"
        "#transformers #review\n"
    )

    def test_process_notes_extracted(self):
        result = parse_rev(self.SAMPLE)
        assert "3 weeks" in result["process_notes"]

    def test_observations_extracted(self):
        result = parse_rev(self.SAMPLE)
        assert "#transformers" in result["observations"]

    def test_knowledge_status_extracted(self):
        result = parse_rev(self.SAMPLE)
        assert result["knowledge_status"] == "Solid"


class TestParseDC:
    SAMPLE = (
        "DC-001\n"
        "Narrative:\n"
        "Flying over a city made of code\n"
        "Symbols:\n"
        "Skyscraper, keyboard\n"
        "Emotions:\n"
        "Wonder, slight anxiety\n"
        "Tags:\n"
        "#flying #city\n"
    )

    def test_narrative_extracted(self):
        result = parse_dc(self.SAMPLE)
        assert "city made of code" in result["dream_narrative"]

    def test_symbols_extracted(self):
        result = parse_dc(self.SAMPLE)
        assert "Skyscraper" in result["symbols"]

    def test_emotions_extracted(self):
        result = parse_dc(self.SAMPLE)
        assert "Wonder" in result["emotions"]

    def test_current_events_extracted(self):
        text = (
            "DC-002\n"
            "Narrative:\nSomething strange\n"
            "Current Events:\nDeep in a research push on convergence proofs\n"
            "Symbols:\nNone\n"
            "Emotions:\nCurious\n"
            "Tags:\n#research\n"
        )
        result = parse_dc(text)
        assert "convergence proofs" in result["current_events"]

    def test_current_events_falls_back_to_legacy_yesterday_label(self):
        """§1.4a: the 2026-04 print revision renamed "Yesterday" to "Current
        Events" — the one pre-revision capture must still parse."""
        text = (
            "DC-000\n"
            "Narrative:\nAn old-template dream\n"
            "Yesterday:\nSpent all day debugging the sedenion code\n"
            "Symbols:\nNone\n"
            "Emotions:\nFrustrated\n"
            "Tags:\n#debugging\n"
        )
        result = parse_dc(text)
        assert "debugging the sedenion code" in result["current_events"]

    def test_current_events_absent_when_no_section(self):
        result = parse_dc(self.SAMPLE)
        assert result["current_events"] == ""


# ── Section layouts (F2, 2026-09-25 test pass) ────────────────────────────────

class TestSectionLayouts:
    """Assistants transcribe pages as "Label: value"; hand-written pages use
    labels alone on a line, with or without a colon, often ALL CAPS. Every
    layout must yield separate, non-bleeding fields."""

    def test_inline_labels_regression(self):
        r = parse_rc("RC-900\nTopic: QA\nFirst Impressions: testing ksj\n"
                     "Key Points: a b\nTags: #x")
        assert r["first_impressions"] == "testing ksj"
        assert r["key_points"] == "a b"
        assert r["tags_raw"] == "#x"

    def test_block_labels_without_colon_regression(self):
        r = parse_rc("RC-900\nFirst Impressions\ntesting ksj\nKey Points\na b\nTags\n#x")
        assert r["first_impressions"] == "testing ksj"
        assert r["key_points"] == "a b"
        assert r["tags_raw"] == "#x"

    def test_inline_content_continues_on_following_lines(self):
        r = parse_rc("First Impressions: line one\nline two\nKey Points: kp")
        assert r["first_impressions"] == "line one\nline two"

    def test_mixed_layouts(self):
        r = parse_rc("First Impressions:\nblock content\nKey Points: inline kp\n"
                     "Quick Questions\nwhy?\nTags: #t")
        assert r["first_impressions"] == "block content"
        assert r["key_points"] == "inline kp"
        assert r["quick_questions"] == "why?"

    def test_markdown_bold_and_heading_labels(self):
        r = parse_rc("**First Impressions:** bold label\n### Key Points\nkp")
        assert r["first_impressions"] == "bold label"
        assert r["key_points"] == "kp"

    def test_label_word_inside_prose_is_not_a_label(self):
        r = parse_rc("First Impressions:\nPoints to consider are many\n"
                     "my first impressions: mixed\nKey Points:\nkp")
        assert "Points to consider" in r["first_impressions"]
        assert "my first impressions" in r["first_impressions"]
        assert r["key_points"] == "kp"

    def test_bullet_with_label_word_stays_in_section(self):
        r = parse_rc("Key Points:\n- Notes: bullet stays\n- second")
        assert "bullet stays" in r["key_points"]
        assert "second" in r["key_points"]

    def test_metadata_labels_end_a_section(self):
        r = parse_iso("Subject: exploded view\nDate: 2026-09-01\nNotes: gear train")
        assert r["subject"] == "exploded view"
        assert r["notes"] == "gear train"

    def test_unknown_heading_with_colon_ends_section(self):
        r = parse_rc("Key Points:\n- kp one\nAction Items:\n- do a thing\nTags: #t")
        assert r["key_points"] == "- kp one"

    def test_line_with_content_after_colon_is_not_a_heading(self):
        r = parse_rc("First Impressions:\nPhase 4 - Grade -2: (27,3) - 81 dims\nKey Points:\nkp")
        assert "Grade -2: (27,3)" in r["first_impressions"]

    def test_all_caps_heading_ends_section(self):
        r = parse_syn("★ BREAKTHROUGH\nthe idea\nAI QUERIES\n? prior art\nTAGS\n#t")
        assert r["breakthrough"] == "the idea"
        assert r["tags_raw"] == "#t"

    def test_all_caps_line_with_digits_is_content(self):
        r = parse_rc("First Impressions:\nIGP24 = 56TH\nKey Points:\nkp")
        assert "IGP24" in r["first_impressions"]

    def test_inline_tags_line_does_not_swallow_the_page(self):
        text = ("SPRINT GOALS — May 21, 2026\nTags: #sprint #goals\n\n"
                "IMMEDIATE (this week):\n- fix the illustrate tool, blocks demos\n")
        result = parse_template("DC", text)
        assert result["fields"]["tags_raw"] == "#sprint #goals"
        values = {t["value"] for t in result["tags"]}
        assert values == {"sprint", "goals"}

    def test_patterns_discovered_label(self):
        r = parse_syn("PATTERNS DISCOVERED\n- one\n★ BREAKTHROUGH\nidea")
        assert r["patterns"] == "- one"
        assert r["breakthrough"] == "idea"

    def test_crlf_line_endings(self):
        r = parse_rc("First Impressions: a\r\nKey Points: b\r\n")
        assert r["first_impressions"] == "a"
        assert r["key_points"] == "b"


class TestSummaryFallback:
    def test_tags_are_never_the_summary(self):
        text = "### Phase 54: Structural Flare\n**Tags:**\n#rh #flare"
        assert parse_template("RC", text)["summary"] == "Phase 54: Structural Flare"

    def test_unlabeled_page_uses_first_content_line(self):
        text = "RC-003\nCORRECTION to AIEX-933. The v1 dial task was leaky."
        assert parse_template("RC", text)["summary"].startswith("CORRECTION to AIEX-933")

    def test_metadata_lines_skipped(self):
        text = "Date: 2026-05-21\nSource: me\nSPRINT GOALS — May 21\nTags: #sprint"
        assert parse_template("DC", text)["summary"] == "SPRINT GOALS — May 21"

    def test_placeholder_question_not_tagged(self):
        result = parse_template("RC", "Quick Questions:\n(none)\nTags: #t")
        assert not any(t["prefix"] == "?" for t in result["tags"])


# ── parse_template dispatcher ─────────────────────────────────────────────────

class TestParseTemplate:
    def test_rc_dispatch(self):
        text = "First Impressions:\nInteresting\nKey Points:\nPoint 1\nTags:\n#test"
        result = parse_template("RC", text)
        assert "fields" in result
        assert "summary" in result
        assert "tags" in result

    def test_tags_extracted_from_full_text(self):
        text = "First Impressions:\nSome content #ml\nKey Points:\nAnother $insight"
        result = parse_template("RC", text)
        prefixes = {t["prefix"] for t in result["tags"]}
        assert "#" in prefixes
        assert "$" in prefixes

    def test_unknown_type_falls_back_to_raw(self):
        result = parse_template("UNKNOWN", "some raw text")
        assert "raw" in result["fields"]
        assert result["fields"]["raw"] == "some raw text"

    def test_summary_uses_first_impressions_for_rc(self):
        text = "First Impressions:\nThis is the key insight here\nKey Points:\nOther stuff"
        result = parse_template("RC", text)
        assert "This is the key insight" in result["summary"]

    def test_summary_uses_breakthrough_for_syn(self):
        text = "Breakthrough:\nThe big idea\nPatterns:\nMore stuff"
        result = parse_template("SYN", text)
        assert "The big idea" in result["summary"]

    def test_lowercase_type_accepted(self):
        result = parse_template("rc", "First Impressions:\nSomething")
        assert "first_impressions" in result["fields"]

    def test_summary_truncated_to_200_chars(self):
        long_text = "First Impressions:\n" + "x" * 300
        result = parse_template("RC", long_text)
        assert len(result["summary"]) <= 200
