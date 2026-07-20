"""
reassemble.py

Takes the surviving (non-boilerplate, non-chrome) blocks for a document and
reassembles them into clean, reading-order text.

Reading order is approximated by sorting blocks top-to-bottom, and within a
horizontal band, left-to-right -- which handles the common multi-column
legal-document case (e.g. the "BETWEEN: ... Applicants ... Respondents"
party block in the sample ruling, which is laid out as two side-by-side
columns) reasonably well without needing a full column-detection model.
"""

from __future__ import annotations

from typing import List

from .layout import DocumentLayout, TextBlock

# Blocks whose vertical centers are within this fraction of page height are
# considered to be "on the same line/band" for left-to-right ordering.
BAND_TOLERANCE = 0.01


def _sort_key(block: TextBlock):
    y_center = (block.y0_frac + block.y1_frac) / 2
    band = round(y_center / BAND_TOLERANCE)
    return (block.page_index, band, block.x0_frac, block.y0)


def reassemble_text(doc: DocumentLayout, kept_block_ids: set) -> str:
    """
    Join all blocks whose id() is in kept_block_ids into a single text,
    in approximate reading order, with paragraph breaks preserved.
    """
    kept: List[TextBlock] = []
    for page in doc.pages:
        for block in page.blocks:
            if id(block) in kept_block_ids:
                kept.append(block)

    kept.sort(key=_sort_key)

    paragraphs = []
    last_page = None
    for block in kept:
        text = block.text.strip()
        if not text:
            continue
        if last_page is not None and block.page_index != last_page:
            # Optional page-break marker; kept subtle so it doesn't clutter
            # plain-text output but still helps a human locate content.
            paragraphs.append("")
        paragraphs.append(text)
        last_page = block.page_index

    # Collapse 3+ blank lines down to a single blank line between
    # paragraphs, and strip leading/trailing whitespace overall.
    joined = "\n\n".join(p for p in paragraphs if p != "" or True)
    # Remove accidental runs of blank paragraphs created by page breaks
    # sitting next to already-blank content.
    lines = joined.split("\n\n")
    cleaned: List[str] = []
    for line in lines:
        if line == "" and (not cleaned or cleaned[-1] == ""):
            continue
        cleaned.append(line)
    result = "\n\n".join(cleaned).strip()
    return result
