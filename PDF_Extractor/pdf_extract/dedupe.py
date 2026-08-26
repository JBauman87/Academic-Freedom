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

# Browser "print to PDF" chrome: most browsers stamp every printed page
# with a timestamp + page title header (e.g. "2024-01-05, 3:47 PM My
# Article Title") and a "Page N of M <url>" footer. This is a highly
# specific, generic-across-browsers signature -- not any one publisher's
# wording -- so it's safe to match structurally by shape by itself,
# independent of the general repeat-threshold logic below (which requires
# at least 3 pages to judge "what repeats"). A 2-page browser-printed
# article still carries this exact header/footer shape on both of its
# pages, but general boilerplate detection deliberately declines to
# draw conclusions from just 2 data points for genuine document content
# (e.g. a 2-page letter's letterhead) -- this narrower, shape-specific
# check is safe at 2 pages precisely because the pattern it matches
# (timestamp string; "Page N of M" + URL) essentially never occurs as
# real letter/article body content.
_PRINT_HEADER_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2},\s*\d{1,2}:\d{2}\s*(AM|PM)\b", re.IGNORECASE
)
_PRINT_FOOTER_RE = re.compile(r"^Page\s+\d+\s+of\s+\d+\b", re.IGNORECASE)

# Blocks longer than this many words are treated as "body text" and are
# never removed as repeated boilerplate, even if they happen to repeat
# (e.g. a boilerplate legal disclaimer that's genuinely long is unlikely,
# and we'd rather under-remove than accidentally eat real content).
MAX_BOILERPLATE_WORDS = 25

# Maximum drift (as a fraction of page width/height) between two blocks'
# bounding boxes for them to be treated as "the same position" when
# clustering same-text blocks across pages (see _cluster_by_position).
POSITION_TOLERANCE = 0.04

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


def _position_close(a: TextBlock, b: TextBlock, tol: float = POSITION_TOLERANCE) -> bool:
    """True if two blocks' bounding boxes are within `tol` of each other on
    all four edges (as a fraction of page width/height)."""
    return (
        abs(a.x0_frac - b.x0_frac) <= tol
        and abs(a.y0_frac - b.y0_frac) <= tol
        and abs(a.x1_frac - b.x1_frac) <= tol
        and abs(a.y1_frac - b.y1_frac) <= tol
    )


