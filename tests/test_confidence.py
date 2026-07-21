"""
Tests for pdf_extract/confidence.py -- in particular the garbled/truncated
PDF text-layer defect detector added during the testing_3 feedback round
(see module docstring in confidence.py for the real-world bug this
detects: a PDF whose block/line clustering only recovers the first few
characters of each real line, scattering them into narrow vertical-strip
blocks that can still carry substantial real content and must therefore
never be silently auto-excluded like a decorative page).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdf_extract import confidence
from pdf_extract.layout import TextBlock

PAGE_W = 600.0
PAGE_H = 800.0


def _block(text, font_size=10.0, nlines=None):
    if nlines is None:
        nlines = len([l for l in text.split("\n") if l.strip()]) or 1
    return TextBlock(
        page_index=0,
        page_width=PAGE_W,
        page_height=PAGE_H,
        bbox=(50, 100, 150, 160),
        text=text,
        font_sizes=[font_size] * nlines,
    )


class TestGarbledTextLayerDetection:
    def test_truncated_line_block_is_detected(self):
        # Each line is only a handful of characters -- the truncated-line
        # signature of the real defect (only the first few characters of
        # each real line survived extraction).
        block = _block("You\nAre\nMor\nWel\nCom", font_size=10.0, nlines=5)
        assert confidence.count_garbled_text_layer_blocks([block]) == 1

    def test_normal_paragraph_block_is_not_flagged(self):
        block = _block(
            "This is a completely normal paragraph with full sentences "
            "and plenty of real text in each line of it.",
            font_size=10.0,
            nlines=1,
        )
        assert confidence.count_garbled_text_layer_blocks([block]) == 0

    def test_large_font_decorative_fragment_is_not_flagged(self):
        # A decorative title's short stacked fragments look superficially
        # similar (short lines) but occur at a much larger font size than
        # the real defect (observed only at normal body/footnote sizes)
        # -- must not be confused with the text-layer defect.
        block = _block("REP\nORT\nCAU\nT", font_size=32.0, nlines=4)
        assert confidence.count_garbled_text_layer_blocks([block]) == 0

    def test_two_line_block_is_too_short_to_flag(self):
        # Requires at least GARBLED_TEXT_MIN_LINES lines -- a 2-line block
        # (e.g. a name + title signature block) is too small a sample to
        # distinguish from legitimate short content.
        block = _block("Ja\nDo", font_size=10.0, nlines=2)
        assert confidence.count_garbled_text_layer_blocks([block]) == 0

    def test_page_level_flag_requires_minimum_block_count(self):
        stats_one = confidence.PageLayoutStats(
            page_index=0,
            block_count=10,
            avg_chars_per_block=50.0,
            total_chars=500,
            font_size_ratio=1.0,
            large_short_block_count=0,
            short_block_fraction=0.1,
            garbled_text_layer_block_count=1,
        )
        assert not confidence.is_garbled_text_layer_page(stats_one)

        stats_two = confidence.PageLayoutStats(
            page_index=0,
            block_count=10,
            avg_chars_per_block=50.0,
            total_chars=500,
            font_size_ratio=1.0,
            large_short_block_count=0,
            short_block_fraction=0.1,
            garbled_text_layer_block_count=2,
        )
        assert confidence.is_garbled_text_layer_page(stats_two)

    def test_garbled_page_is_never_safe_to_auto_exclude(self):
        # This is the critical safety property: even if a garbled page
        # also happens to match the decorative-layout signature and is
        # small enough to be under the auto-exclude character cap, it
        # must NEVER be auto-excluded -- it may carry substantial real
        # content that a naive decorative-page rule would silently
        # discard. It must only ever be flagged for manual/OCR review.
        stats = confidence.PageLayoutStats(
            page_index=0,
            block_count=10,
            avg_chars_per_block=20.0,
            total_chars=200,  # well under MAX_CHARS_FOR_AUTO_EXCLUDE
            font_size_ratio=1.0,
            large_short_block_count=0,
            short_block_fraction=0.1,
            garbled_text_layer_block_count=3,
        )
        assert confidence.is_garbled_text_layer_page(stats)
        assert not confidence.is_safe_to_auto_exclude(stats)

    def test_non_garbled_small_page_is_still_safe_to_auto_exclude(self):
        # Sanity check that the garbled-page safety override doesn't
        # accidentally disable auto-exclusion for genuinely decorative
        # pages that were never garbled in the first place.
        stats = confidence.PageLayoutStats(
            page_index=0,
            block_count=10,
            avg_chars_per_block=20.0,
            total_chars=200,
            font_size_ratio=1.0,
            large_short_block_count=0,
            short_block_fraction=0.1,
            garbled_text_layer_block_count=0,
        )
        assert not confidence.is_garbled_text_layer_page(stats)
        assert confidence.is_safe_to_auto_exclude(stats)
