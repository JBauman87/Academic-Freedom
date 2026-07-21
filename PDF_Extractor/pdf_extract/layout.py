"""
layout.py

Extracts text from a PDF as a list of positioned "blocks" rather than a flat
string. Each block records *where* it sits on the page (bounding box), which
page it's on, and some basic typographic info. Keeping this metadata around
is what lets later stages tell the difference between "this is the article
body" and "this is a page number in the corner" or "this is a right-rail
sidebar item" -- distinctions that are invisible once everything has been
flattened into plain text.

This module deliberately does NOT try to classify or filter anything. It is
a thin, faithful wrapper around PyMuPDF's block/line/span structures. All
the "is this junk" decisions live in dedupe.py, columns.py, and
boilerplate.py so they can be tested and tuned independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import fitz  # PyMuPDF


@dataclass
class TextBlock:
    """A single block of text as reported by PyMuPDF, with page context."""

    page_index: int          # 0-based page number
    page_width: float
    page_height: float
    bbox: tuple               # (x0, y0, x1, y1) in PDF points
    text: str                 # raw text of the block, newline-joined lines
    font_sizes: List[float] = field(default_factory=list)
    block_index: int = 0       # original order within the page (reading-ish)

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def avg_font_size(self) -> float:
        if not self.font_sizes:
            return 0.0
        return sum(self.font_sizes) / len(self.font_sizes)

    @property
    def normalized_text(self) -> str:
        """Collapsed whitespace, lowercased -- used for fuzzy matching."""
        return " ".join(self.text.split()).lower()

    @property
    def x0_frac(self) -> float:
        """x0 as a fraction of page width (0=left edge, 1=right edge)."""
        return self.x0 / self.page_width if self.page_width else 0.0

    @property
    def x1_frac(self) -> float:
        return self.x1 / self.page_width if self.page_width else 0.0

    @property
    def y0_frac(self) -> float:
        return self.y0 / self.page_height if self.page_height else 0.0

    @property
    def y1_frac(self) -> float:
        return self.y1 / self.page_height if self.page_height else 0.0


@dataclass
class PageLayout:
    page_index: int
    width: float
    height: float
    blocks: List[TextBlock]
    has_text_layer: bool  # False => page is likely a scanned image


@dataclass
class DocumentLayout:
    path: str
    pages: List[PageLayout]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def total_chars(self) -> int:
        return sum(len(b.text) for p in self.pages for b in p.blocks)

    @property
    def pages_without_text(self) -> List[int]:
        return [p.page_index for p in self.pages if not p.has_text_layer]


def extract_layout(path: str, min_chars_for_text_layer: int = 10) -> DocumentLayout:
    """
    Open a PDF and extract per-page text blocks with positional metadata.

    A page is marked ``has_text_layer=False`` if it yields fewer than
    ``min_chars_for_text_layer`` characters of extractable text -- this is
    the signal used downstream to decide whether OCR fallback is needed.
    """
    doc = fitz.open(path)
    pages: List[PageLayout] = []

    try:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            width, height = page.rect.width, page.rect.height

            raw = page.get_text("dict")
            blocks: List[TextBlock] = []

            for block_index, raw_block in enumerate(raw.get("blocks", [])):
                # type 0 = text block, type 1 = image block. We only care
                # about text here; image blocks (e.g. inline photos, logos)
                # carry no extractable text and are simply skipped.
                if raw_block.get("type") != 0:
                    continue

                lines_text = []
                font_sizes: List[float] = []
                for line in raw_block.get("lines", []):
                    spans = line.get("spans", [])
                    line_text = "".join(span.get("text", "") for span in spans)
                    if line_text.strip():
                        lines_text.append(line_text)
                    for span in spans:
                        size = span.get("size")
                        if size:
                            font_sizes.append(size)

                text = "\n".join(lines_text).strip()
                if not text:
                    continue

                bbox = tuple(raw_block.get("bbox", (0, 0, 0, 0)))
                blocks.append(
                    TextBlock(
                        page_index=page_index,
                        page_width=width,
                        page_height=height,
                        bbox=bbox,
                        text=text,
                        font_sizes=font_sizes,
                        block_index=block_index,
                    )
                )

            char_count = sum(len(b.text) for b in blocks)
            has_text_layer = char_count >= min_chars_for_text_layer

            pages.append(
                PageLayout(
                    page_index=page_index,
                    width=width,
                    height=height,
                    blocks=blocks,
                    has_text_layer=has_text_layer,
                )
            )
    finally:
        doc.close()

    return DocumentLayout(path=path, pages=pages)
