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

from . import confidence
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

# Real navigation bars use one (or very nearly one) font size across all
# their items -- e.g. "Home News Sports" are all rendered in the same nav
# stylesheet rule. A decorative title/cover page broken into many small
# fragments, by contrast, typically uses several sharply different font
# sizes across those fragments (parts of a large stylized headline mixed
# with smaller subtitle/byline pieces). Requiring near-uniform font size
# within a candidate row -- and a maximum absolute size, since real nav
# items are normal UI text size, not large decorative type -- prevents
# the nav-row heuristic from mistaking a decorative page's fragments for
# a real navigation bar. This was found necessary after observing the
# nav-row heuristic consume most of a real title page's fragments (font
# sizes ranging ~27-46pt) before the decorative-layout detector
# (confidence.py) got a chance to evaluate the page, hiding it from that
# detector entirely.
NAV_ROW_MAX_FONT_SIZE_RATIO = 1.3
NAV_ROW_MAX_FONT_SIZE = 16.0

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
    "related bulletins",
    "view all",
    "you might also like",
    "recommended for you",
    "recommended videos",
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
    "story continues below",
    "keep watching",
    "up next",
    "my account",
}

# Durations like "2:57" or "30:13" (video length badges) and pure page
# furniture like lone numbers ("1", "2", "3" list rankings) are also generic
# giveaways of a "trending/popular" list rather than article prose.
_DURATION_RE = re.compile(r"^\d{1,2}:\d{2}$")
_LONE_RANK_RE = re.compile(r"^\d{1,2}$")

# "435 Comments" / "13 comments" -- a bare comment-count widget, distinct
# from real prose that discusses "comments" as a word (which is why this
# requires a leading number rather than joining "comments" itself into the
# phrase denylist above).
_COMMENT_COUNT_RE = re.compile(r"^[\d,]+\s+comments?$", re.IGNORECASE)

# "Popular Now in News", "Trending Videos", "Recommended For You in Sports"
# etc: a "popular/trending/recommended" widget label followed by an
# optional "in <category>" suffix that varies per publisher/section, so
# can't be enumerated as exact phrases in the denylist above.
_POPULAR_WIDGET_RE = re.compile(
    r"^(popular now|trending now|trending|most popular|most read|"
    r"recommended for you|recommended)(\s+(in|on)\s+\w[\w\s&]*)?$",
    re.IGNORECASE,
)

# "Sponsored by Destination Osoyoos" / "Promoted by Our Newsroom" -- native
# advertising / cross-promo labels where the advertiser/section name varies,
# so matched as a prefix rather than an exact phrase.
_SPONSORED_BY_RE = re.compile(r"^(sponsored|promoted)\s+by\b", re.IGNORECASE)

# "Subscribe $0.50/week" / "Subscribe $20 for 1 year" / "Subscribe $6 for 6
# months" -- a paywall CTA button whose price/term varies per publisher.
_SUBSCRIBE_PRICE_RE = re.compile(r"^subscribe\s*\$", re.IGNORECASE)

# "Sign up now >>" -- a newsletter-signup CTA link; the ">>" arrow and
# "now" wording is common across several outlets' PDF exports.
_SIGN_UP_NOW_RE = re.compile(r"^sign up now\b", re.IGNORECASE)


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
    if _COMMENT_COUNT_RE.match(norm):
        return True
    if _POPULAR_WIDGET_RE.match(norm):
        return True
    if _SPONSORED_BY_RE.match(norm):
        return True
    if _SUBSCRIBE_PRICE_RE.match(norm):
        return True
    if _SIGN_UP_NOW_RE.match(norm):
        return True
    # "Estimated 2 minutes" / "Estimated N minute(s)" read-time widgets
    if norm.startswith("estimated") and ("minute" in norm or "min" in norm):
        return True
    return False


