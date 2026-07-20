"""
ocr.py

Optional OCR fallback for pages/documents that have no extractable text
layer (i.e. scanned images saved as PDF). This is a fallback path, not the
primary extraction route -- per the project's stated assumption, the vast
majority of source documents are exported/printed and already have a real
text layer, so this path should rarely trigger. It exists so the pipeline
degrades gracefully instead of silently producing an empty file when it
does encounter a scanned page.

Requires:
  - the `pytesseract` Python package (declared in requirements.txt)
  - the `tesseract` OCR binary installed on the system and on PATH

If either is unavailable, `ocr_available()` returns False and callers
should flag the document for manual review rather than fail outright.
"""

from __future__ import annotations

import shutil
from typing import Optional

import fitz  # PyMuPDF

try:
    import pytesseract
    from PIL import Image

    _PYTESSERACT_IMPORTED = True
except ImportError:  # pragma: no cover - exercised only when dep missing
    _PYTESSERACT_IMPORTED = False

# Render scanned pages at this resolution for OCR. Higher = more accurate,
# slower. 300 DPI is a standard sweet spot for OCR of printed text.
OCR_RENDER_DPI = 300


def ocr_available() -> bool:
    """True if both the pytesseract package and tesseract binary are usable."""
    if not _PYTESSERACT_IMPORTED:
        return False
    return shutil.which("tesseract") is not None


def ocr_page(pdf_path: str, page_index: int) -> Optional[str]:
    """
    Render a single page to an image and run OCR on it. Returns the
    recognized text, or None if OCR isn't available / the page couldn't be
    read.
    """
    if not ocr_available():
        return None

    doc = fitz.open(pdf_path)
    try:
        if page_index >= doc.page_count:
            return None
        page = doc.load_page(page_index)
        zoom = OCR_RENDER_DPI / 72.0  # PDF base resolution is 72 DPI
        matrix = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=matrix)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        text = pytesseract.image_to_string(img)
        return text.strip()
    finally:
        doc.close()


def ocr_document_pages(pdf_path: str, page_indices) -> dict:
    """
    OCR a set of pages in a document. Returns {page_index: text}. Pages
    that fail to OCR are simply omitted from the result (caller should
    treat missing pages as "needs manual review").
    """
    results = {}
    if not ocr_available():
        return results
    for page_index in page_indices:
        text = ocr_page(pdf_path, page_index)
        if text:
            results[page_index] = text
    return results
