"""
OCR layer for KSJ MCP server.

Wraps pytesseract with a clear, actionable error when Tesseract is not installed.
All public functions raise OcrNotAvailableError rather than letting a cryptic
pytesseract error bubble up to Claude Desktop.
"""

import re
from pathlib import Path


class OcrNotAvailableError(RuntimeError):
    """Raised when Tesseract OCR binary is not installed or not found on PATH."""

    INSTALL_GUIDE = (
        "Tesseract OCR is not installed or not on your PATH.\n\n"
        "Install instructions:\n"
        "  Windows : https://github.com/UB-Mannheim/tesseract/wiki\n"
        "            (download the installer, accept default PATH option)\n"
        "  macOS   : brew install tesseract\n"
        "  Linux   : sudo apt install tesseract-ocr   # or equivalent\n\n"
        "After installing, restart Claude Desktop so the updated PATH is picked up.\n"
        "Then try uploading your capture again."
    )

    def __init__(self, original: Exception | None = None):
        detail = f" ({original})" if original else ""
        super().__init__(self.INSTALL_GUIDE + detail)


# ── Internal helpers ──────────────────────────────────────────────────────────

_WINDOWS_TESSERACT_CANDIDATES = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _configure_tesseract_path(pytesseract) -> None:
    """On Windows, auto-detect the Tesseract binary if it is not on PATH."""
    import shutil
    import sys

    if sys.platform != "win32":
        return
    if shutil.which("tesseract"):
        return  # already on PATH — nothing to do
    for candidate in _WINDOWS_TESSERACT_CANDIDATES:
        if Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            return


def _import_tesseract():
    """Lazy import so the server starts even when pytesseract is installed
    but the Tesseract binary itself is absent."""
    try:
        import pytesseract
        _configure_tesseract_path(pytesseract)
        return pytesseract
    except ImportError as e:
        raise OcrNotAvailableError(e) from e


def _run_ocr(image_path: Path) -> tuple[str, float]:
    """Run Tesseract on *image_path*, return (text, confidence 0-1)."""
    pytesseract = _import_tesseract()
    try:
        from PIL import Image
        img = Image.open(image_path)
        # Attempt to get per-word confidence data
        data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
        text = pytesseract.image_to_string(img)
        # Average confidence of words with conf > 0
        confs = [c for c in data["conf"] if isinstance(c, (int, float)) and c >= 0]
        confidence = (sum(confs) / len(confs) / 100.0) if confs else 0.0
        return text, confidence
    except pytesseract.TesseractNotFoundError as e:
        raise OcrNotAvailableError(e) from e
    except Exception as e:
        # Surface unexpected errors clearly
        raise RuntimeError(f"OCR failed on {image_path}: {e}") from e


# ── Template detection ────────────────────────────────────────────────────────

_TEMPLATE_PATTERNS = [
    (re.compile(r'\bRC-(\d{3})\b',  re.IGNORECASE), "RC"),
    (re.compile(r'\bSYN-(\d{3})\b', re.IGNORECASE), "SYN"),
    (re.compile(r'\bREV-(\d{3})\b', re.IGNORECASE), "REV"),
    (re.compile(r'\bDC-(\d{3})\b',  re.IGNORECASE), "DC"),
]


def detect_template_type(text: str) -> tuple[str, str]:
    """
    Scan OCR text for a template ID (e.g. RC-001).
    Returns (template_type, template_id) or ("UNKNOWN", "") if not found.
    """
    for pattern, ttype in _TEMPLATE_PATTERNS:
        m = pattern.search(text)
        if m:
            return ttype, f"{ttype}-{m.group(1)}"
    return "UNKNOWN", ""


# ── Public API ────────────────────────────────────────────────────────────────

def extract_text(image_path: str | Path) -> dict:
    """
    Run OCR on *image_path* and return a structured result dict.

    Returns:
        {
          "raw_text":      str,
          "template_type": str,   # RC | SYN | REV | DC | UNKNOWN
          "template_id":   str,   # e.g. RC-001 (empty if unknown)
          "confidence":    float, # 0.0 – 1.0
        }

    Raises:
        OcrNotAvailableError  if Tesseract is missing
        FileNotFoundError     if image_path does not exist
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    raw_text, confidence = _run_ocr(path)
    template_type, template_id = detect_template_type(raw_text)

    return {
        "raw_text": raw_text,
        "template_type": template_type,
        "template_id": template_id,
        "confidence": round(confidence, 3),
    }