# Fraction of a multi-line block's own lines that must independently match
# a generic chrome phrase (see _matches_generic_chrome) for the whole
# block to be treated as chrome. This handles nav bars/footers that a PDF
# renderer merged into a single multi-line block (e.g. "Local\nWatch\n...
# \nSign In" as one block) rather than one block per item -- the
# whole-block phrase match above only catches a block whose ENTIRE
# (normalized) text equals a denylist phrase, which such a merged block
# never does, since it also contains non-denylist section names.
MERGED_CHROME_LINE_MIN_LINES = 3
MERGED_CHROME_LINE_MATCH_FRACTION = 0.4

# A 2-line block (below MERGED_CHROME_LINE_MIN_LINES) is still safe to
# treat as chrome if BOTH of its lines independently match the generic
# phrase denylist -- e.g. a "Related Bulletins" widget label merged with
# its own "View All" link into one 2-line block. Requiring *all* lines to
# match (rather than the more lenient fraction used for 3+ line blocks)
# keeps this safe: a real 2-line sentence/heading could coincidentally
# have one line match a denylist word, but having both short lines each
# independently equal a denylist phrase is essentially only possible for
# genuine merged UI chrome.
MERGED_CHROME_TWO_LINE_MIN_LINES = 2


def _matches_merged_chrome_block(text: str) -> bool:
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < MERGED_CHROME_TWO_LINE_MIN_LINES:
        return False
    matches = sum(1 for l in lines if _matches_generic_chrome(l))
    if len(lines) < MERGED_CHROME_LINE_MIN_LINES:
        return matches == len(lines)
    return (matches / len(lines)) >= MERGED_CHROME_LINE_MATCH_FRACTION


# "Packed short lines" geometric heuristic -- a further fallback for
# merged nav bars/social-icon rows whose individual items don't even
# match the generic phrase denylist (e.g. an outlet's own section names
# like "Local"/"Watch"/"Trade War", which can't be enumerated generically
# without overfitting to specific publishers).
#
# The key structural signal is *vertical packing*: when a PDF renderer
# lays out several short items side-by-side on what is visually one
# horizontal row (a nav bar, an icon strip), PyMuPDF's block/line
# clustering still reports each item as a separate "line" within the
# block, one after another -- but because they're side-by-side rather
# than actually stacked, the block's total height is far smaller than
# what that many lines at that font size would occupy if genuinely
# stacked. A real multi-line paragraph (or a decorative title's stacked
# fragments) has a height-per-line roughly equal to its font size
# (ratio close to 1.0); a merged nav/icon row has a much smaller ratio,
# since the "lines" are actually side by side, not stacked on top of
# each other.
#
# This alone is not sufficient, though -- some genuinely packed *content*
# also has small height-per-line ratios, notably table rows/columns in
# reports (e.g. the Calgary CAUT report's "Date / Item # / Event / ..."
# appendix tables, where cells are individually short but the table as a
# whole conveys real substantive information). Those are excluded by
# additionally requiring every line be very short (<= 4 words -- shorter
# than even a table cell's citation/quote fragment) and requiring no
# line contain a digit or a "/" or "#" character, since dates, item
# numbers, and table markers are exactly what distinguishes genuine
# tabular report content from nav-bar/icon-strip labels (which are
# always plain words, e.g. "Home", "News", "Sign In").
#
# Verified against the full real-document test corpus (21 PDFs spanning
# news articles, CAUT/CBC reports, letters, and legal-adjacent
# documents): this combination fires only on genuine nav bars/social
# icon strips (CTV's "Local/Watch/Trade War/.../Sign In", The Campus's
# "Home/News/Opinions/.../Arts & Culture" and "Business & Economics/About
# Us", CBC's "Menu/Search/Sign In", CAUT's "Join/Français" icon row,
# "Related Bulletins/View All", and footer link row) plus one
# icon-glyph-fragment junk block, and never fires on real paragraphs,
# decorative title-page fragments, letter/address or signature blocks,
# wrapped two-line headlines, or the Calgary report's tables/garbled-text
# fragments -- every genuine 2-line block in the corpus has a
# height-per-line ratio >= 0.95, comfortably above the 0.7 threshold, so
# requiring only 2 lines (rather than 3) is safe and additionally catches
# short 2-item merged chrome rows that 3+ would miss.
PACKED_LINES_MIN_LINES = 2
PACKED_LINES_MAX_HEIGHT_RATIO = 0.7
PACKED_LINES_MAX_WORDS_PER_LINE = 4
PACKED_LINES_MAX_TOTAL_WORDS = 30
_PACKED_LINE_DISQUALIFY_RE = re.compile(r"[0-9/#]")


