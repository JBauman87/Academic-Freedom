"""
reassemble.py

Takes the surviving (non-boilerplate, non-chrome) blocks for a document and
reassembles them into clean, reading-order text.

Reading order is determined per-page using a simplified recursive "XY-cut":

  1. Try to split the page's blocks into a top band and a bottom band at a
     horizontal gap where no block's vertical extent crosses the gap (i.e.
     nothing straddles it). This correctly stacks e.g. a full-width header
     above a multi-column body, or separates unrelated vertically-stacked
     sections, without needing to know column widths in advance.
  2. If no such horizontal gap exists, try to split into a left and right
     column at a vertical gap where no block's horizontal extent crosses
     it. This handles genuine side-by-side layouts (a two-column body, a
     letter's letterhead column beside its body, a court document's
     party/counsel block).
  3. Recurse into each resulting group until no further clean split is
     possible, at which point the remaining blocks are sorted by top edge
     (y0) then left edge (x0).

This replaces a simpler "sort by vertical band, then x" approach that broke
down on pages mixing very differently-sized blocks -- e.g. a decorative
title page with large stylized drop-cap-style letters running down the
left margin beside normal-sized wrapped subtitle text. There, a tall
block's *center* could land in the middle of several shorter blocks below
it, causing words to be interleaved/torn apart in the output. Sorting by
top edge within a properly split column avoids that failure mode: a tall
block's top edge is compared against other blocks' top edges directly,
rather than an averaged center point.

The algorithm is deliberately conservative: a split is only made when a
genuinely clean gap exists (nothing straddles it), so it never invents
columns or bands that aren't really there. When no clean split exists, it
falls back to the same safe top-to-bottom-then-left-to-right ordering as
before, so ordinary single-column text is unaffected.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .layout import DocumentLayout, TextBlock

# A vertical (column) gap must be at least this fraction of the page width
# to be treated as a genuine column boundary, rather than incidental
# whitespace/indentation between blocks that aren't actually in separate
# columns. Horizontal (row) gaps don't need this safeguard: any gap there
# already implies non-overlapping vertical extents, which is a reliable
# signal on its own.
MIN_VERTICAL_GAP_FRAC = 0.02


def _merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Merge overlapping/touching (start, end) intervals, sorted by start."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _find_gap(intervals: List[Tuple[float, float]], min_gap: float = 0.0) -> Optional[float]:
    """
    Given a set of (start, end) intervals, find the largest gap between
    merged clusters of overlapping intervals and return a cut point (the
    midpoint of that gap). Returns None if there's no such gap at least
    ``min_gap`` wide -- i.e. the intervals form one contiguous block with
    no clean separation.
    """
    merged = _merge_intervals(intervals)
    if len(merged) < 2:
        return None
    best_cut = None
    best_gap = min_gap
    for (_, end), (next_start, _) in zip(merged, merged[1:]):
        gap = next_start - end
        if gap > best_gap:
            best_gap = gap
            best_cut = (end + next_start) / 2
    return best_cut


def _order_blocks(blocks: List[TextBlock], page_width: float) -> List[TextBlock]:
    """
    Recursively order a set of same-page blocks into reading order via a
    simplified XY-cut (see module docstring).
    """
    if len(blocks) <= 1:
        return list(blocks)

    # Prefer a horizontal (row) split: nothing here straddles the gap by
    # construction, so this is always safe when one exists.
    y_gap = _find_gap([(b.y0, b.y1) for b in blocks])
    if y_gap is not None:
        top = [b for b in blocks if b.y1 <= y_gap]
        bottom = [b for b in blocks if b.y0 >= y_gap]
        if top and bottom and len(top) + len(bottom) == len(blocks):
            return _order_blocks(top, page_width) + _order_blocks(bottom, page_width)

    # Otherwise, try a vertical (column) split, requiring a genuine gutter.
    min_gap = page_width * MIN_VERTICAL_GAP_FRAC
    x_gap = _find_gap([(b.x0, b.x1) for b in blocks], min_gap=min_gap)
    if x_gap is not None:
        left = [b for b in blocks if b.x1 <= x_gap]
        right = [b for b in blocks if b.x0 >= x_gap]
        if left and right and len(left) + len(right) == len(blocks):
            return _order_blocks(left, page_width) + _order_blocks(right, page_width)

    # No clean split available -- fall back to top edge, then left edge.
    # (Using the top edge rather than a vertical center is itself part of
    # the fix: it prevents a tall block from being sorted into the middle
    # of shorter blocks that merely happen to average out to a similar
    # center position.)
    return sorted(blocks, key=lambda b: (b.y0, b.x0))


def reassemble_text(doc: DocumentLayout, kept_block_ids: set) -> str:
    """
    Join all blocks whose id() is in kept_block_ids into a single text, in
    reading order, with paragraph breaks preserved. Reading order is
    computed independently per page via ``_order_blocks``.
    """
    paragraphs: List[str] = []
    any_page_emitted = False

    for page in doc.pages:
        page_blocks = [
            b for b in page.blocks if id(b) in kept_block_ids and b.text.strip()
        ]
        if not page_blocks:
            continue

        ordered = _order_blocks(page_blocks, page.width)

        if any_page_emitted:
            paragraphs.append("")
        for block in ordered:
            paragraphs.append(block.text.strip())
        any_page_emitted = True

    # Collapse any accidental doubled blank separators between paragraphs.
    cleaned: List[str] = []
    for para in paragraphs:
        if para == "" and (not cleaned or cleaned[-1] == ""):
            continue
        cleaned.append(para)
    return "\n\n".join(cleaned).strip()
