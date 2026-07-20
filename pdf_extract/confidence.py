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

# If the ratio between the largest and smallest average font size among a
# page's blocks exceeds this, the page mixes markedly different text
# sizes (e.g. a big title next to normal-sized body/byline text).
MAX_FONT_SIZE_RATIO = 2.2

# A page with at least this many blocks...
FRAGMENTED_BLOCK_MIN_COUNT = 8
# ...where the average block is no longer than this many characters...
FRAGMENTED_BLOCK_MAX_AVG_CHARS = 20
# ...is considered fragmented into decorative pieces rather than normal
# paragraphs (which are typically well over 100 characters per block).

# Safety cap for automatic exclusion (see pipeline.py): a page is only
# ever *auto-excluded* from output (as opposed to merely flagged) if its
# surviving text totals no more than this many characters. Genuine
# decorative pages (a title, a crest/seal rendered as scattered text
# fragments, an imprint block) are almost always well under this. A page
# with substantially more text that happens to also match one of the
# decorative signals (e.g. a numbered list of short findings) is flagged
# for a human to check, but its content is never silently dropped.
MAX_CHARS_FOR_AUTO_EXCLUDE = 600


@dataclass
class ConfidenceReport:
    flagged: bool = False
    reasons: List[str] = field(default_factory=list)

    def add(self, reason: str) -> None:
        self.flagged = True
        self.reasons.append(reason)


@dataclass
class PageLayoutStats:
    """Minimal per-page geometry summary used for decorative-layout
    detection. Built by the pipeline from the blocks that survived
    boilerplate/chrome removal (not the raw page), so a page's own
    running-header font size, if any, doesn't skew the ratio."""

    page_index: int
    block_count: int
    avg_chars_per_block: float
    total_chars: int
    font_size_ratio: float  # max avg_font_size / min avg_font_size across blocks


def is_decorative_layout_page(stats: "PageLayoutStats") -> bool:
    """True if this page's surviving blocks match one of the geometric
    signatures of a decorative layout (large font-size mix, or many very
    short fragments) -- see module docstring for detail and rationale."""
    if stats.font_size_ratio >= MAX_FONT_SIZE_RATIO:
        return True
    if (
        stats.block_count >= FRAGMENTED_BLOCK_MIN_COUNT
        and stats.avg_chars_per_block <= FRAGMENTED_BLOCK_MAX_AVG_CHARS
    ):
        return True
    return False


def is_safe_to_auto_exclude(stats: "PageLayoutStats") -> bool:
    """True if a decorative-flagged page is also small enough in total
    surviving text that automatically excluding it from output is safe --
    i.e. there isn't enough real content at risk for a false positive to
    matter. See MAX_CHARS_FOR_AUTO_EXCLUDE."""
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
        decorative_pages = [
            s.page_index for s in page_layout_stats if is_decorative_layout_page(s)
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
                    f"decorative/cover-page layout detected and automatically "
                    f"excluded from output on page(s) {[p + 1 for p in excluded]} "
                    f"(large font-size mix or many short fragments, low text "
                    f"volume) -- full original text for these pages was saved "
                    f"to the .excluded.txt sidecar file; please review"
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