def _matches_packed_short_lines(block: TextBlock) -> bool:
    lines = [l.strip() for l in block.text.split("\n") if l.strip()]
    if len(lines) < PACKED_LINES_MIN_LINES:
        return False
    total_words = sum(len(l.split()) for l in lines)
    if total_words > PACKED_LINES_MAX_TOTAL_WORDS:
        return False
    for line in lines:
        if len(line.split()) > PACKED_LINES_MAX_WORDS_PER_LINE:
            return False
        if _PACKED_LINE_DISQUALIFY_RE.search(line):
            return False
    font = block.avg_font_size
    if not font:
        return False
    ratio = block.height / (len(lines) * font)
    return ratio < PACKED_LINES_MAX_HEIGHT_RATIO


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
        if x1 - x0 < NAV_ROW_MIN_SPAN_FRAC:
            continue

        # Font-size uniformity/size check (see NAV_ROW_MAX_FONT_SIZE_RATIO
        # docstring above) -- skip groups that look like decorative
        # fragments rather than a real nav bar.
        sizes = [b.avg_font_size for b in group if b.avg_font_size > 0]
        if sizes:
            size_ratio = max(sizes) / min(sizes)
            if size_ratio > NAV_ROW_MAX_FONT_SIZE_RATIO or max(sizes) > NAV_ROW_MAX_FONT_SIZE:
                continue

        removed.update(id(b) for b in group)
    return removed


# "Card grid" detection: a "related articles" section rendered as several
# side-by-side teaser cards (as opposed to a single-column vertical list,
# which the side-rail heuristic already covers) shows up as multiple
# blocks that occupy roughly the same vertical band (y-extent) but are
# narrow and non-overlapping horizontally -- e.g. four article teasers
# arranged in a row of columns near the bottom of a page. This is
# distinct from a nav row (which is wide, low blocks near the top/below a
# masthead): a card grid is typically several *narrow* blocks side by
# side, each internally containing a short multi-line teaser (category
# label, headline, byline).
CARD_GRID_MIN_ITEMS = 3
CARD_GRID_MAX_WIDTH_FRAC = 0.3
CARD_GRID_Y_OVERLAP_MIN_FRAC = 0.5

# An upper bound on how many narrow blocks a single "card grid" cluster
# may contain. This was found necessary after discovering a serious false
# positive on a real report (the Calgary CAUT report) whose appendix
# letters are laid out with a torn/scattered multi-column text-extraction
# defect: dozens to over a hundred narrow blocks per page, each a small
# fragment of a column of running prose, satisfying the same
# y-overlap/x-non-overlap geometry test as a genuine card grid purely by
# coincidence of how many narrow columns happen to line up. A real
# "related articles" teaser row, by contrast, has a small, fixed number
# of items (3-7 observed across the real corpus, since it mirrors a
# small number of teaser cards a web page actually displays side by
# side) -- there is no real-world nav pattern that legitimately produces
# dozens of side-by-side narrow cards on one page. Capped well above the
# observed real maximum (7 items, The Campus's "recent posts" row) while
# still rejecting the pathological 10-to-100+-item clusters seen on the
# Calgary report (whose smallest false-positive cluster was already 10
# items).
CARD_GRID_MAX_ITEMS = 8

