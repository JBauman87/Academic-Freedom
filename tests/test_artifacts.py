"""Tests for pdf_extract/artifacts.py -- URL/e-mail/separator-line cleanup
applied to final extracted text, primarily to avoid feeding noise tokens
into a downstream word-embedding/topic-modeling pipeline (e.g. BERTopic)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdf_extract.artifacts import clean_artifacts


class TestUrlRemoval:
    def test_bare_https_url_removed(self):
        text = "See the report at https://example.com/report.pdf for details."
        result = clean_artifacts(text)
        assert "https://" not in result.text
        assert "example.com" not in result.text
        assert result.urls_removed == 1
        assert "See the report at" in result.text
        assert "for details." in result.text

    def test_www_url_without_scheme_removed(self):
        text = "Visit www.example.com/page for further information."
        result = clean_artifacts(text)
        assert "www.example.com" not in result.text
        assert result.urls_removed == 1

    def test_trailing_sentence_punctuation_preserved(self):
        # A URL glued directly to sentence-ending punctuation (no space)
        # should have that punctuation preserved once the URL is removed.
        text = "Accessed 18 July 2017: http://www.macleans.ca/news/article/."
        result = clean_artifacts(text)
        assert "http://" not in result.text
        assert result.text.rstrip().endswith(".")

    def test_url_wrapped_across_a_line_break_is_fully_removed(self):
        # PDF exports frequently word-wrap long URLs at a hyphen, e.g.
        # "...statement-academic-\nfreedom." A naive whitespace-bounded
        # regex would remove only the first line's fragment and leave the
        # wrapped continuation ("freedom.") behind as a stray, meaningless
        # word fragment.
        text = (
            "See https://www.mcgill.ca/secretariat/statement-academic-\n"
            "freedom. for details."
        )
        result = clean_artifacts(text)
        assert "mcgill.ca" not in result.text
        assert "freedom." not in result.text
        assert result.urls_removed == 1
        assert "for details." in result.text

    def test_multiple_urls_in_footnote_block(self):
        text = (
            "1. See https://a.example.com/one for the first source.\n"
            "2. See https://b.example.com/two for the second source."
        )
        result = clean_artifacts(text)
        assert result.urls_removed == 2
        assert "example.com" not in result.text
        assert "1. See" in result.text
        assert "2. See" in result.text

    def test_can_be_disabled(self):
        text = "Visit https://example.com for more."
        result = clean_artifacts(text, remove_urls=False)
        assert "https://example.com" in result.text
        assert result.urls_removed == 0


class TestEmailRemoval:
    def test_email_address_removed(self):
        text = "Contact the author at jane.doe@example.com with questions."
        result = clean_artifacts(text)
        assert "@" not in result.text
        assert result.emails_removed == 1
        assert "Contact the author at" in result.text
        assert "with questions." in result.text

    def test_can_be_disabled(self):
        text = "Contact jane@example.com for more."
        result = clean_artifacts(text, remove_emails=False)
        assert "jane@example.com" in result.text


class TestSeparatorLineRemoval:
    def test_dash_divider_line_removed(self):
        text = "Body text here.\n—————————————————————\n1. A footnote."
        result = clean_artifacts(text)
        assert "—————" not in result.text
        assert result.separator_lines_removed == 1
        assert "Body text here." in result.text
        assert "A footnote." in result.text

    def test_underscore_divider_removed(self):
        text = "Body text.\n________________\nMore text."
        result = clean_artifacts(text)
        assert "____" not in result.text

    def test_short_dashes_in_prose_not_removed(self):
        # A short em-dash or double-hyphen used mid-sentence is legitimate
        # punctuation, not a decorative divider, and must be preserved.
        text = "This is a clause--set off by dashes--within a sentence."
        result = clean_artifacts(text)
        assert "dashes--within" in result.text
        assert result.separator_lines_removed == 0

    def test_can_be_disabled(self):
        text = "Text.\n—————————————————————\nMore."
        result = clean_artifacts(text, remove_separator_lines=False)
        assert "—————" in result.text


class TestEmptyBracketCleanup:
    def test_parenthetical_containing_only_a_url_is_cleaned_up(self):
        text = "This is discussed elsewhere (see https://example.com/page)."
        result = clean_artifacts(text)
        assert "()" not in result.text
        assert "example.com" not in result.text


class TestCombinedCleanup:
    def test_realistic_footnote_block(self):
        text = (
            "Andrew Potter, \"How a snowstorm exposed Quebec's real "
            "problem,\" MacLean's, 20 March 2017. Accessed 18 July 2017: "
            "http://www.macleans.ca/news/canada/how-a-snowstorm-exposed-"
            "quebecs-real-problem-social-malaise/.\n"
            "—————————————————————\n"
            "Contact: safs@safs.ca"
        )
        result = clean_artifacts(text)
        assert "http://" not in result.text
        assert "@" not in result.text
        assert "—————" not in result.text
        assert "Andrew Potter" in result.text
        assert result.urls_removed == 1
        assert result.emails_removed == 1
        assert result.separator_lines_removed == 1
