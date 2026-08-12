"""Tests for the YAML overrides config loader (pdf_extract/exceptions.py)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdf_extract.exceptions import load_overrides


def test_load_overrides_from_example_file():
    config_path = os.path.join(os.path.dirname(__file__), "..", "overrides.example.yaml")
    config = load_overrides(config_path)
    assert len(config.rules) == 3

    skip_rule = config.find_rule("example-scanned-transcript.pdf")
    assert skip_rule is not None
    assert skip_rule.mode == "skip"

    pages_rule = config.find_rule("example-report-with-appendix.pdf")
    assert pages_rule is not None
    assert pages_rule.mode == "pages"
    assert pages_rule.pages == [1, 2, 3]

    heading_rule = config.find_rule("example-ruling-disposition-only.pdf")
    assert heading_rule is not None
    assert heading_rule.mode == "heading_range"
    assert heading_rule.start_heading == "DISPOSITION"


def test_load_overrides_missing_file_returns_empty_config():
    config = load_overrides("/nonexistent/path/overrides.yaml")
    assert config.rules == []


def test_load_overrides_none_path_returns_empty_config():
    config = load_overrides(None)
    assert config.rules == []


def test_glob_matching():
    config_path = os.path.join(os.path.dirname(__file__), "..", "overrides.example.yaml")
    config = load_overrides(config_path)
    # Not in the example file, but exercise glob matching generically.
    from pdf_extract.exceptions import OverrideConfig, OverrideRule

    globby = OverrideConfig(rules=[OverrideRule(match="transcript-*.pdf", mode="skip")])
    assert globby.find_rule("transcript-2020-01.pdf") is not None
    assert globby.find_rule("unrelated.pdf") is None