# A second, independent safeguard against the same false-positive family:
# even after capping cluster size, a handful of the Calgary report's
# torn-column fragments still happened to land in small (4-8 item)
# clusters that satisfy the geometry test on their own (e.g. an
# "Appendix D"-style oversized decorative heading broken into vertical
# letter-strips, and torn two/three-column body-text fragments -- both
# found via corpus re-scan after the cluster-size cap above). What
# reliably distinguishes a genuine card-grid page from a torn/decorative
# page is not the candidate cluster itself but the *page as a whole*: a
# real page with a card-grid teaser row is still a normal page of
# mostly-normal-width content, with the teaser row as one relatively
# small feature (narrow blocks were <= 77% of that page's blocks in
# every genuine example found in the real corpus). A page suffering the
# column-tearing defect or covered in decorative fragments, by contrast,
# is overwhelmingly made of narrow blocks (92-100% in every Calgary
# false-positive page found). Requiring the *page's* narrow-block
# fraction to stay below this threshold before card-grid detection is
# even attempted is a cheap, robust page-level gate that catches these
# cases regardless of the specific cluster size/width/font-size quirks
# that happened to let them slip through the other, more targeted guards
# above.
CARD_GRID_MAX_PAGE_NARROW_FRACTION = 0.8


# When grouping a column's stacked narrow blocks into a single "card"
# (see _group_into_cards below), a gap between consecutive blocks no
# wider than this (as a fraction of page height) is treated as still
# part of the same card -- e.g. the small gaps between a teaser's
# category label, wrapped headline lines, and trailing date stamp.
CARD_STACK_MAX_GAP_FRAC = 0.05

# A grouped "card" is only eligible for card-grid detection if it stays
# within these limits on total text and vertical extent. This was found
# necessary after the whole-card grouping fix above (needed to correctly
# remove a genuine multi-row teaser grid in its entirety -- see
# _group_into_cards docstring) also caused a serious false-positive
# regression: a real page laid out in narrow newspaper/journal-style
# columns (each column full of genuine running body-text paragraphs, not
# short teasers) satisfies the exact same narrow-column,
# vertically-overlapping, non-overlapping-horizontally geometry as an
# actual card grid once each column's blocks are grouped into one
# tall "card" -- e.g. a 4-column newspaper page or a 3-column journal
# article layout, both found via corpus re-scan, produced whole-page
# card-grid false positives that deleted genuine multi-paragraph
# articles entirely. A real teaser card (category label + headline +
# byline/date) is short by nature -- every genuine example found in the
# real corpus stayed under 100 characters and under 25% of the page's
# height -- while a real column of body-text paragraphs, once merged
# into one card by the stacking logic, is both far longer and taller.
# Capped with headroom above the observed genuine maximum while staying
# well below every false-positive example found (which started at 280
# characters / 10% of page height and ran far higher).
CARD_MAX_CHARS = 200
CARD_MAX_HEIGHT_FRAC = 0.25


def _group_into_cards(column_blocks: List[TextBlock]) -> List[List[TextBlock]]:
    """
    Within a single narrow column (blocks already sharing a left edge),
    group vertically-stacked blocks with only a small gap between them
    into one "card" -- e.g. a teaser's category label, its headline
    (possibly itself wrapped across more than one block), and a trailing
    date stamp, which are visually one teaser item, not several. A large
    vertical gap starts a new card in the same column.
    """
    ordered = sorted(column_blocks, key=lambda b: b.y0_frac)
    cards: List[List[TextBlock]] = []
    for block in ordered:
        if cards and block.y0_frac - cards[-1][-1].y1_frac <= CARD_STACK_MAX_GAP_FRAC:
            cards[-1].append(block)
        else:
            cards.append([block])
    return cards


