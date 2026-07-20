"""
columns.py

Strips same-page navigation/sidebar/related-content chrome from PDFs that
are exported/printed web pages (e.g. news articles saved to PDF from a
browser). Unlike dedupe.py, this operates within a single page and doesn't
rely on repetition across pages -- a printed news article is often only
1-2 pages, so "repeats across most pages" isn't a usable signal here.

Two complementary heuristics are used, deliberately kept generic so they
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


def _find_main_column_x_range(blocks: List[TextBlock]):
    """
    Estimate the horizontal span of the "main column" using the longest
    (by character count) blocks on the page, which are overwhelmingly
    likely to be actual body paragraphs rather than UI chrome.
    """
    if not blocks:
        return None
    candidates = sorted(blocks, key=lambda b: len(b.text), reverse=True)
    top_n = candidates[: max(1, len(candidates) // 4)] or candidates[:1]
    x0 = min(b.x0_frac for b in top_n)
    x1 = max(b.x1_frac for b in top_n)
    return x0, x1


def strip_page_chrome(page: PageLayout) -> ColumnResult:
    removed: Set[int] = set()
    notes: List[str] = []

    blocks = page.blocks
    if not blocks:
        return ColumnResult(removed_block_ids=removed, notes=notes)

    main_range = _find_main_column_x_range(blocks)

    for block in blocks:
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
