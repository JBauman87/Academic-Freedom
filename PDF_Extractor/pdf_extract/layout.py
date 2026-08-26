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


# A page whose surviving text is dominated by full-bleed scanned image(s)
# covering at least this fraction of the page area is treated as
# image-dominated, overriding the char-count-based has_text_layer signal
# below (see MAX_CAPTION_CHARS_ON_IMAGE_DOMINATED_PAGE). Real single-photo
# illustrations in an otherwise-text page cover only a modest fraction of
# the page; a genuinely scanned page reliably covers effectively the
# entire page (often slightly over 100% of the nominal page area, since
# scanners commonly capture a hair past the page's own trimmed edges).
IMAGE_DOMINATED_PAGE_MIN_AREA_FRAC = 0.5

# Even an image-dominated page (see above) is only reclassified as
# "no text layer" if its own extractable text is this short or shorter.
# Found via real documents in this corpus: a scanned page's PDF text layer
# sometimes carries a short caption ("Attachment # 1", 15 chars) added
# separately from the scan itself -- just past min_chars_for_text_layer,
# which otherwise wrongly marks the whole page as already having a usable
# text layer and skips OCR, discarding the real scanned content entirely.
# A genuine (non-image-dominated) text page's real body content is always
# far longer than this, so raising the bar only on image-dominated pages
# cannot misclassify one.
MAX_CAPTION_CHARS_ON_IMAGE_DOMINATED_PAGE = 60


def _page_image_area_fraction(page: "fitz.Page") -> float:
    """
    Fraction of the page's area covered by embedded image(s), summing each
    image's own bounding box (not deduplicated/clipped against overlaps or
    the page edge) -- a cheap approximation that's deliberately biased
    toward *overestimating* coverage for multiple/overlapping images, since
    the only decision this feeds is "does this page look like a scan",
    where overestimating a genuine multi-image scan page is harmless but
    underestimating it (and skipping OCR) is not.
    """
    page_area = page.rect.width * page.rect.height
    if not page_area:
        return 0.0
    total = 0.0
    for info in page.get_image_info():
        bbox = info.get("bbox")
        if not bbox:
            continue
        rect = fitz.Rect(bbox)
        total += rect.width * rect.height
    return total / page_area


def extract_layout(path: str, min_chars_for_text_layer: int = 10) -> DocumentLayout:
    """
    Open a PDF and extract per-page text blocks with positional metadata.

    A page is marked ``has_text_layer=False`` if it yields fewer than
    ``min_chars_for_text_layer`` characters of extractable text -- this is
    the signal used downstream to decide whether OCR fallback is needed.

    A page is ALSO marked ``has_text_layer=False`` -- even if it clears
    that char-count bar -- when it is dominated by full-page scanned
    image(s) (see IMAGE_DOMINATED_PAGE_MIN_AREA_FRAC) and its extractable
    text is no longer than a short caption (see
    MAX_CAPTION_CHARS_ON_IMAGE_DOMINATED_PAGE). Without this override, a
    scanned page carrying only a short caption in its real text layer
    (e.g. "Attachment # 1") would be wrongly treated as already having
    usable text, silently skipping OCR and losing the actual scanned
    content -- a real defect found in this corpus (a report's photocopied
    attachment pages, and a Brock/Isla report's scanned appendix pages).
    """
    doc = fitz.open(path)
    pages: List[PageLayout] = []

    try:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            width, height = page.rect.width, page.rect.height

            # PyMuPDF's get_text("dict") reports bounding boxes in the
            # page's raw/pre-rotation coordinate space (matching its
            # mediabox), not the rotated, "as displayed" space that
            # page.rect describes -- these differ whenever a page has a
            # nonzero /Rotate entry (e.g. a landscape-scanned letter
            # embedded at 90/270 degrees inside an otherwise-portrait
            # PDF, observed in real documents in this batch). Left
            # unaccounted for, every downstream x0_frac/y0_frac-based
            # heuristic (chrome stripping, column estimation, reading
            # order) silently breaks on such a page -- coordinates can
            # even exceed the nominal page width/height, since they're
            # being divided by the wrong axis. `page.rotation_matrix`
            # maps raw coordinates into the same rotated space page.rect
            # already describes, so every block's bbox is transformed
            # through it before any fraction is computed. On an
            # unrotated page this matrix is the identity, so this is a
            # no-op for the common case.
            rotation_matrix = page.rotation_matrix

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

                raw_bbox = fitz.Rect(raw_block.get("bbox", (0, 0, 0, 0)))
                rotated_bbox = raw_bbox * rotation_matrix
                # A rotation can flip which corner is "top-left" relative
                # to the raw coordinates; normalize so x0<=x1, y0<=y1 as
                # every downstream consumer expects.
                rotated_bbox.normalize()
                bbox = tuple(rotated_bbox)
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

            if (
                has_text_layer
                and char_count <= MAX_CAPTION_CHARS_ON_IMAGE_DOMINATED_PAGE
                and _page_image_area_fraction(page) >= IMAGE_DOMINATED_PAGE_MIN_AREA_FRAC
            ):
                has_text_layer = False

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