def _find_card_grid_block_ids(blocks: List[TextBlock]) -> Set[int]:
    """
    Detect a horizontal row of narrow "card" blocks (e.g. several
    "related articles" teasers side by side) that substantially overlap
    each other's vertical extent but occupy distinct, non-overlapping
    horizontal ranges. Returns the ids of blocks belonging to such a
    grid, or an empty set if none is found.

    Blocks matching the garbled-truncated-text-layer signature (see
    confidence.count_garbled_text_layer_blocks) are excluded from
    eligibility here. That defect -- narrow vertical-strip blocks each
    made up of only the first few characters of otherwise-real lines --
    was found to visually resemble a column of narrow "cards" closely
    enough to be swept up by this heuristic and silently removed,
    hiding a genuine PDF text-layer defect from confidence.py's
    dedicated (and much more important) garbled-text detector, which
    routes such pages to manual/OCR review rather than letting them be
    treated as harmless decorative chrome. This must never happen, since
    a garbled page can carry substantial real content.

    Detection works in two stages:

    1. Narrow blocks are first clustered by left edge into columns (the
       same left-margin-clustering approach used for the main-column
       estimate), then each column's blocks are grouped into "cards" --
       contiguous vertical runs with only a small gap between them (see
       _group_into_cards). This matters because a real card grid's
       individual teasers often wrap to different numbers of lines (one
       teaser's headline is one line, another's wraps to three), so
       checking row-by-row alignment across columns directly -- as an
       earlier version of this function did -- only catches whichever
       row happens to still line up across all columns (typically just
       the first), silently leaving the rest of each card's lines
       behind as ordinary "kept" text and, worse, pulling a real content
       page's short-block statistics out of proportion in a way that can
       trigger the unrelated decorative-page auto-exclusion check in
       confidence.py on the page's genuine body paragraphs. Grouping into
       whole cards first ensures a matched grid removes each card in its
       entirety.
    2. The resulting per-column cards (each with its own merged bounding
       box) are then checked for the actual grid geometry: enough of them
       (CARD_GRID_MIN_ITEMS to CARD_GRID_MAX_ITEMS) sitting in the same
       vertical band as each other, without overlapping horizontally.
    """
    narrow = [
        b
        for b in blocks
        if (b.x1_frac - b.x0_frac) <= CARD_GRID_MAX_WIDTH_FRAC
        and confidence.count_garbled_text_layer_blocks([b]) == 0
    ]
    if len(narrow) < CARD_GRID_MIN_ITEMS:
        return set()

    # Page-level gate (see CARD_GRID_MAX_PAGE_NARROW_FRACTION docstring
    # above): skip card-grid detection entirely on pages where narrow
    # blocks dominate, since that pattern belongs to a torn-text-layer
    # defect or a decorative/fragmented layout, not a real card grid.
    if blocks and len(narrow) / len(blocks) > CARD_GRID_MAX_PAGE_NARROW_FRACTION:
        return set()

    # Stage 1: cluster into columns by left edge, then group each
    # column's blocks into whole cards.
    column_clusters: List[List[TextBlock]] = []
    seen_ids: Set[int] = set()
    for candidate in sorted(narrow, key=lambda b: b.x0_frac):
        if id(candidate) in seen_ids:
            continue
        column = [
            b for b in narrow if abs(b.x0_frac - candidate.x0_frac) <= LEFT_EDGE_CLUSTER_TOLERANCE
        ]
        seen_ids.update(id(b) for b in column)
        column_clusters.append(column)

    cards: List[List[TextBlock]] = []
    for column in column_clusters:
        cards.extend(_group_into_cards(column))

    # A card that is too long or too tall to plausibly be a short teaser
    # item (see CARD_MAX_CHARS/CARD_MAX_HEIGHT_FRAC docstring above) is
    # not eligible -- this is what actually distinguishes a genuine
    # multi-column card grid from a page laid out in narrow
    # newspaper/journal-style body-text columns, which would otherwise
    # satisfy the same union-find geometry test once grouped into cards.
    cards = [
        c
        for c in cards
        if sum(len(b.text) for b in c) <= CARD_MAX_CHARS
        and (max(b.y1_frac for b in c) - min(b.y0_frac for b in c)) <= CARD_MAX_HEIGHT_FRAC
    ]
    if len(cards) < CARD_GRID_MIN_ITEMS:
        return set()

    # Stage 2: union-find over whole cards (using each card's merged
    # bounding box) rather than individual blocks.
    n = len(cards)
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

    def card_bbox(card: List[TextBlock]):
        return (
            min(b.x0_frac for b in card),
            min(b.y0_frac for b in card),
            max(b.x1_frac for b in card),
            max(b.y1_frac for b in card),
        )

    bboxes = [card_bbox(c) for c in cards]

    for i in range(n):
        for j in range(i + 1, n):
            ax0, ay0, ax1, ay1 = bboxes[i]
            bx0, by0, bx1, by1 = bboxes[j]
            overlap_start = max(ay0, by0)
            overlap_end = min(ay1, by1)
            overlap = max(0.0, overlap_end - overlap_start)
            shorter_height = min(ay1 - ay0, by1 - by0)
            if shorter_height > 0 and overlap / shorter_height >= CARD_GRID_Y_OVERLAP_MIN_FRAC:
                # Also require they don't horizontally overlap much --
                # side-by-side cards, not a stacked column of items at
                # the same x position (which is a normal single-column
                # sidebar list, already handled by the side-rail
                # heuristic elsewhere).
                x_overlap_start = max(ax0, bx0)
                x_overlap_end = min(ax1, bx1)
                x_overlap = max(0.0, x_overlap_end - x_overlap_start)
                shorter_width = min(ax1 - ax0, bx1 - bx0)
                if shorter_width == 0 or x_overlap / shorter_width < 0.5:
                    union(i, j)

    clusters: dict = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    removed: Set[int] = set()
    for card_indices in clusters.values():
        if CARD_GRID_MIN_ITEMS <= len(card_indices) <= CARD_GRID_MAX_ITEMS:
            for idx in card_indices:
                removed.update(id(b) for b in cards[idx])
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
        # Cluster the long blocks by left edge and use only the cluster
        # with the most total characters, rather than spanning min/max
        # across ALL long blocks regardless of which column they're in.
        # A single long block sitting alone in a right-hand sidebar/ad
        # column (observed on a real CBC News page: a 105-char "related
        # articles" teaser headline, long enough on its own to pass the
        # LONG_BLOCK_MIN_CHARS bar, sitting well to the right of ten
        # genuine body paragraphs at the page's actual left margin) must
        # not be allowed to pull the estimated main-column range out to
        # cover that teaser's position -- doing so hides it (and its
        # sibling teaser items) from the side-rail heuristic below,
        # since they'd then appear to already be "inside" the main
        # column. Genuine body text reliably accounts for the large
        # majority of a page's real paragraph-length prose, so the
        # highest-total-character cluster is a robust way to prefer it
        # over an isolated long outlier in a different column.
        clusters_of_long: List[List[TextBlock]] = []
        seen_long_ids: Set[int] = set()
        for candidate in sorted(long_blocks, key=lambda b: b.x0_frac):
            if id(candidate) in seen_long_ids:
                continue
            cluster = [
                b
                for b in long_blocks
                if abs(b.x0_frac - candidate.x0_frac) <= LEFT_EDGE_CLUSTER_TOLERANCE
            ]
            seen_long_ids.update(id(b) for b in cluster)
            clusters_of_long.append(cluster)
        best_long_cluster = max(
            clusters_of_long, key=lambda c: sum(len(b.text) for b in c)
        )
        x0 = min(b.x0_frac for b in best_long_cluster)
        x1 = max(b.x1_frac for b in best_long_cluster)
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

    nav_row_ids = _find_nav_row_block_ids(blocks)
    for block in blocks:
        if id(block) in nav_row_ids:
            removed.add(id(block))
            notes.append(f"nav-row: {block.text[:30]!r}")

    card_grid_ids = _find_card_grid_block_ids(blocks)
    for block in blocks:
        if id(block) in card_grid_ids and id(block) not in removed:
            removed.add(id(block))
            notes.append(f"card-grid: {block.text[:30]!r}")

    # Heuristics 1/1b/1c below are all "column-independent": they classify
    # a block as chrome from its own text/geometry alone, without needing
    # to know where the main body column is. Running them BEFORE
    # estimating the main column (heuristic 2) matters: on a page whose
    # only long/wide blocks happen to be nav/footer/ad chrome rather than
    # real article body text (e.g. a masthead nav bar merged into one wide
    # block, or an "Introducing our newsletter" promo block wider than the
    # real body paragraphs below it), leaving that chrome in the pool the
    # column estimator samples from can pull the estimated column far
    # wider than the real body text -- silently disabling side-rail
    # detection for genuine sidebar/ad content on that same page. Cheaply
    # stripping the unambiguous, self-evident chrome first keeps the
    # column estimate honest.
    prefiltered: List[TextBlock] = []
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

        # Heuristic 1b: a multi-line block where most individual lines
        # independently match a generic chrome phrase -- catches a nav
        # bar/footer merged into a single block by the PDF's own layout
        # (see _matches_merged_chrome_block docstring above). Only
        # eligible when the block as a whole is still short in word
        # count, so a real multi-sentence paragraph can never match.
        if word_count <= TOP_STRIP_MAX_WORDS * 2 and _matches_merged_chrome_block(
            block.text
        ):
            removed.add(id(block))
            notes.append(f"merged-chrome-block: {block.text[:40]!r}")
            continue

        # Heuristic 1c: packed-short-lines geometry (see
        # _matches_packed_short_lines docstring above) -- catches merged
        # nav bars/icon strips whose items don't match the generic
        # phrase denylist at all (outlet-specific section names).
        if _matches_packed_short_lines(block):
            removed.add(id(block))
            notes.append(f"packed-short-lines: {block.text[:40]!r}")
            continue

        # Heuristic 3 (masthead/nav strip): a short block hugging the very
        # top of the page. Catches multi-item nav bars (e.g. "Menu" + site
        # name + "Sign In") that a PDF renderer merged into one block
        # because they share a baseline, which Heuristic 1's whole-block
        # phrase match would otherwise miss. Applied here, ahead of the
        # column estimate, for the same reason as 1/1b/1c above -- a wide
        # top-of-page masthead block must not be allowed to widen the
        # column estimate before it's removed.
        #
        # Restricted to the first page of the document: a masthead/nav bar
        # is a top-of-*document* element that a printed multi-page web
        # article only carries once, on its first page. On page 2+, real
        # body text simply continues from the top margin (there is no
        # masthead there to repeat), so this same y1_frac<=TOP_STRIP_MAX_Y_FRAC
        # geometry test would otherwise misfire on an ordinary paragraph
        # that happens to start at the top of a later page -- a real
        # observed failure (e.g. a National Post article's page 4 opening
        # sentence, "saying she couldn't effectively continue in...",
        # being deleted outright). Cross-page repeated chrome (a
        # timestamp/breadcrumb repeated on every page) is instead caught
        # by dedupe.py, which correctly requires actual repetition across
        # pages rather than mere position on a single page.
        if (
            page.page_index == 0
            and word_count <= TOP_STRIP_MAX_WORDS
            and block.y1_frac <= TOP_STRIP_MAX_Y_FRAC
        ):
            removed.add(id(block))
            notes.append(f"top-strip (y1_frac={block.y1_frac:.2f}): {block.text!r}")
            continue

        prefiltered.append(block)

    main_range = _find_main_column_x_range(prefiltered)

    for block in prefiltered:
        word_count = len(block.text.split())

        # Heuristic 2: geometry -- a block sitting well outside (to the
        # right of, or narrower/offset from) the estimated main column.
        # Rather than a fixed absolute x0_frac cutoff (which fails on a
        # page whose main column itself is narrow, e.g. a right-hand news
        # article column with its own further-right sidebar), a block
        # qualifies if it starts clearly past where the main column ends
        # OR is noticeably narrower than the main column while sitting
        # outside it -- both signals a secondary column/rail rather than
        # a continuation of body text.
        if main_range and word_count <= SIDE_RAIL_MAX_WORDS:
            main_x0, main_x1 = main_range
            starts_right_of_body = block.x0_frac >= main_x1 - 0.03 and block.x0_frac > 0.55
            if starts_right_of_body:
                removed.add(id(block))
                notes.append(f"side-rail (x0_frac={block.x0_frac:.2f}): {block.text!r}")
                continue

    return ColumnResult(removed_block_ids=removed, notes=notes)
