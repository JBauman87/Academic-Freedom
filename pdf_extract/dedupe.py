"""
dedupe.py

Detects and removes text blocks that repeat across most/all pages of a
document at roughly the same position -- i.e. headers, footers, running
page numbers, and watermarks. This is done structurally (by looking at what
actually repeats in *this* document) rather than by hardcoding phrases like
"CanLII" or a specific court's citation format, so it generalizes across
rulings, reports, and letters from different sources.

Examples this is designed to catch, from the sample documents:
  - "Page: 2", "Page: 3", ... repeated at the top of every page of a ruling
    (digits vary, so matching is done on a digit-collapsed normal form)
  - "2016 ONSC 5747 (CanLII)" repeated as a rotated watermark down the right
    margin of every page
  - A report's running header/footer, e.g. "CAUT Report on Academic Freedom
    at the Faculty of Law, University of Toronto" + "October 2020" on every
    page, and "Canadian Association of University Teachers" + a page number
    in the footer.

Deliberately NOT handled here: one-off sidebars/nav on a single-page web
article (CBC-style). Those don't repeat across pages because there's only
one page to begin with -- that's columns.py / boilerplate.py's job.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .layout import DocumentLayout, TextBlock

_DIGIT_RE = re.compile(r"\d+")

# Blocks longer than this many words are treated as "body text" and are
# never removed as repeated boilerplate, even if they happen to repeat
# (e.g. a boilerplate legal disclaimer that's genuinely long is unlikely,
# and we'd rather under-remove than accidentally eat real content).
MAX_BOILERPLATE_WORDS = 25

# Position grid size (as a fraction of page width/height) used to decide
# whether two blocks on different pages are "in the same place."
POSITION_BUCKET = 0.04

# Fraction of (text-bearing) pages a block's (text, position) signature
# must appear on to be considered repeated boilerplate.
DEFAULT_REPEAT_THRESHOLD = 0.6


def _normalize_for_dedup(text: str) -> str:
    """Lowercase, collapse whitespace, and collapse digit runs to '#'.

    Collapsing digits means "Page: 2" and "Page: 3" are treated as the same
    repeating signature, which is essential for catching running page
    numbers/citations without hardcoding any specific format.
    """
    collapsed = " ".join(text.split()).lower()
    return _DIGIT_RE.sub("#", collapsed)


def _position_bucket(block: TextBlock) -> Tuple[int, int, int, int]:
    def bucket(frac: float) -> int:
        return round(frac / POSITION_BUCKET)

    return (
        bucket(block.x0_frac),
        bucket(block.y0_frac),
        bucket(block.x1_frac),
        bucket(block.y1_frac),
    )


@dataclass
class DedupeResult:
    removed_block_ids: set  # set of id(block) for blocks flagged as boilerplate
    signatures_removed: List[str]  # human-readable log of what was stripped


def find_repeated_boilerplate(
    doc: DocumentLayout,
    repeat_threshold: float = DEFAULT_REPEAT_THRESHOLD,
) -> DedupeResult:
    """
    Identify blocks that repeat across most pages at a stable position.

    Returns the set of block object ids to drop, plus a short log of the
    unique signatures removed (useful for debugging / manual review).
    """
    text_bearing_pages = [p for p in doc.pages if p.has_text_layer]
    total_pages = len(text_bearing_pages)
    if total_pages < 3:
        # Not enough pages to meaningfully judge what "repeats." A 1-2 page
        # letter shouldn't have its header stripped just because it appears
        # on "all" of its (very few) pages.
        return DedupeResult(removed_block_ids=set(), signatures_removed=[])

    # signature -> set of page indices it appears on, and one example block
    groups: Dict[Tuple[str, Tuple[int, int, int, int]], List[TextBlock]] = defaultdict(list)

    for page in text_bearing_pages:
        for block in page.blocks:
            words = block.text.split()
            if len(words) > MAX_BOILERPLATE_WORDS:
                continue
            sig = (_normalize_for_dedup(block.text), _position_bucket(block))
            groups[sig].append(block)

    removed_ids = set()
    signatures_removed = []

    for (norm_text, _pos), blocks in groups.items():
        pages_hit = {b.page_index for b in blocks}
        coverage = len(pages_hit) / total_pages
        if coverage >= repeat_threshold and norm_text.strip():
            for b in blocks:
                removed_ids.add(id(b))
            signatures_removed.append(
                f"{norm_text!r} (on {len(pages_hit)}/{total_pages} pages)"
            )

    return DedupeResult(removed_block_ids=removed_ids, signatures_removed=signatures_removed)