def _cluster_by_position(blocks: List[TextBlock]) -> List[List[TextBlock]]:
    """
    Group blocks (all already sharing the same normalized text) into
    clusters of mutually close position, using nearest-neighbor chaining
    rather than a fixed position grid.

    A fixed grid (rounding each coordinate to the nearest multiple of a
    bucket size) has a boundary-effect flaw: a running page-number stamp
    whose position drifts gradually across a long document (e.g. a
    170-page report where the footer's centered "- N -" page number
    shifts left/right by a few points as the digit count changes, 1 -> 2
    -> 3 digits) can drift enough, page over page, to cross a fixed grid
    boundary partway through the document. That splits what is genuinely
    one repeated footer into two (or more) separate position buckets, so
    neither bucket alone reaches the repeat-coverage threshold, and the
    footer survives into the output on the pages in the smaller bucket --
    observed on a real 171-page tribunal decision, where a footer page
    number appeared on 162/171 pages under one grid bucket and a further
    4/171 pages under an adjacent bucket it drifted into, neither of
    which alone reached the repeat threshold on its own, even though the
    footer as a whole clearly repeats on 166/171 (97%) of pages.
    Nearest-neighbor chaining tolerates this kind of gradual drift, since
    each block only needs to be close to *some* other block already in
    its cluster, not close to a single fixed grid cell.
    """
    clusters: List[List[TextBlock]] = []
    for block in blocks:
        matched_cluster = None
        for cluster in clusters:
            if any(_position_close(block, existing) for existing in cluster):
                matched_cluster = cluster
                break
        if matched_cluster is not None:
            matched_cluster.append(block)
        else:
            clusters.append([block])
    return clusters


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

    # Browser print-header/footer stamps (see _PRINT_HEADER_RE/_PRINT_FOOTER_RE
    # above) are checked unconditionally, even below the page-count floor
    # that gates the general repeated-boilerplate logic below -- the shape
    # of this specific pattern is distinctive enough that a single-page
    # match is still a safe removal, and it's the norm for a 2-page
    # browser-printed article (too few pages for the general "what
    # repeats across most pages" signal to safely apply) to carry this
    # exact stamp on every one of its pages.
    print_chrome_ids = set()
    print_chrome_signatures = []
    for page in text_bearing_pages:
        for block in page.blocks:
            first_line = block.text.split("\n", 1)[0].strip()
            if _PRINT_HEADER_RE.match(first_line) or _PRINT_FOOTER_RE.match(first_line):
                print_chrome_ids.add(id(block))
                print_chrome_signatures.append(f"{first_line[:60]!r} (print header/footer)")

    if total_pages < 3:
        # Not enough pages to meaningfully judge what "repeats" via the
        # general position-based signal below. A 1-2 page letter shouldn't
        # have its header stripped just because it appears on "all" of its
        # (very few) pages -- but the print-chrome stamps above are still
        # safe to remove regardless, since they're matched by distinctive
        # shape, not just by repetition.
        return DedupeResult(
            removed_block_ids=print_chrome_ids,
            signatures_removed=print_chrome_signatures,
        )

    # First group by normalized text alone, then cluster each text group
    # by position tolerance (see _cluster_by_position) rather than a fixed
    # grid, so a footer/header whose position drifts gradually across a
    # long document is still recognized as one repeated element.
    text_groups: Dict[str, List[TextBlock]] = defaultdict(list)
    for page in text_bearing_pages:
        for block in page.blocks:
            words = block.text.split()
            if len(words) > MAX_BOILERPLATE_WORDS:
                continue
            norm_text = _normalize_for_dedup(block.text)
            if not norm_text.strip():
                continue
            text_groups[norm_text].append(block)

    removed_ids = set(print_chrome_ids)
    signatures_removed = list(print_chrome_signatures)

    # Page indices actually present, split by parity -- used below to
    # detect an alternating recto/verso running header/footer (see its
    # handling in the loop).
    all_page_indices = [p.page_index for p in text_bearing_pages]
    even_page_indices = {i for i in all_page_indices if i % 2 == 0}
    odd_page_indices = {i for i in all_page_indices if i % 2 == 1}

    for norm_text, blocks in text_groups.items():
        for cluster in _cluster_by_position(blocks):
            pages_hit = {b.page_index for b in cluster}
            coverage = len(pages_hit) / total_pages
            if coverage >= repeat_threshold:
                for b in cluster:
                    removed_ids.add(id(b))
                signatures_removed.append(
                    f"{norm_text!r} (on {len(pages_hit)}/{total_pages} pages)"
                )
                continue

            # Alternating recto/verso running header/footer: many printed
            # books/journals put a different running header on
            # left-hand/right-hand pages (e.g. the journal name on even
            # pages, the article title on odd pages) -- both individually
            # repeat on only ~half the document, so neither ever reaches
            # `repeat_threshold` against the *whole* page count, even
            # though each is genuinely a repeated header/footer on every
            # page where it's structurally meant to appear. Checking
            # coverage against just the same-parity subset of pages
            # catches this without needing to hardcode "journal running
            # head" as a concept -- any text block that reliably recurs
            # at the same position on (most of) the even -or- odd pages
            # is boilerplate by the same logic as the whole-page check
            # above, just restricted to the relevant half of the
            # document. Requires at least 3 hits so two coincidentally
            # similar short blocks on a short document can't qualify.
            if len(pages_hit) < 3:
                continue
            even_hit = pages_hit & even_page_indices
            odd_hit = pages_hit & odd_page_indices
            for same_parity_pages, hit in (
                (even_page_indices, even_hit),
                (odd_page_indices, odd_hit),
            ):
                if not same_parity_pages or len(hit) < 3:
                    continue
                parity_coverage = len(hit) / len(same_parity_pages)
                if parity_coverage >= repeat_threshold:
                    for b in cluster:
                        if b.page_index in hit:
                            removed_ids.add(id(b))
                    signatures_removed.append(
                        f"{norm_text!r} (on {len(hit)}/{len(same_parity_pages)} "
                        f"same-parity pages, alternating header/footer)"
                    )
                    break

    return DedupeResult(removed_block_ids=removed_ids, signatures_removed=signatures_removed)
