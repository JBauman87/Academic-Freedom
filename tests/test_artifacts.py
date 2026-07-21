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


class TestDanglingLinkIntroducerCleanup:
    def test_website_at_dangling_after_url_removal(self):
        # Common in letterheads: "...please see our website at \n" is
        # followed by the URL on its own line/paragraph. Removing the URL
        # would otherwise leave "...website at" dangling with nothing
        # after it.
        text = "please see our website at \n\nwww.safs.ca.)\nOur Society is concerned"
        result = clean_artifacts(text)
        assert "website at" not in result.text
        assert "please see our" in result.text
        assert "Our Society is concerned" in result.text

    def test_email_colon_dangling_after_address_removal(self):
        text = "B3L 4T6, Canada; e-mail: safs@safs.ca\n\nTo date, McGill has not"
        result = clean_artifacts(text)
        assert "e-mail:" not in result.text
        assert "B3L 4T6, Canada" in result.text
        assert "To date, McGill has not" in result.text

    def test_facebook_colon_dangling_after_link_removal(self):
        text = "Montréal (Qc) H2V 4L1 \nFacebook: https://www.facebook.com/safs.ca/\nProfessor of Philosophy"
        result = clean_artifacts(text)
        assert "Facebook:" not in result.text
        assert "facebook.com" not in result.text
        assert "Professor of Philosophy" in result.text

    def test_real_wrapped_sentence_ending_in_at_is_not_touched(self):
        # This must NOT be altered: a real sentence legitimately word-
        # wrapping onto "at" at the end of a line, with no URL/e-mail
        # involved anywhere in the text, must be left completely intact.
        # A generic "strip trailing short word" heuristic would corrupt
        # this; only specific unambiguous link-introducer phrases (e-mail:,
        # website at, etc.) are removed, and only when a link was actually
        # found and removed elsewhere in the text.
        text = (
            "it would have taken no time at\n"
            "all for those who would have been outraged by the article."
        )
        result = clean_artifacts(text)
        assert result.text == text
        assert result.urls_removed == 0

    def test_orphaned_bullet_marker_line_is_dropped(self):
        # A "• Email: <address>" line, once the introducer and address are
        # both removed, can leave a bare bullet character alone on its own
        # line -- pure noise that should be dropped, while a bullet line
        # that still has real content after it must be kept.
        text = (
            "Some real closing paragraph.\n"
            "\u2022 Email: safs@safs.ca\n"
            "\u2022 A bullet item with real content."
        )
        result = clean_artifacts(text)
        assert "\u2022 Email" not in result.text
        lines = result.text.split("\n")
        assert not any(line.strip() == "\u2022" for line in lines)
        assert "A bullet item with real content." in result.text

    def test_introducer_only_cleaned_when_a_link_was_actually_removed(self):
        # If remove_urls/remove_emails are disabled (so nothing is
        # actually stripped), the dangling-introducer cleanup must not
        # run either -- otherwise it could remove a legitimate
        # "e-mail:" label that's still followed by a real address the
        # user chose to keep.
        text = "Contact us; e-mail: safs@safs.ca for more information."
        result = clean_artifacts(text, remove_emails=False)
        assert "e-mail:" in result.text
        assert "safs@safs.ca" in result.text


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



class TestLigatureNormalization:
    def test_fi_ligature_normalized(self):
        text = "The professor was \ufb01red after a lengthy investigation."
        result = clean_artifacts(text)
        assert "\ufb01" not in result.text
        assert "fired" in result.text
        assert result.ligatures_normalized == 1

    def test_multiple_ligatures_in_one_document(self):
        text = "The \ufb01rst of\ufb01ce visit was brie\ufb02y noted in the of\ufb01cial record."
        result = clean_artifacts(text)
        assert result.ligatures_normalized == 4
        assert "first" in result.text
        assert "office" in result.text
        assert "briefly" in result.text
        assert "official" in result.text

    def test_can_be_disabled(self):
        text = "He was \ufb01red."
        result = clean_artifacts(text, normalize_ligatures=False)
        assert "\ufb01" in result.text
        assert result.ligatures_normalized == 0


class TestIconGlyphRemoval:
    def test_private_use_area_glyph_removed(self):
        # Social-share icon fonts commonly land in Unicode's Private Use
        # Area when extracted as plain text (e.g. \uf060, \uf39e).
        text = "Share this article \uf060\uf39e\uf0e1 with others."
        result = clean_artifacts(text)
        assert "\uf060" not in result.text
        assert "\uf39e" not in result.text
        assert "\uf0e1" not in result.text
        assert result.icon_glyphs_removed == 3
        assert "Share this article" in result.text
        assert "with others." in result.text

    def test_can_be_disabled(self):
        text = "Icons: \uf060\uf39e"
        result = clean_artifacts(text, remove_icon_glyphs=False)
        assert "\uf060" in result.text
        assert result.icon_glyphs_removed == 0


class TestHtmlTagRemoval:
    def test_well_formed_anchor_tag_removed(self):
        text = 'Download the film from <a href="http://example.com">here</a>.'
        result = clean_artifacts(text)
        assert "<a href" not in result.text
        assert "</a>" not in result.text
        assert result.html_tags_removed == 2

    def test_malformed_anchor_tag_with_mismatched_quotes_removed(self):
        # Reproduces the real observed defect: a leaked anchor tag whose
        # quote characters were mangled by the PDF's own text encoding,
        # e.g. from a real Canadian Dimension article export.
        text = (
            "the film will be available in the near future from "
            '<a href="\nhttp://www.dwdtv.org/" target=_blank">www.dwdtv.org.'
        )
        result = clean_artifacts(text)
        assert "<a href" not in result.text
        assert "target=_blank" not in result.text
        assert "available in the near future from" in result.text

    def test_can_be_disabled(self):
        text = 'See <a href="http://example.com">this link</a>.'
        result = clean_artifacts(text, remove_html_tags=False, remove_urls=False)
        assert "<a href" in result.text
        assert result.html_tags_removed == 0

    def test_real_comparison_operator_not_mistaken_for_a_tag(self):
        # A bare "<" used as a mathematical/comparison symbol in body
        # text must never be treated as the start of an HTML tag.
        text = "The measured value was found to be x < 5 in every trial."
        result = clean_artifacts(text)
        assert result.text == text
        assert result.html_tags_removed == 0
