"""
pipeline.py

Orchestrates the full per-document extraction flow:

    1. Check for an override rule (skip / page-restrict / heading-range).
    2. Extract layout (text blocks + metadata) via layout.py.
    3. OCR any pages with no text layer (best effort).
    4. Strip cross-page repeated boilerplate via dedupe.py.
    5. Strip same-page nav/sidebar chrome via columns.py.
    6. Reassemble surviving blocks into reading-order text.
    7. Evaluate confidence / flag for manual review via confidence.py.
    8. Write the .txt output (unless mode == skip).

Every document is processed inside a try/except so that one corrupt,
encrypted, or otherwise malformed PDF cannot halt a batch of several
hundred. Failures are recorded in the returned DocumentResult and in the
summary report (see report.py) rather than raised.
"""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass, field
from typing import List, Optional

from . import columns, confidence, dedupe, ocr
from .exceptions import (
    OverrideConfig,
    apply_heading_range_filter,
    apply_page_filter,
)
from .layout import DocumentLayout, extract_layout
from .reassemble import reassemble_text


@dataclass
class DocumentResult:
    source_path: str
    output_path: Optional[str] = None
    success: bool = False
    error: str = ""
    page_count: int = 0
    final_char_count: int = 0
    flagged: bool = False
    flag_reasons: List[str] = field(default_factory=list)
    override_mode: str = ""
    ocr_pages_used: List[int] = field(default_factory=list)


def process_document(
    pdf_path: str,
    output_dir: str,
    overrides: OverrideConfig,
) -> DocumentResult:
    filename = os.path.basename(pdf_path)
    result = DocumentResult(source_path=pdf_path)

    rule = overrides.find_rule(filename)
    override_mode = rule.mode if rule else ""
    result.override_mode = override_mode

    if rule and rule.mode == "skip":
        result.success = True
        result.flagged = True
        result.flag_reasons = [
            f"marked as manual-only by override config"
            + (f" ({rule.reason})" if rule.reason else "")
        ]
        return result

    try:
        doc = extract_layout(pdf_path)
        result.page_count = doc.page_count

        # --- Apply override page/heading restriction, if any ---------
        pages_needing_ocr_before_override = [
            p.page_index for p in doc.pages if not p.has_text_layer
        ]

        if rule and rule.mode == "pages" and rule.pages:
            doc.pages = apply_page_filter(doc.pages, rule.pages)
        elif rule and rule.mode == "heading_range" and rule.start_heading:
            new_pages, started, _ended = apply_heading_range_filter(
                doc.pages, rule.start_heading, rule.end_heading
            )
            doc.pages = new_pages
            if not started:
                result.flagged = True
                result.flag_reasons.append(
                    f"heading_range override: start_heading "
                    f"{rule.start_heading!r} was not found in this document"
                )

        # --- OCR fallback for pages with no text layer ---------------
        pages_needing_ocr = [p.page_index for p in doc.pages if not p.has_text_layer]
        ocr_succeeded_pages: List[int] = []
        if pages_needing_ocr:
            ocr_results = ocr.ocr_document_pages(pdf_path, pages_needing_ocr)
            for page in doc.pages:
                if page.page_index in ocr_results:
                    text = ocr_results[page.page_index]
                    if text:
                        # Synthesize a single full-page block carrying the
                        # OCR'd text so it flows through the same
                        # dedupe/column/reassemble path as native text.
                        from .layout import TextBlock

                        synthetic = TextBlock(
                            page_index=page.page_index,
                            page_width=page.width,
                            page_height=page.height,
                            bbox=(0, 0, page.width, page.height),
                            text=text,
                            font_sizes=[],
                            block_index=0,
                        )
                        page.blocks = [synthetic]
                        ocr_succeeded_pages.append(page.page_index)
        result.ocr_pages_used = ocr_succeeded_pages

        original_char_count = sum(len(b.text) for p in doc.pages for b in p.blocks)

        # --- Strip repeated cross-page boilerplate --------------------
        dedupe_result = dedupe.find_repeated_boilerplate(doc)

        # --- Strip same-page chrome (nav/sidebar/promo) ---------------
        all_removed_ids = set(dedupe_result.removed_block_ids)
        for page in doc.pages:
            remaining_blocks = [b for b in page.blocks if id(b) not in all_removed_ids]
            # Build a lightweight page view for the column heuristic so it
            # only ever considers blocks that survived dedupe.
            from .layout import PageLayout as _PageLayout

            view = _PageLayout(
                page_index=page.page_index,
                width=page.width,
                height=page.height,
                blocks=remaining_blocks,
                has_text_layer=page.has_text_layer,
            )
            col_result = columns.strip_page_chrome(view)
            all_removed_ids |= col_result.removed_block_ids

        kept_ids = set()
        for page in doc.pages:
            for block in page.blocks:
                if id(block) not in all_removed_ids:
                    kept_ids.add(id(block))

        # Summarize per-page block geometry (font-size spread, fragment
        # count) over the *surviving* blocks only, so the decorative-layout
        # check reflects what will actually be reassembled, not blocks that
        # were already stripped as boilerplate/chrome.
        page_layout_stats: List[confidence.PageLayoutStats] = []
        for page in doc.pages:
            surviving = [b for b in page.blocks if id(b) in kept_ids and b.text.strip()]
            if not surviving:
                continue
            avg_sizes = [b.avg_font_size for b in surviving if b.avg_font_size > 0]
            font_size_ratio = (max(avg_sizes) / min(avg_sizes)) if avg_sizes else 1.0
            avg_chars = sum(len(b.text) for b in surviving) / len(surviving)
            page_layout_stats.append(
                confidence.PageLayoutStats(
                    page_index=page.page_index,
                    block_count=len(surviving),
                    avg_chars_per_block=avg_chars,
                    font_size_ratio=font_size_ratio,
                )
            )

        final_text = reassemble_text(doc, kept_ids)
        result.final_char_count = len(final_text)

        contributing_pages = len(
            {b.page_index for p in doc.pages for b in p.blocks if id(b) in kept_ids}
        )

        removed_char_count = original_char_count - len(
            "".join(b.text for p in doc.pages for b in p.blocks if id(b) in kept_ids)
        )

        conf = confidence.evaluate(
            page_count=result.page_count,
            original_char_count=original_char_count,
            final_char_count=result.final_char_count,
            removed_char_count=removed_char_count,
            pages_needing_ocr=pages_needing_ocr_before_override,
            ocr_succeeded_pages=ocr_succeeded_pages,
            contributing_pages=contributing_pages,
            override_applied=bool(rule),
            override_mode=override_mode,
            page_layout_stats=page_layout_stats,
        )
        result.flagged = result.flagged or conf.flagged
        result.flag_reasons.extend(conf.reasons)

        # --- Write output ----------------------------------------------
        os.makedirs(output_dir, exist_ok=True)
        stem = os.path.splitext(filename)[0]
        out_path = os.path.join(output_dir, f"{stem}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(final_text)
        result.output_path = out_path
        result.success = True

    except Exception as exc:  # noqa: BLE001 - intentional catch-all for batch robustness
        result.success = False
        result.error = f"{type(exc).__name__}: {exc}"
        result.flagged = True
        result.flag_reasons.append(f"processing error: {result.error}")
        # Full traceback is not stored in the result (kept for terminal/log
        # output only) so the CSV report stays compact.
        traceback.print_exc()

    return result
