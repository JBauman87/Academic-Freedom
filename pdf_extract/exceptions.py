"""
exceptions.py

Loads and applies per-document overrides from a YAML config file, for the
"exceptional cases" the user described where only certain portions of a
document should be extracted (or where a document should be skipped
entirely and handled by hand).

This lets the vast majority of documents run through the fully-automatic
pipeline while a small, explicit, human-reviewable list of rules handles
the exceptions -- rather than requiring every odd document to be pulled out
of the batch and processed manually from scratch.

Config file format (YAML), matched by filename (exact name or glob):

    overrides:
      - match: "some-report.pdf"
        mode: pages
        pages: [4, 5, 6]          # 1-indexed, inclusive

      - match: "another-ruling.pdf"
        mode: heading_range
        start_heading: "DISPOSITION"
        end_heading: "Released:"   # optional; end of document if omitted

      - match: "*.transcript.pdf"
        mode: skip
        reason: "Transcript formatting is too irregular for automatic extraction"

Modes:
  - "pages": only extract the given 1-indexed page numbers.
  - "heading_range": only extract text from the first block matching
    start_heading (case-insensitive substring) through the block matching
    end_heading (exclusive), or end of document if end_heading is omitted
    or not found.
  - "skip": don't run the automatic pipeline at all; just record that this
    file needs fully manual handling.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import List, Optional

import yaml


@dataclass
class OverrideRule:
    match: str
    mode: str  # "pages" | "heading_range" | "skip"
    pages: Optional[List[int]] = None
    start_heading: Optional[str] = None
    end_heading: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class OverrideConfig:
    rules: List[OverrideRule] = field(default_factory=list)

    def find_rule(self, filename: str) -> Optional[OverrideRule]:
        for rule in self.rules:
            if fnmatch.fnmatch(filename, rule.match) or filename == rule.match:
                return rule
        return None


def load_overrides(config_path: Optional[str]) -> OverrideConfig:
    if not config_path or not os.path.exists(config_path):
        return OverrideConfig(rules=[])

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    rules = []
    for entry in raw.get("overrides", []) or []:
        rules.append(
            OverrideRule(
                match=entry["match"],
                mode=entry.get("mode", "pages"),
                pages=entry.get("pages"),
                start_heading=entry.get("start_heading"),
                end_heading=entry.get("end_heading"),
                reason=entry.get("reason"),
            )
        )
    return OverrideConfig(rules=rules)


def apply_page_filter(doc_pages, page_numbers_1indexed: List[int]):
    """Return only the DocumentLayout pages whose 1-indexed page number is
    in the given list."""
    wanted = {p - 1 for p in page_numbers_1indexed}
    return [p for p in doc_pages if p.page_index in wanted]


def apply_heading_range_filter(doc_pages, start_heading: str, end_heading: Optional[str]):
    """
    Return a filtered copy of doc_pages' blocks, keeping only blocks from
    the first block containing start_heading (inclusive) through the block
    containing end_heading (exclusive), across the whole document in
    reading order (page index, then block index).
    """
    import copy

    start_lower = start_heading.lower()
    end_lower = end_heading.lower() if end_heading else None

    # Flatten to (page_index, block) pairs in document order.
    flat = []
    for page in doc_pages:
        for block in sorted(page.blocks, key=lambda b: b.block_index):
            flat.append((page.page_index, block))

    started = False
    ended = False
    keep_ids = set()

    for _page_index, block in flat:
        text_lower = block.text.lower()
        if not started and start_lower in text_lower:
            started = True
        if started and end_lower and end_lower in text_lower:
            ended = True
            break
        if started:
            keep_ids.add(id(block))

    filtered_pages = []
    for page in doc_pages:
        new_page = copy.copy(page)
        new_page.blocks = [b for b in page.blocks if id(b) in keep_ids]
        filtered_pages.append(new_page)

    return filtered_pages, started, ended
