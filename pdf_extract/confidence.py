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
from typing import List

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


@dataclass
class ConfidenceReport:
    flagged: bool = False
    reasons: List[str] = field(default_factory=list)

    def add(self, reason: str) -> None:
        self.flagged = True
        self.reasons.append(reason)


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

    chars_per_page = final_char_count / page_count if page_count else 0
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

    contributing_fraction = contributing_pages / page_count if page_count else 0
    if contributing_fraction < MIN_CONTRIBUTING_PAGE_FRACTION:
        report.add(
            f"only {contributing_pages}/{page_count} pages contributed any "
            f"surviving text"
        )

    if override_applied and override_mode == "heading_range":
        # Not inherently a problem, but worth a quick human glance to
        # confirm the heading was actually found in this particular file.
        report.add("heading-range override applied -- verify start/end headings matched")

    if final_char_count == 0:
        report.add("zero characters extracted")

    return report
