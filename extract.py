#!/usr/bin/env python3
"""
extract.py -- CLI entry point for the PDF text-extraction pipeline.

Usage:
    python extract.py --input ./input_pdfs --output ./output_text

    python extract.py --input ./input_pdfs --output ./output_text \\
        --overrides overrides.example.yaml --report report.csv

Run `python extract.py --help` for all options.

See README.md for a full description of what this tool does and how to
configure exceptions.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys

from pdf_extract.exceptions import load_overrides
from pdf_extract.ocr import ocr_available
from pdf_extract.pipeline import process_document
from pdf_extract.report import print_summary, write_report


def find_pdfs(input_dir: str) -> list:
    pattern = os.path.join(input_dir, "**", "*.pdf")
    paths = sorted(glob.glob(pattern, recursive=True))
    return paths


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract substantive body text from a batch of PDFs, "
        "stripping repeated headers/footers/watermarks and web-page "
        "navigation/sidebar chrome, with OCR fallback and manual-review "
        "flagging for low-confidence results."
    )
    parser.add_argument(
        "--input", required=True, help="Directory containing input PDFs (searched recursively)."
    )
    parser.add_argument(
        "--output", required=True, help="Directory to write extracted .txt files to."
    )
    parser.add_argument(
        "--overrides",
        default=None,
        help="Path to a YAML overrides config for exception documents (see "
        "overrides.example.yaml).",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Path to write the CSV summary report to. Defaults to "
        "<output>/report.csv.",
    )
    parser.add_argument(
        "--keep-urls",
        action="store_true",
        help="Do not strip URLs (http/https/www links) from the output text. "
        "By default URLs are removed, since they add noise tokens for "
        "downstream embedding/topic-modeling pipelines.",
    )
    parser.add_argument(
        "--keep-emails",
        action="store_true",
        help="Do not strip e-mail addresses from the output text.",
    )
    parser.add_argument(
        "--keep-separator-lines",
        action="store_true",
        help="Do not strip decorative separator lines (rows of repeated "
        "dashes/underscores used as visual dividers, e.g. above footnotes).",
    )
    parser.add_argument(
        "--keep-decorative-pages",
        action="store_true",
        help="Do not automatically exclude small decorative/cover-page "
        "layouts (title pages, crests, imprint blocks) from output -- keep "
        "them in the main .txt file (flagged) instead of moving them to a "
        "<name>.excluded.txt sidecar file.",
    )
    args = parser.parse_args(argv)

    if not os.path.isdir(args.input):
        print(f"error: input directory not found: {args.input}", file=sys.stderr)
        return 2

    pdf_paths = find_pdfs(args.input)
    if not pdf_paths:
        print(f"warning: no PDF files found under {args.input}", file=sys.stderr)

    overrides = load_overrides(args.overrides)

    if not ocr_available():
        print(
            "note: OCR fallback is unavailable in this environment (the "
            "`tesseract` binary was not found on PATH). Scanned/image-only "
            "pages will be flagged for manual review instead of OCR'd.\n",
            file=sys.stderr,
        )

    os.makedirs(args.output, exist_ok=True)
    report_path = args.report or os.path.join(args.output, "report.csv")

    results = []
    for i, pdf_path in enumerate(pdf_paths, start=1):
        print(f"[{i}/{len(pdf_paths)}] {pdf_path}")
        result = process_document(
            pdf_path,
            args.output,
            overrides,
            remove_urls=not args.keep_urls,
            remove_emails=not args.keep_emails,
            remove_separator_lines=not args.keep_separator_lines,
            auto_exclude_decorative_pages=not args.keep_decorative_pages,
        )
        results.append(result)
        if not result.success:
            print(f"    FAILED: {result.error}")
        elif result.flagged:
            print(f"    flagged: {'; '.join(result.flag_reasons)}")

    write_report(results, report_path)
    print(f"\nWrote summary report to {report_path}")
    print_summary(results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
