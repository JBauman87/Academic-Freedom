"""
confidence.py

Decides whether a document's extraction result should be flagged for
manual review, and why. The goal is not to silently produce a "best
effort" text file for documents the pipeline is unsure about -- it's to
surface exactly which documents need a human, and a short reason, so the
user can triage a batch of several hundred documents efficiently instead
of spot-checking everything.

A document can be flagged for more than one reason at once; all applicable
reasons are recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# --- Tunable thresholds -----------------------------------------------
# If the extracted body text has fewer characters per page than this, the
# document is probably mostly image/scanned/near-empty and worth a look.
MIN_CHARS_PER_PAGE = 200

# If more than this fraction of a document's *original* extracted
# characters were removed as boilerplate/chrome, flag it -- either the
# document is unusually short (so removal has an outsized effect) or the
# heuristics may have over-triggered.
MAX_REMOVED_FRACTION = 0.55

# If fewer than this fraction of pages contributed any surviving text,
# something is likely wrong (e.g. heading-range override matched nothing,
# or every page was OCR'd unsuccessfully).
MIN_CONTRIBUTING_PAGE_FRACTION = 0.5

# --- Decorative-layout detection ---------------------------------------
# Cover pages, title pages, and acknowledgements sections often mix large
# stylized headline text with normal body-sized text, or break a headline
# into many small fragments (one per glyph/word) to achieve a decorative
# effect. Neither pattern is reliably caught by the boilerplate/chrome
# heuristics (the text isn't repeated across pages, and it isn't short
# generic UI chrome) -- but both are detectable from block-level geometry,
# and both correlate strongly with the "torn words" garbling seen when
# such a page's blocks are reassembled into reading order. Pages matching
# either signal are flagged so a human can confirm the output is correct.
#
# NOTE: an earlier version of this module used a single "max/min font
# size ratio across the whole page" signal. That produced false positives
# on completely ordinary pages: a normal page with one short section
# heading (e.g. 26pt) next to a long paragraph and a footnote block
# (e.g. 8.5-11pt) has a font-size ratio around 3.0 purely because *one*
# heading is much larger than the body text -- exactly what a normal
# document looks like, not a decorative layout. The distinguishing signal
# for an actual decorative page turns out to be that *multiple* short
# blocks sit at a much larger font size than the page's dominant body
# text (a title broken into several oversized pieces), not just one.

# A block counts as "large" if its font size is at least this many times
# the page's dominant body-text font size (the font size of whichever
# surviving block has the most characters -- i.e. the actual paragraph
# text, which is what "font size relative to body text" should be judged
# against).
LARGE_FONT_MULTIPLIER = 1.8

# A "large" block is only suspicious if it's also short -- a single long
# large-font heading is normal; several short large-font fragments are
# the decorative-title signature.
LARGE_FONT_SHORT_BLOCK_MAX_CHARS = 15

# At least this many large-and-short blocks must be present to flag a
# page. A single oversized heading block (count=1) is completely normal
# and must not trigger this.
MIN_LARGE_SHORT_BLOCKS = 2

# A page with at least this many blocks...
FRAGMENTED_BLOCK_MIN_COUNT = 8
# ...where the average block is no longer than this many characters...
FRAGMENTED_BLOCK_MAX_AVG_CHARS = 20
# ...is considered fragmented into decorative pieces rather than normal
# paragraphs (which are typically well over 100 characters per block).
# This signal alone already catches title pages where the whole title AND
# byline/date/author block are all broken into many small pieces.
#
# A second, complementary signal catches web-page chrome that the AVERAGE
# check above can miss: a page made up mostly of short nav/list/footer
# items (e.g. a "Related Bulletins" teaser list, a footer link list) can
# include one or two longer headline/caption blocks that pull the average
# block length just above FRAGMENTED_BLOCK_MAX_AVG_CHARS even though the
# large majority of blocks are still short chrome fragments. Validated
# against a real 43-document corpus (including several dense historical
# reports with many short footnote-style blocks, which stay well under
# this fraction): pages of genuine chrome had >=69% of blocks at or below
# the short-block character threshold, while every genuine content page
# in the corpus stayed under 50%.
FRAGMENTED_BLOCK_SHORT_FRACTION = 0.65

# Safety cap for automatic exclusion (see pipeline.py): a page is only
# ever *auto-excluded* from output (as opposed to merely flagged) if its
# surviving text totals no more than this many characters. Genuine
# decorative pages (a title, a crest/seal rendered as scattered text
# fragments, an imprint block, an acknowledgements section with a second
# duplicated rendering of the title) are almost always well under this.
# A page with substantially more text that happens to also match one of
# the decorative signals (e.g. a numbered list of short findings) is
# flagged for a human to check, but its content is never silently dropped.
#
# Calibrated against real title/acknowledgements pages observed in
# practice: a two-page decorative front section (title page + an
# acknowledgements page that also re-renders the title as a second
# fragmented decorative element) had totals of ~190 and ~650 characters
# respectively. The cap is set with headroom above that observed range
# rather than exactly at it, since decorative pages vary in how much
# text they carry (e.g. a longer acknowledgements paragraph, additional
# imprint/copyright boilerplate).
MAX_CHARS_FOR_AUTO_EXCLUDE = 900

# --- Garbled/truncated text-layer detection -----------------------------
# Distinct from decorative-layout detection above: this catches a genuine
# PDF *source* defect rather than a stylistic page layout. Found via the
# feedback loop on a real document where several pages -- visually normal,
# clean single-column prose when rendered to an image -- had a PDF text
# layer that was only extracting the first few characters of each line
# (e.g. "You conclude your letter..." extracted as just "You"), with the
# extractor's own block/line clustering scattering those truncated
# fragments into narrow vertical-strip blocks. Word-level extraction
# (page.get_text("words")) on the same page recovered the full, correct
# text, confirming the defect is in the PDF's line/block-level text
# layout metadata specifically, not in the underlying character data.
#
# This must be flagged and routed to manual/OCR review, NOT treated as a
# "decorative page safe to auto-exclude" -- unlike a title page, this
# pattern can appear on pages carrying substantial real content (e.g. a
# multi-paragraph appendix letter), where auto-excluding would silently
# discard real substance rather than harmless cover-page filler.
#
# Signature: a block with several distinct lines where each line is only
# a handful of characters -- consistent with only the first few
# characters of each real line surviving extraction.
GARBLED_TEXT_MIN_LINES = 3
GARBLED_TEXT_MAX_AVG_LINE_CHARS = 6
# At least this many such blocks on one page is required to flag it --
# a single such block is much more likely to be an address block, a
# short list, or similar legitimate short-line content.
GARBLED_TEXT_MIN_BLOCK_COUNT = 2
# Real occurrences of this defect were observed only at normal body/
# footnote text sizes (8-11pt). A decorative title broken into similarly
# short multi-line fragments uses much larger, heading-sized type (e.g.
# 18-48pt observed on real title/appendix-heading pages) -- requiring a
# modest font size here prevents the two signatures from being confused
# with each other.
GARBLED_TEXT_MAX_FONT_SIZE = 13.0


# --- Character-level ("weird word") OCR/scan garbling detection --------
# A second, distinct PDF-source defect from the truncated-line signature
# above: found on a real CAUT report's scanned appendix pages (an email
# thread and an open letter, re-extracted via this project's OCR
# fallback), where individual characters mid-word are replaced with a
# handful of stray symbols -- "b~en" for "been", "Ntw\tSttldenrt_y" for
# "Nicole Studenny" -- rather than whole lines being truncated. Unlike the
# truncated-line defect, this corruption is scattered unpredictably
# across otherwise-normal-length lines/blocks, so it needs its own
# detector: a word containing a letter immediately followed by one of a
# small set of symbols that never appear glued to a real word in this
# corpus (~^`\|), then another letter, is almost certainly a real
# character misread rather than legitimate punctuation.
_WEIRD_GLUED_SYMBOL_RE = re.compile(r"[A-Za-z][~^`\\|][A-Za-z]")

# A page is only flagged if it has at least this many such words...
WEIRD_WORD_MIN_COUNT = 5
# ...spread across at least this many distinct blocks. Requiring more
# than one block rules out a single garbled word or two inside an
# otherwise-clean block (e.g. a single OCR misread in one line), which is
# far more likely to be an isolated glitch than the page-wide corruption
# this detector targets. Validated against the full corpus: every page
# matching both thresholds together showed genuine, substantial
# character-level corruption on manual inspection; no clean page in the
# corpus matched both.
WEIRD_WORD_MIN_BLOCKS = 2


def count_weird_glued_symbol_words(blocks) -> int:
    """
    Count words, across all of the given blocks, that match the
    glued-symbol character-corruption signature described above.
    """
    return sum(
        1
        for block in blocks
        for word in block.text.split()
        if _WEIRD_GLUED_SYMBOL_RE.search(word)
    )


def count_weird_glued_symbol_blocks(blocks) -> int:
    """Count how many of the given blocks contain at least one word
    matching the glued-symbol signature (see count_weird_glued_symbol_words)."""
    return sum(
        1
        for block in blocks
        if any(_WEIRD_GLUED_SYMBOL_RE.search(word) for word in block.text.split())
    )


def count_garbled_text_layer_blocks(blocks) -> int:
    """
    Count how many of the given blocks match the truncated-line signature
    described above. ``blocks`` are TextBlock-like objects exposing
    ``.text`` (newline-separated lines within a block, as produced by
    layout.py) and ``.avg_font_size``.
    """
    count = 0
    for block in blocks:
        text = block.text
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if len(lines) < GARBLED_TEXT_MIN_LINES:
            continue
        avg_len = sum(len(line) for line in lines) / len(lines)
        if avg_len > GARBLED_TEXT_MAX_AVG_LINE_CHARS:
            continue
        if block.avg_font_size and block.avg_font_size > GARBLED_TEXT_MAX_FONT_SIZE:
            continue
        count += 1
    return count


@dataclass
class ConfidenceReport:
    flagged: bool = False
    reasons: List[str] = field(default_factory=list)

    def add(self, reason: str) -> None:
        self.flagged = True
        self.reasons.append(reason)


@dataclass
class PageLayoutStats:
    """Per-page geometry summary used for decorative-layout detection.
    Built by the pipeline from the blocks that survived boilerplate/chrome
    removal (not the raw page), so a page's own running-header font size,
    if any, doesn't skew the numbers.

    ``large_short_block_count`` -- rather than a single whole-page
    max/min font-size ratio (which false-positives on an ordinary page
    with one heading), this counts blocks that are BOTH large relative
    to the page's dominant body-text font size AND short. Multiple such
    blocks is the actual signature of a fragmented decorative title; a
    single such block is just a normal section heading.
    """

    page_index: int
    block_count: int
    avg_chars_per_block: float
    total_chars: int
    font_size_ratio: float  # max avg_font_size / min avg_font_size across blocks (kept for diagnostics)
    large_short_block_count: int = 0
    short_block_fraction: float = 0.0  # fraction of blocks <= FRAGMENTED_BLOCK_MAX_AVG_CHARS chars
    garbled_text_layer_block_count: int = 0  # see count_garbled_text_layer_blocks
    weird_glued_symbol_word_count: int = 0  # see count_weird_glued_symbol_words
    weird_glued_symbol_block_count: int = 0  # see count_weird_glued_symbol_blocks


def is_decorative_layout_page(stats: "PageLayoutStats") -> bool:
    """True if this page's surviving blocks match one of the geometric
    signatures of a decorative layout (multiple oversized-and-short
    blocks, many very short fragments on average, or a large majority of
    short chrome-like fragments even if one or two longer headline/caption
    blocks are also present) -- see module docstring for detail and
    rationale."""
    if stats.large_short_block_count >= MIN_LARGE_SHORT_BLOCKS:
        return True
    if (
        stats.block_count >= FRAGMENTED_BLOCK_MIN_COUNT
        and stats.avg_chars_per_block <= FRAGMENTED_BLOCK_MAX_AVG_CHARS
    ):
        return True
    if (
        stats.block_count >= FRAGMENTED_BLOCK_MIN_COUNT
        and stats.short_block_fraction >= FRAGMENTED_BLOCK_SHORT_FRACTION
    ):
        return True
    return False


def is_garbled_text_layer_page(stats: "PageLayoutStats") -> bool:
    """True if this page shows the truncated-line PDF-text-layer defect
    signature (see module notes above). Distinct from
    ``is_decorative_layout_page`` -- this is never eligible for
    auto-exclusion, since the affected page may carry substantial real
    content that a naive decorative-page rule would otherwise discard."""
    return stats.garbled_text_layer_block_count >= GARBLED_TEXT_MIN_BLOCK_COUNT


def is_character_garbled_page(stats: "PageLayoutStats") -> bool:
    """True if this page shows the character-level "weird glued symbol"
    corruption signature (see module notes above count_weird_glued_symbol_words).
    Distinct from ``is_garbled_text_layer_page`` -- that detector catches
    whole lines truncated to a handful of characters each; this one
    catches individual characters replaced by stray symbols scattered
    through otherwise-normal-length lines. Like the truncated-line
    defect, this is never eligible for auto-exclusion, since the
    affected page can carry substantial real (if partially corrupted)
    content."""
    return (
        stats.weird_glued_symbol_word_count >= WEIRD_WORD_MIN_COUNT
        and stats.weird_glued_symbol_block_count >= WEIRD_WORD_MIN_BLOCKS
    )


def is_safe_to_auto_exclude(stats: "PageLayoutStats") -> bool:
    """True if a decorative-flagged page is also small enough in total
    surviving text that automatically excluding it from output is safe --
    i.e. there isn't enough real content at risk for a false positive to
    matter. See MAX_CHARS_FOR_AUTO_EXCLUDE.

    A page matching the garbled-text-layer signature -- either the
    truncated-line defect or the character-level "weird glued symbol"
    defect -- is NEVER eligible for auto-exclusion, regardless of size:
    both indicate a PDF source defect that can affect pages with
    substantial real content (e.g. a multi-paragraph appendix letter),
    unlike a genuine decorative title/cover page. Such pages must always
    be flagged for manual/OCR review instead of being silently dropped.
    """
    if is_garbled_text_layer_page(stats) or is_character_garbled_page(stats):
        return False
    return stats.total_chars <= MAX_CHARS_FOR_AUTO_EXCLUDE


# Retained for backwards compatibility with any external callers/tests
# written against the previous private name.
_is_decorative_layout_page = is_decorative_layout_page


def evaluate(
    *,
    page_count: int,
    original_char_count: int,
    final_char_count: int,
    removed_char_count: int,
    pages_needing_ocr: List[int],
    ocr_succeeded_pages: List[int],
    contributing_pages: int,
    override_applied: bool,
    override_mode: str = "",
    error: str = "",
    page_layout_stats: Optional[List["PageLayoutStats"]] = None,
    auto_excluded_page_count: int = 0,
) -> ConfidenceReport:
    report = ConfidenceReport()

    if error:
        report.add(f"error during processing: {error}")
        return report

    if page_count == 0:
        report.add("document had zero pages")
        return report

    if override_mode == "skip":
        report.add("marked as manual-only by override config")
        return report

    # Pages that were automatically excluded as decorative layout (see
    # below) are intentionally content-free in the main output -- that's
    # by design, not a defect. Excluding them from the "effective" page
    # count for the yield/coverage checks below avoids a cascade of
    # redundant "low yield" / "zero chars" flags that would just restate
    # the same fact the decorative-layout message already explains.
    effective_page_count = page_count - auto_excluded_page_count

    if effective_page_count <= 0 and auto_excluded_page_count > 0:
        report.add(
            f"entire document ({page_count} page(s)) was automatically "
            f"excluded as decorative layout -- no substantive content was "
            f"extracted; original text was saved to the .excluded.txt "
            f"sidecar file for review"
        )
        return report

    chars_per_page = final_char_count / effective_page_count if effective_page_count else 0
    if chars_per_page < MIN_CHARS_PER_PAGE:
        report.add(
            f"low text yield: {chars_per_page:.0f} chars/page "
            f"(threshold {MIN_CHARS_PER_PAGE})"
        )

    if original_char_count > 0:
        removed_fraction = removed_char_count / original_char_count
        if removed_fraction > MAX_REMOVED_FRACTION:
            report.add(
                f"high boilerplate removal: {removed_fraction:.0%} of extracted "
                f"text was stripped as repeated/chrome content"
            )

    if pages_needing_ocr:
        unresolved = [p for p in pages_needing_ocr if p not in ocr_succeeded_pages]
        if unresolved:
            report.add(
                f"{len(unresolved)} page(s) had no text layer and could not be "
                f"OCR'd (tesseract unavailable or OCR failed): pages "
                f"{[p + 1 for p in unresolved]}"
            )
        elif ocr_succeeded_pages:
            report.add(
                f"{len(ocr_succeeded_pages)} page(s) required OCR fallback "
                f"(pages {[p + 1 for p in ocr_succeeded_pages]}) -- please spot-check"
            )

    contributing_fraction = (
        contributing_pages / effective_page_count if effective_page_count else 0
    )
    if contributing_fraction < MIN_CONTRIBUTING_PAGE_FRACTION:
        report.add(
            f"only {contributing_pages}/{effective_page_count} non-excluded "
            f"page(s) contributed any surviving text"
        )

    if override_applied and override_mode == "heading_range":
        # Not inherently a problem, but worth a quick human glance to
        # confirm the heading was actually found in this particular file.
        report.add("heading-range override applied -- verify start/end headings matched")

    if final_char_count == 0:
        report.add("zero characters extracted")

    if page_layout_stats:
        garbled_pages = [
            s.page_index for s in page_layout_stats if is_garbled_text_layer_page(s)
        ]
        if garbled_pages:
            report.add(
                f"page(s) {[p + 1 for p in garbled_pages]} show signs of a "
                f"corrupted/truncated PDF text layer (many lines extracting "
                f"as only a handful of characters each) -- this is a defect "
                f"in the source PDF's text encoding, not a pipeline "
                f"ordering issue, and cannot be fixed by reordering text; "
                f"consider re-extracting these pages via OCR "
                f"(--keep-decorative-pages has no effect here) or reviewing "
                f"the original document manually"
            )

        char_garbled_pages = [
            s.page_index for s in page_layout_stats if is_character_garbled_page(s)
        ]
        if char_garbled_pages:
            report.add(
                f"page(s) {[p + 1 for p in char_garbled_pages]} show signs of "
                f"character-level corruption (individual letters replaced by "
                f"stray symbols scattered through otherwise-normal text, e.g. "
                f"a scanned/OCR'd page with recognition errors) -- consider "
                f"re-extracting these pages via a higher-quality OCR pass or "
                f"reviewing the original document manually"
            )

        decorative_pages = [
            s.page_index
            for s in page_layout_stats
            if is_decorative_layout_page(s)
            and not is_garbled_text_layer_page(s)
            and not is_character_garbled_page(s)
        ]
        if decorative_pages:
            excluded = [
                s.page_index
                for s in page_layout_stats
                if is_decorative_layout_page(s) and is_safe_to_auto_exclude(s)
            ]
            kept_but_flagged = [p for p in decorative_pages if p not in excluded]
            if excluded:
                report.add(
                    f"decorative/cover-page or navigation-chrome layout "
                    f"detected and automatically excluded from output on "
                    f"page(s) {[p + 1 for p in excluded]} (large font-size "
                    f"mix, or mostly short fragments, with low overall text "
                    f"volume) -- full original text for these pages was "
                    f"saved to the .excluded.txt sidecar file; please review"
                )
            if kept_but_flagged:
                report.add(
                    f"possible decorative layout on page(s) "
                    f"{[p + 1 for p in kept_but_flagged]} was NOT auto-excluded "
                    f"because it contains more text than the safety threshold "
                    f"allows -- reading order may still be unreliable here, "
                    f"please spot-check"
                )

    return report
