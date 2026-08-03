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


# ── Template detection (tiered) ───────────────────────────────────────────────
#
# Three tiers with confidence:
#   1.0 — strict match (RC-001, optionally with a page suffix: RC-002p)
#   0.6 — loose match after OCR-confusion normalization (RC 7, SVN-3, RC-OO2)
#   0.0 — nothing found: the page should still be stored, as unidentified

# Common OCR digit confusions — applied ONLY to the captured digit group,
# never to page body text.
_ID_CONFUSIONS = str.maketrans({
    'O': '0', 'o': '0', 'D': '0', 'Q': '0',
    'l': '1', 'I': '1', '|': '1', 'i': '1',
    'S': '5', 's': '5', 'B': '8', 'Z': '2', 'z': '2', 'G': '6',
})

_STRICT = re.compile(r'\b(RC|SYN|REV|DC)-(\d{3})([a-z])?\b', re.IGNORECASE)

_LOOSE = re.compile(
    r'(?:(?:V|VOL|BOOK)\s*(\d+)\s*[-\s])?'       # optional volume marker (V2-RC-001)
    r'(?<![A-Za-z])'                              # not inside a word (ARC4 ≠ RC-004)
    r'(RC|SYN|REV|DC|R[C(]|SVN|S7N|REU|0C)'       # prefix incl. OCR variants
    r'[\s\-‐-―_.:]*'                    # any separator or none
    r'([0-9OoIlDQSsBZzG|]{1,3})'                  # digits pre-normalization
    r'\s*([a-z])?\b',                             # optional page suffix
    re.IGNORECASE,
)

_PREFIX_CANON = {
    "RC": "RC", "R(": "RC",
    "SYN": "SYN", "SVN": "SYN", "S7N": "SYN",
    "REV": "REV", "REU": "REV",
    "DC": "DC", "0C": "DC",
}


def parse_template_id(text: str) -> dict:
    """
    Tiered template-ID parse.

    Returns:
        {
          "template_type": "RC" | "SYN" | "REV" | "DC" | "UNKNOWN",
          "template_id":   str,          # normalized "RC-001" (empty if unknown)
          "page_suffix":   str | None,   # stray trailing letter, preserved not interpreted
          "volume":        int | None,   # only when written on the page (V2 ...)
          "id_confidence": float,        # 1.0 strict / 0.6 loose / 0.0 none
        }
    """
    m = _STRICT.search(text)
    if m:
        ttype = m.group(1).upper()
        return {
            "template_type": ttype,
            "template_id":   f"{ttype}-{int(m.group(2)):03d}",
            "page_suffix":   (m.group(3) or None),
            "volume":        None,
            "id_confidence": 1.0,
        }

    m = _LOOSE.search(text)
    if m:
        prefix = _PREFIX_CANON.get(m.group(2).upper())
        digits = m.group(3).translate(_ID_CONFUSIONS)
        if prefix and digits.isdigit():
            return {
                "template_type": prefix,
                "template_id":   f"{prefix}-{int(digits):03d}",
                "page_suffix":   (m.group(4) or None),
                "volume":        int(m.group(1)) if m.group(1) else None,
                "id_confidence": 0.6,
            }

    return {
        "template_type": "UNKNOWN",
        "template_id":   "",
        "page_suffix":   None,
        "volume":        None,
        "id_confidence": 0.0,
    }


def detect_template_type(text: str) -> tuple[str, str]:
    """
    Scan OCR text for a template ID (e.g. RC-001).
    Returns (template_type, template_id) or ("UNKNOWN", "") if not found.

    Backward-compatible wrapper around parse_template_id() — accepts both
    strict and loose (confusion-normalized) matches.
    """
    parsed = parse_template_id(text)
    return parsed["template_type"], parsed["template_id"]


# ── Public API ────────────────────────────────────────────────────────────────

def extract_text(image_path: str | Path) -> dict:
    """
    Run OCR on *image_path* and return a structured result dict.

    Returns:
        {
          "raw_text":      str,
          "template_type": str,   # RC | SYN | REV | DC | UNKNOWN
          "template_id":   str,   # e.g. RC-001 (empty if unknown)
          "page_suffix":   str | None,
          "volume":        int | None,   # only if written on the page
          "id_confidence": float, # 1.0 strict / 0.6 loose / 0.0 none
          "confidence":    float, # OCR confidence 0.0 – 1.0
        }

    Raises:
        OcrNotAvailableError  if Tesseract is missing
        FileNotFoundError     if image_path does not exist
    """
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    raw_text, confidence = _run_ocr(path)
    parsed = parse_template_id(raw_text)

    return {
        "raw_text": raw_text,
        "template_type": parsed["template_type"],
        "template_id": parsed["template_id"],
        "page_suffix": parsed["page_suffix"],
        "volume": parsed["volume"],
        "id_confidence": parsed["id_confidence"],
        "confidence": round(confidence, 3),
    }
