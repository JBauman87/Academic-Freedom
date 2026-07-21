"""
report.py

Writes a CSV summary of a batch run: one row per input document, recording
success/failure, output path, character counts, and -- most importantly --
whether the document was flagged for manual review and why. This is the
file the user should open first after a run of several hundred documents,
to triage which ones need a manual look before trusting the .txt outputs.
"""

from __future__ import annotations

import csv
from typing import List

from .pipeline import DocumentResult

REPORT_FIELDNAMES = [
    "source_file",
    "output_file",
    "excluded_output_file",
    "success",
    "page_count",
    "final_char_count",
    "flagged",
    "flag_reasons",
    "override_mode",
    "ocr_pages_used",
    "auto_excluded_pages",
    "urls_removed",
    "emails_removed",
    "separator_lines_removed",
    "ligatures_normalized",
    "icon_glyphs_removed",
    "html_tags_removed",
    "error",
]


def write_report(results: List[DocumentResult], report_path: str) -> None:
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDNAMES)
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "source_file": r.source_path,
                    "output_file": r.output_path or "",
                    "excluded_output_file": r.excluded_output_path or "",
                    "success": r.success,
                    "page_count": r.page_count,
                    "final_char_count": r.final_char_count,
                    "flagged": r.flagged,
                    "flag_reasons": " | ".join(r.flag_reasons),
                    "override_mode": r.override_mode,
                    "ocr_pages_used": ",".join(str(p + 1) for p in r.ocr_pages_used),
                    "auto_excluded_pages": ",".join(
                        str(p + 1) for p in r.auto_excluded_pages
                    ),
                    "urls_removed": r.urls_removed,
                    "emails_removed": r.emails_removed,
                    "separator_lines_removed": r.separator_lines_removed,
                    "ligatures_normalized": r.ligatures_normalized,
                    "icon_glyphs_removed": r.icon_glyphs_removed,
                    "html_tags_removed": r.html_tags_removed,
                    "error": r.error,
                }
            )


def print_summary(results: List[DocumentResult]) -> None:
    total = len(results)
    succeeded = sum(1 for r in results if r.success)
    failed = total - succeeded
    flagged = sum(1 for r in results if r.flagged)

    print("=" * 60)
    print(f"Processed {total} document(s)")
    print(f"  Succeeded:            {succeeded}")
    print(f"  Failed (errors):      {failed}")
    print(f"  Flagged for review:   {flagged}")
    print("=" * 60)

    if flagged:
        print("\nFlagged documents (see report.csv for full detail):")
        for r in results:
            if r.flagged:
                reasons = "; ".join(r.flag_reasons) if r.flag_reasons else "unspecified"
                print(f"  - {r.source_path}: {reasons}")
