"""
OCR layer for KSJ MCP server.

Two backends, selected by the KSJ_OCR_BACKEND environment variable:

  tesseract (default) — fully local, no data leaves the machine. Weak on
      cursive handwriting; fine for printed text.
  azure — Azure Document Intelligence (prebuilt-read), user-supplied
      endpoint + key via KSJ_AZURE_ENDPOINT / KSJ_AZURE_KEY. Sends each
      image to the user's own Azure resource. OFF unless explicitly
      enabled; intended for bulk imports of handwritten pages, where
      Tesseract output is unusable.

Wraps pytesseract with a clear, actionable error when Tesseract is not installed.
All public functions raise OcrNotAvailableError / CloudOcrConfigError rather
than letting a cryptic error bubble up to the MCP client.
"""

import os
import re
from pathlib import Path


class CloudOcrConfigError(RuntimeError):
    """Raised when a cloud OCR backend is selected but misconfigured."""


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


# ── Cloud backend: Azure Document Intelligence (prebuilt-read) ────────────────

_AZURE_API_VERSION = "2023-07-31"


def active_backend() -> str:
    """The OCR backend selected via KSJ_OCR_BACKEND (default: tesseract)."""
    return os.environ.get("KSJ_OCR_BACKEND", "").strip().lower() or "tesseract"


def _parse_azure_result(analyze_result: dict) -> tuple[str, float]:
    """Extract (text, avg word confidence 0-1) from an analyzeResult body."""
    text = analyze_result.get("content", "")
    confs = [
        w["confidence"]
        for page in analyze_result.get("pages", [])
        for w in page.get("words", [])
        if isinstance(w.get("confidence"), (int, float))
    ]
    confidence = (sum(confs) / len(confs)) if confs else (0.9 if text else 0.0)
    return text, confidence


def _run_azure_ocr(image_path: Path) -> tuple[str, float]:
    """
    Run Azure Document Intelligence prebuilt-read on *image_path*.

    Requires KSJ_AZURE_ENDPOINT and KSJ_AZURE_KEY. The image is sent to the
    user's own Azure resource — nothing else leaves the machine.
    """
    endpoint = os.environ.get("KSJ_AZURE_ENDPOINT", "").strip().rstrip("/")
    key      = os.environ.get("KSJ_AZURE_KEY", "").strip()
    if not endpoint or not key:
        raise CloudOcrConfigError(
            "KSJ_OCR_BACKEND=azure requires two more environment variables:\n"
            "  KSJ_AZURE_ENDPOINT  (e.g. https://<resource>.cognitiveservices.azure.com)\n"
            "  KSJ_AZURE_KEY       (a key for that Document Intelligence resource)\n"
            "Set them in the same env block as KSJ_OCR_BACKEND, or unset "
            "KSJ_OCR_BACKEND to use local Tesseract."
        )

    import json as _json
    import time
    import urllib.error
    import urllib.request

    url = (f"{endpoint}/formrecognizer/documentModels/prebuilt-read:analyze"
           f"?api-version={_AZURE_API_VERSION}")
    req = urllib.request.Request(
        url,
        data=image_path.read_bytes(),
        method="POST",
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/octet-stream",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            op_url = resp.headers.get("Operation-Location")
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Azure OCR request rejected (HTTP {e.code}). Check KSJ_AZURE_ENDPOINT "
            f"and KSJ_AZURE_KEY. Detail: {e.read().decode(errors='replace')[:300]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Azure OCR unreachable: {e.reason}") from e

    if not op_url:
        raise RuntimeError("Azure OCR did not return an Operation-Location header.")

    poll = urllib.request.Request(op_url, headers={"Ocp-Apim-Subscription-Key": key})
    for _ in range(60):
        time.sleep(1)
        with urllib.request.urlopen(poll, timeout=60) as resp:
            body = _json.loads(resp.read().decode())
        status = body.get("status")
        if status == "succeeded":
            return _parse_azure_result(body.get("analyzeResult", {}))
        if status == "failed":
            raise RuntimeError(f"Azure OCR analysis failed: {body.get('error', body)}")
    raise RuntimeError("Azure OCR timed out after 60s waiting for the analysis result.")


_BACKENDS = {
    "tesseract": _run_ocr,
    "azure":     _run_azure_ocr,
}


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

    backend = active_backend()
    runner = _BACKENDS.get(backend)
    if runner is None:
        raise CloudOcrConfigError(
            f"Unknown KSJ_OCR_BACKEND {backend!r} — supported values: "
            f"{', '.join(sorted(_BACKENDS))}. Unset it to use local Tesseract."
        )

    raw_text, confidence = runner(path)
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
