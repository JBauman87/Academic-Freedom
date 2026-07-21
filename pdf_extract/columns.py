"""
columns.py

Strips same-page navigation/sidebar/related-content chrome from PDFs that
are exported/printed web pages (e.g. news articles saved to PDF from a
browser). Unlike dedupe.py, this operates within a single page and doesn't
rely on repetition across pages -- a printed news article is often only
1-2 pages, so "repeats across most pages" isn't a usable signal here.

Three complementary heuristics are used, deliberately kept generic so they
don't overfit to any one outlet's specific wording or layout:

1. Column/geometry heuristic: on pages with a wide "main column" of body
   text, any block sitting mostly to the right of that column (a right
   rail) or that is clearly narrower and offset (a side card) is treated
   as secondary chrome, *provided* it is also short relative to the body
   text. This catches things like "Popular Now in News" lists and
   "Trending Videos" grids without needing to know their exact text.

2. Structural cue heuristic: a small, generic denylist of short phrases
   that overwhelmingly indicate UI chrome rather than article content
   (e.g. "Sign In", "Subscribe", "Advertisement", "Related Stories").
   This list is intentionally generic (not tuned to any single publisher)
   and is only ever applied to *short* blocks (<= SHORT_BLOCK_WORDS words),
   so it can never accidentally delete a real paragraph that happens to
   contain one of these words in passing.

3. Horizontal nav-row heuristic: several short blocks (e.g. "Home",
   "News", "Opinion", "Sports") sitting at roughly the same vertical
   position and together spanning a wide portion of the page width is
   the structural signature of a horizontal navigation bar, regardless of
   the specific site's section names. This complements heuristic 2 for
   nav items whose text isn't in the generic phrase denylist (a
   publication's actual section names vary), and complements the
   top-of-page strip heuristic below for nav bars positioned further down
   the page (e.g. below a masthead/logo banner) rather than hugging the
   very top edge.

Both heuristics are conservative by design: when in doubt, a block is kept.
False negatives (missed chrome) are far less costly than false positives
(deleted article text), and the confidence-flagging stage (confidence.py)
exists precisely to surface documents where a human should double check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Set

from .layout import PageLayout, TextBlock

# Blocks with this many words or fewer are eligible for the keyword-cue
# heuristic. This keeps the denylist from ever nuking a real paragraph.
SHORT_BLOCK_WORDS = 6

# A block is only eligible to be classified as a "side rail" by the geometry
# heuristic if it is no longer than this many words -- real article
# paragraphs run long, sidebar items are short (headlines, timestamps,
# durations like "2:57").
SIDE_RAIL_MAX_WORDS = 18

# A block sitting entirely within this top fraction of the page, and short
# enough, is treated as a masthead/nav-bar strip. This complements the
# phrase-based heuristic for cases where several nav items (e.g. "Menu",
# a site name, "Sign In") get merged into a single text block by the PDF's
# own layout because they sit on the same visual line -- a phrase match
# against the *whole* block's text would miss this, since the combined
# string doesn't equal any single denylist phrase.
TOP_STRIP_MAX_Y_FRAC = 0.07
TOP_STRIP_MAX_WORDS = 10

# Horizontal nav-row detection: short blocks whose vertical extents
# overlap (see _find_nav_row_block_ids) are clustered into rows. A
# cluster qualifies as a nav row if it contains at least NAV_ROW_MIN_ITEMS
# blocks that are each short (<= NAV_ROW_MAX_WORDS words) and, together,
# span at least NAV_ROW_MIN_SPAN_FRAC of the page width. This is a purely
# structural signal (many short items in a row, spread wide) with no
# dependency on specific wording, so it generalizes across publishers
# whose section names aren't in the generic phrase list.
NAV_ROW_MIN_ITEMS = 5
NAV_ROW_MAX_WORDS = 4
NAV_ROW_MIN_SPAN_FRAC = 0.5

# Generic UI/navigation/promo phrases. Matched as a whole-block match (after
# normalization) or as a standalone leading phrase, not as a substring, to
# avoid clipping legitimate sentences that happen to contain these words.
_GENERIC_CHROME_PHRASES = {
    "menu",
    "search",
    "sign in",
    "sign up",
    "log in",
    "subscribe",
    "advertisement",
    "sponsored",
    "sponsored content",
    "related",
    "related stories",
    "related articles",
    "you might also like",
    "recommended for you",
    "read more",
    "share",
    "share this article",
    "comments",
    "trending",
    "trending videos",
    "trending now",
    "popular now",
    "most popular",
    "most read",
    "top stories",
    "video",
    "listen to this article",
    "estimated",
    "facebook",
    "twitter",
    "instagram",
    "linkedin",
    "print",
    "email",
    "copy link",
    "skip to main content",
    "skip navigation",
    "cookie policy",
    "accept cookies",
    "manage preferences",
    "back to top",
}

# Durations like "2:57" or "30:13" (video length badges) and pure page
# furniture like lone numbers ("1", "2", "3" list rankings) are also generic
# giveaways of a "trending/popular" list rather than article prose.
_DURATION_RE = re.compile(r"^\d{1,2}:\d{2}$")
_LONE_RANK_RE = re.compile(r"^\d{1,2}$")


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower().strip(" \u2022-|:")


def _matches_generic_chrome(text: str) -> bool:
    norm = _normalize(text)
    if not norm:
        return False
    if norm in _GENERIC_CHROME_PHRASES:
        return True
    if _DURATION_RE.match(norm) or _LONE_RANK_RE.match(norm):
        return True
    # "Estimated 2 minutes" / "Estimated N minute(s)" read-time widgets
    if norm.startswith("estimated") and ("minute" in norm or "min" in norm):
        return True
    return False


@dataclass
class ColumnResult:
    removed_block_ids: Set[int]
    notes: List[str]


def _find_nav_row_block_ids(blocks: List[TextBlock]) -> Set[int]:
    """
    Detect a horizontal navigation bar: a group of short blocks whose
    vertical extents overlap (directly or transitively, e.g. a two-line
    wrapped nav item like "Arts &\nCulture" overlapping both a
    single-line item above and one below it) and which together span a
    wide portion of the page width (see NAV_ROW_* constants for
    thresholds). Returns the ids of blocks belonging to such a row, or an
    empty set if no page content matches this pattern.

    Overlap-based clustering (rather than a fixed tolerance around each
    block's vertical center) is used because real nav bars often mix
    single-line items with items that wrap onto two lines -- a fixed
    center-tolerance band can split those into separate "rows" purely
    because a two-line item's center sits slightly lower than its
    single-line neighbors.
    """
    eligible = [b for b in blocks if len(b.text.split()) <= NAV_ROW_MAX_WORDS]
    if len(eligible) < NAV_ROW_MIN_ITEMS:
        return set()

    # Union-find style transitive clustering by vertical (y0, y1) overlap.
    n = len(eligible)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            a, b = eligible[i], eligible[j]
            overlap = a.y0_frac < b.y1_frac and b.y0_frac < a.y1_frac
            if overlap:
                union(i, j)

    clusters: dict = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(eligible[i])

    removed: Set[int] = set()
    for group in clusters.values():
        if len(group) < NAV_ROW_MIN_ITEMS:
            continue
        x0 = min(b.x0_frac for b in group)
        x1 = max(b.x1_frac for b in group)
        if x1 - x0 >= NAV_ROW_MIN_SPAN_FRAC:
            removed.update(id(b) for b in group)
    return removed


# A block is only trusted as unambiguous "real body paragraph" evidence
# for the main-column estimate below if it's at least this long. Below
# this length, a block could plausibly be a sidebar teaser headline
# rather than an actual paragraph -- e.g. observed on a sparse news page
# where a real headline/caption ("U of T law students... concerned...",
# 88 chars) was barely longer than several "Related articles" teaser
# fragments (e.g. "3. Blues fall to Bold for", 25 chars), so simply
# taking the "top 25% by length" pulled sidebar text into the estimate
# and blew the main column out to ~92% of the page width -- silently
# disabling the sidebar-detection heuristic entirely on that page.
LONG_BLOCK_MIN_CHARS = 80

# Left-edge positions (as a fraction of page width) within this tolerance
# of each other are treated as "the same column" when falling back to the
# left-margin-clustering estimate below.
LEFT_EDGE_CLUSTER_TOLERANCE = 0.02


def _find_main_column_x_range(blocks: List[TextBlock]):
    """
    Estimate the horizontal span of the "main column" of real body text.

    Primary signal: blocks long enough (>= LONG_BLOCK_MIN_CHARS) to be
    unambiguously real paragraph text rather than a sidebar teaser or
    headline fragment. This is the strongest signal when available.

    Fallback: if a page has too few (or zero) such long blocks -- e.g. a
    sparse news page whose real content is just a short headline and
    caption -- fall back to whichever left-edge (x0) position the most
    blocks share (within a small tolerance). Real body text reliably sits
    at one consistent left margin even when individual blocks/lines are
    short; a sidebar column sits at a different, less-populated left
    edge. This avoids the failure mode where short-but-real blocks get
    outnumbered/out-ranked by short sidebar fragments under a pure
    length-ranking approach.
    """
    if not blocks:
        return None

    long_blocks = [b for b in blocks if len(b.text) >= LONG_BLOCK_MIN_CHARS]
    if long_blocks:
        x0 = min(b.x0_frac for b in long_blocks)
        x1 = max(b.x1_frac for b in long_blocks)
        return x0, x1

    # Fallback: cluster blocks by left edge, then prefer the *leftmost*
    # cluster with more than one block. This encodes a standard
    # Western-document-layout prior: the primary reading column starts at
    # (or very near) the page's left margin, while sidebars/rails/teaser
    # lists are conventionally positioned to the right of it. This is
    # deliberately preferred over "pick whichever cluster has the most
    # blocks/characters" -- a sidebar list can easily have more blocks
    # (many short teaser items) or even more total characters than a
    # sparse article's real content, which would make either of those
    # metrics pick the wrong cluster. Requiring more than one block
    # avoids a single stray far-left element (e.g. a lone page number or
    # decorative rule) being mistaken for the main column.
    clusters: List[List[TextBlock]] = []
    seen_ids: Set[int] = set()
    for candidate in sorted(blocks, key=lambda b: b.x0_frac):
        if id(candidate) in seen_ids:
            continue
        cluster = [
            b
            for b in blocks
            if abs(b.x0_frac - candidate.x0_frac) <= LEFT_EDGE_CLUSTER_TOLERANCE
        ]
        seen_ids.update(id(b) for b in cluster)
        clusters.append(cluster)

    best_cluster = next((c for c in clusters if len(c) > 1), None)
    if best_cluster is None:
        best_cluster = clusters[0] if clusters else []

    if not best_cluster:
        return None
    x0 = min(b.x0_frac for b in best_cluster)
    x1 = max(b.x1_frac for b in best_cluster)
    return x0, x1


def strip_page_chrome(page: PageLayout) -> ColumnResult:
    removed: Set[int] = set()
    notes: List[str] = []

    blocks = page.blocks
    if not blocks:
        return ColumnResult(removed_block_ids=removed, notes=notes)

    main_range = _find_main_column_x_range(blocks)
    nav_row_ids = _find_nav_row_block_ids(blocks)
    for block in blocks:
        if id(block) in nav_row_ids:
            removed.add(id(block))
            notes.append(f"nav-row: {block.text[:30]!r}")

    for block in blocks:
        if id(block) in removed:
            continue
        word_count = len(block.text.split())

        # Heuristic 1: generic chrome phrases (only on short blocks, so we
        # never risk deleting real sentences).
        if word_count <= SHORT_BLOCK_WORDS and _matches_generic_chrome(block.text):
            removed.add(id(block))
            notes.append(f"chrome-phrase: {block.text!r}")
            continue

        # Heuristic 2: geometry -- short block sitting well outside (to the
        # right of, or narrower/offset from) the estimated main column.
        if main_range and word_count <= SIDE_RAIL_MAX_WORDS:
            main_x0, main_x1 = main_range
            # Block starts clearly to the right of where body text ends.
            starts_right_of_body = block.x0_frac >= main_x1 - 0.03 and block.x0_frac > 0.55
            if starts_right_of_body:
                removed.add(id(block))
                notes.append(f"side-rail (x0_frac={block.x0_frac:.2f}): {block.text!r}")
                continue

        # Heuristic 3: masthead/nav strip -- a short block hugging the very
        # top of the page. Catches multi-item nav bars (e.g. "Menu" + site
        # name + "Sign In") that a PDF renderer merged into one block
        # because they share a baseline, which Heuristic 1's whole-block
        # phrase match would otherwise miss.
        if word_count <= TOP_STRIP_MAX_WORDS and block.y1_frac <= TOP_STRIP_MAX_Y_FRAC:
            removed.add(id(block))
            notes.append(f"top-strip (y1_frac={block.y1_frac:.2f}): {block.text!r}")
            continue

    return ColumnResult(removed_block_ids=removed, notes=notes)
