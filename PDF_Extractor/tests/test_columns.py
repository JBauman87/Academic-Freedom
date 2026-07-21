"""
Tests for pdf_extract/columns.py -- same-page navigation/sidebar/card-grid
chrome stripping heuristics, focusing on the fixes made during the
testing_3 feedback round:

  1. Nav-row heuristic must not mistake a decorative title page's large,
     non-uniform font-size fragments for a real navigation bar.
  2. Merged-single-block nav bars/icon strips (an entire nav bar rendered
     as one multi-line PDF text block, whose individual "lines" don't
     match the generic chrome-phrase denylist) must still be caught via
     the packed-short-lines geometric signature.
  3. Card-grid detection must not sweep up a garbled/torn PDF text-layer
     defect's narrow-strip fragments (which can carry substantial real
     content) or a page dominated by narrow/decorative fragments more
     generally, even though those incidentally satisfy the same
     y-overlap/x-non-overlap geometry test as a genuine card grid.

These tests build TextBlock objects directly (rather than via a rendered
PDF fixture) since the properties under test are purely geometric/
structural and this keeps each case minimal and precise.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdf_extract import columns
from pdf_extract.layout import TextBlock

PAGE_W = 600.0
PAGE_H = 800.0


def _block(text, x0, y0, x1, y1, font_size=10.0, nlines=None):
    """Convenience constructor for a TextBlock with a uniform font size
    across all of its lines (the common case for real PDF text)."""
    if nlines is None:
        nlines = len([l for l in text.split("\n") if l.strip()]) or 1
    return TextBlock(
        page_index=0,
        page_width=PAGE_W,
        page_height=PAGE_H,
        bbox=(x0, y0, x1, y1),
        text=text,
        font_sizes=[font_size] * nlines,
    )


class TestNavRowFontUniformityGuard:
    def test_uniform_modest_font_nav_row_is_detected(self):
        # Five short items at the same modest font size, spanning a wide
        # portion of the page -- the structural signature of a real nav
        # bar (see NAV_ROW_* constants).
        items = ["Home", "News", "Sports", "Opinion", "Culture"]
        blocks = []
        x = 10.0
        for item in items:
            blocks.append(_block(item, x, 30, x + 60, 42, font_size=10.0))
            x += 110
        ids = columns._find_nav_row_block_ids(blocks)
        assert len(ids) == len(items)

    def test_decorative_title_fragments_not_mistaken_for_nav_row(self):
        # Several short, large-font fragments of a decorative title,
        # spanning a wide portion of the page at roughly the same height
        # -- structurally similar to a nav row by position/word-count
        # alone, but at a much larger and non-uniform font size than any
        # real nav bar uses. Must NOT be treated as a nav row.
        fragments = [
            ("FAKE", 10, 100, 34.4),
            ("REPORT", 130, 105, 46.3),
            ("ON", 280, 100, 28.0),
            ("A", 350, 102, 39.4),
            ("TOPIC", 420, 101, 33.4),
        ]
        blocks = [
            _block(text, x, 90, x + 80, 90 + size, font_size=size)
            for text, x, _y, size in fragments
        ]
        ids = columns._find_nav_row_block_ids(blocks)
        assert ids == set()


class TestPackedShortLinesHeuristic:
    def test_merged_nav_bar_block_is_detected(self):
        # An entire nav bar rendered as ONE multi-line PDF block (as seen
        # on real CTV/The Campus article exports) -- items don't match
        # the generic chrome-phrase denylist (outlet-specific section
        # names), but the block's lines are packed much closer together
        # than its font size would produce if genuinely stacked.
        block = _block(
            "Local\nWatch\nTrade War\nIn Pictures\nCTV Your Morning\nSign In",
            x0=200,
            y0=60,
            x1=650,
            y1=72,
            font_size=8.0,
            nlines=6,
        )
        assert columns._matches_packed_short_lines(block)

    def test_genuine_stacked_paragraph_is_not_flagged(self):
        # A normal multi-line paragraph: lines are genuinely stacked, so
        # height-per-line is close to the font size (ratio near 1.0).
        lines = [
            "Students at the university say they are shocked and",
            "appalled by the apparent lack of action taken so far",
            "in response to this ongoing situation on campus.",
        ]
        text = "\n".join(lines)
        block = _block(text, x0=60, y0=500, x1=430, y1=536, font_size=8.0, nlines=3)
        assert not columns._matches_packed_short_lines(block)

    def test_decorative_title_fragment_is_not_flagged(self):
        # A decorative title's stacked single-word fragments are also
        # genuinely stacked (ratio near 1.0), just at a large font size --
        # must not be caught by this heuristic either.
        text = "REPORT\nCAUT"
        block = _block(text, x0=60, y0=100, x1=200, y1=156, font_size=28.0, nlines=2)
        assert not columns._matches_packed_short_lines(block)

    def test_table_cell_with_digits_is_not_flagged(self):
        # A short table cell (e.g. a report appendix's "Date / Item # /
        # Event" row) can also be short and tightly packed, but contains
        # digits/table-structure characters that real nav/icon labels
        # never do -- must not be treated as chrome.
        text = "20-4-11\n8-24\nEvent/document"
        block = _block(text, x0=60, y0=200, x1=200, y1=215, font_size=10.0, nlines=3)
        assert not columns._matches_packed_short_lines(block)

    def test_long_line_disqualifies_block(self):
        # A block with one line far longer than a nav-item label (e.g. a
        # real sentence) must not be flagged even if a couple of its
        # other lines are short.
        text = "Related\nThis is a much longer line that reads like a real sentence"
        block = _block(text, x0=60, y0=300, x1=560, y1=320, font_size=10.0, nlines=2)
        assert not columns._matches_packed_short_lines(block)


class TestCardGridGarbledTextExclusion:
    def test_garbled_narrow_strip_blocks_are_excluded_from_card_grid(self):
        # Reproduces the Calgary CAUT report defect: several narrow
        # vertical-strip blocks, each a truncated-line PDF text-layer
        # defect (many short lines), positioned side by side. These must
        # never be swept up as a "card grid", since the page may carry
        # substantial real content that confidence.py's dedicated
        # garbled-text detector needs to see and flag for manual/OCR
        # review.
        blocks = []
        x = 50.0
        for i in range(4):
            text = "\n".join(["ab", "cd", "ef"])  # short lines -> garbled signature
            blocks.append(_block(text, x, 100, x + 40, 160, font_size=10.0, nlines=3))
            x += 60
        ids = columns._find_card_grid_block_ids(blocks)
        assert ids == set()

    def test_genuine_card_grid_is_still_detected(self):
        # A real "related articles" teaser row: narrow blocks side by
        # side, each with a longer multi-line teaser (headline + byline),
        # not matching the garbled-text signature. Also includes several
        # normal-width body-paragraph blocks elsewhere on the page, since
        # a real page with a card-grid teaser row is still a normal page
        # of mostly-normal-width content (see
        # CARD_GRID_MAX_PAGE_NARROW_FRACTION) -- a page made up ONLY of
        # narrow blocks is the torn-column/decorative-page signature this
        # heuristic must avoid, not a genuine card grid.
        blocks = []
        x = 50.0
        for i in range(4):
            text = (
                f"Category {i}\nA longer headline fragment number {i}\nByline name"
            )
            blocks.append(_block(text, x, 500, x + 90, 560, font_size=11.0, nlines=3))
            x += 110
        # Normal-width article body paragraphs elsewhere on the page.
        for i in range(6):
            blocks.append(
                _block(
                    f"This is a normal-width body paragraph number {i} with "
                    "plenty of real article text in it.",
                    60,
                    100 + i * 40,
                    520,
                    130 + i * 40,
                    font_size=11.0,
                    nlines=1,
                )
            )
        ids = columns._find_card_grid_block_ids(blocks)
        assert len(ids) == 4

    def test_page_dominated_by_narrow_fragments_is_skipped_entirely(self):
        # Reproduces the second Calgary false-positive mode: even a
        # *small* (within-cap-size) cluster of narrow blocks must not be
        # treated as a card grid if the page as a whole is dominated by
        # narrow blocks (e.g. a torn-column or heavily fragmented
        # decorative page) -- see CARD_GRID_MAX_PAGE_NARROW_FRACTION.
        blocks = []
        x = 50.0
        # Many narrow blocks covering almost the whole page (simulating a
        # torn-column defect/decorative page).
        for i in range(20):
            blocks.append(
                _block(f"w{i}", x, 100 + i * 5, x + 30, 100 + i * 5 + 12, font_size=10.0)
            )
            x = 50.0 + (i % 6) * 90
        ids = columns._find_card_grid_block_ids(blocks)
        assert ids == set()

    def test_cluster_larger_than_max_items_is_not_treated_as_card_grid(self):
        # A cluster with far more items than any real card grid observed
        # in practice (see CARD_GRID_MAX_ITEMS docstring) must be
        # rejected outright, regardless of its other properties.
        blocks = []
        x = 40.0
        for i in range(15):
            blocks.append(
                _block(f"Item {i}", x, 100, x + 30, 112, font_size=9.0)
            )
            x += 35
        ids = columns._find_card_grid_block_ids(blocks)
        assert ids == set()
