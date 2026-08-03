"""
Tests for OCR backend selection (KSJ_OCR_BACKEND) and the Azure adapter's
response parsing. No network calls — the HTTP path stays thin and untested.
"""

import pytest

from ksj_mcp.ocr import (
    CloudOcrConfigError,
    _parse_azure_result,
    _run_azure_ocr,
    active_backend,
    extract_text,
)


class TestBackendSelection:
    def test_default_is_tesseract(self, monkeypatch):
        monkeypatch.delenv("KSJ_OCR_BACKEND", raising=False)
        assert active_backend() == "tesseract"

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("KSJ_OCR_BACKEND", "Azure")
        assert active_backend() == "azure"

    def test_blank_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("KSJ_OCR_BACKEND", "  ")
        assert active_backend() == "tesseract"

    def test_unknown_backend_raises_clear_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("KSJ_OCR_BACKEND", "gcp")
        img = tmp_path / "page.jpg"
        img.write_bytes(b"fake")
        with pytest.raises(CloudOcrConfigError, match="gcp"):
            extract_text(img)


class TestAzureAdapter:
    def test_missing_config_raises(self, monkeypatch, tmp_path):
        monkeypatch.delenv("KSJ_AZURE_ENDPOINT", raising=False)
        monkeypatch.delenv("KSJ_AZURE_KEY", raising=False)
        img = tmp_path / "page.jpg"
        img.write_bytes(b"fake")
        with pytest.raises(CloudOcrConfigError, match="KSJ_AZURE_ENDPOINT"):
            _run_azure_ocr(img)

    def test_parse_result_content_and_confidence(self):
        body = {
            "content": "RC-001\nFirst impressions here",
            "pages": [{
                "words": [
                    {"content": "RC-001", "confidence": 0.9},
                    {"content": "First", "confidence": 0.7},
                ],
            }],
        }
        text, conf = _parse_azure_result(body)
        assert "RC-001" in text
        assert conf == pytest.approx(0.8)

    def test_parse_result_no_words(self):
        text, conf = _parse_azure_result({"content": "some text", "pages": []})
        assert text == "some text"
        assert conf == pytest.approx(0.9)

    def test_parse_empty_result(self):
        text, conf = _parse_azure_result({})
        assert text == ""
        assert conf == 0.0
