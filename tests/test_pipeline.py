"""
Tests exercising the pipeline's structural heuristics against the
synthetic fixtures in tests/fixtures/ (see tests/make_fixtures.py for how
they were generated and what real-world pattern each one approximates).

These tests check *structural* properties (e.g. "the repeated header text
does not appear in the output more than once" / "sidebar phrases are
absent" / "body paragraphs are present") rather than exact string
equality, since the fixtures use invented placeholder text.
"""

import os
import sys

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pdf_extract.exceptions import OverrideConfig, load_overrides
from pdf_extract.pipeline import process_document


def _run(tmp_path, filename):
    src = os.path.join(FIXTURES_DIR, filename)
    out_dir = str(tmp_path)
    result = process_document(src, out_dir, OverrideConfig(rules=[]))
    assert result.success, f"pipeline failed on {filename}: {result.error}"
    with open(result.output_path, "r", encoding="utf-8") as f:
        text = f.read()
    return result, text


class TestLegalRuling:
    def test_body_paragraphs_present(self, tmp_path):
        _, text = _run(tmp_path, "legal_ruling.pdf")
        assert "synthetic paragraph number 1-a" in text
        assert "synthetic paragraph number 4-a" in text
        assert "fictional tribunal considered" in text
        assert "Further fictional analysis specific to page 2" in text

    def test_repeated_page_header_removed(self, tmp_path):
        _, text = _run(tmp_path, "legal_ruling.pdf")
        # "Page: N" should not survive as standalone boilerplate.
        assert "Page: 1" not in text
        assert "Page: 2" not in text

    def test_repeated_watermark_removed(self, tmp_path):
        _, text = _run(tmp_path, "legal_ruling.pdf")
        assert "FAKECITE" not in text

    def test_not_flagged_for_a_normal_clean_ruling(self, tmp_path):
        result, _ = _run(tmp_path, "legal_ruling.pdf")
        # A clean, fully-text multi-page ruling with plenty of body text
        # per page should not trip the low-yield / high-removal flags.
        assert not result.flagged, result.flag_reasons


class TestNewsArticle:
    def test_body_paragraphs_present(self, tmp_path):
        _, text = _run(tmp_path, "news_article.pdf")
        assert "returned to a fictional workplace" in text
        assert "placeholder quote text" in text

    def test_nav_chrome_removed(self, tmp_path):
        _, text = _run(tmp_path, "news_article.pdf")
        assert "Sign In" not in text
        assert "\nMenu\n" not in f"\n{text}\n"

    def test_listen_widget_removed(self, tmp_path):
        _, text = _run(tmp_path, "news_article.pdf")
        assert "Listen to this article" not in text
        assert "Estimated 2 minutes" not in text

    def test_sidebar_removed(self, tmp_path):
        _, text = _run(tmp_path, "news_article.pdf")
        assert "Popular Now" not in text
        assert "Unrelated headline" not in text

    def test_trending_videos_removed(self, tmp_path):
        _, text = _run(tmp_path, "news_article.pdf")
        assert "Trending Videos" not in text
        assert "2:57" not in text
        assert "Some unrelated video title" not in text


class TestLetter:
    def test_body_present(self, tmp_path):
        _, text = _run(tmp_path, "letter.pdf")
        assert "Dear President Recipient" in text
        assert "synthetic letter body" in text

    def test_letterhead_sidebar_not_merged_into_body_incoherently(self, tmp_path):
        # We don't require the letterhead to be fully removed (a 2-line
        # letter's "sidebar" isn't repeated across pages, and is long
        # enough in aggregate that the geometry heuristic may reasonably
        # leave individual short lines alone) -- but the core letter body
        # must still be intact and readable.
        _, text = _run(tmp_path, "letter.pdf")
        assert "Sincerely" in text
        assert "Jane Doe, PhD" in text


class TestReport:
    def test_body_paragraphs_present(self, tmp_path):
        _, text = _run(tmp_path, "report.pdf")
        assert "synthetic report body text on page" in text
        assert "second synthetic paragraph specific to page" in text

    def test_running_header_removed(self, tmp_path):
        _, text = _run(tmp_path, "report.pdf")
        assert "FAKE Report on a Synthetic Topic" not in text

    def test_running_footer_removed(self, tmp_path):
        _, text = _run(tmp_path, "report.pdf")
        assert "Fake Association of Testing Teachers" not in text

    def test_page_numbers_removed(self, tmp_path):
        _, text = _run(tmp_path, "report.pdf")
        # Footer page numbers ("1", "2", "3" alone on a line) should not
        # appear as standalone tokens.
        lines = [l.strip() for l in text.splitlines()]
        assert "1" not in lines
        assert "2" not in lines
        assert "3" not in lines


class TestTwoColumnBody:
    def test_left_column_read_before_right_column(self, tmp_path):
        # This is the core regression test for the column-aware reassembly
        # fix: a genuine two-column layout must be read fully down the
        # left column before moving to the right column, not interleaved
        # line-by-line (which would scramble the two independent
        # paragraphs together).
        _, text = _run(tmp_path, "two_column_body.pdf")
        left_pos = text.find("left column of a synthetic")
        right_pos = text.find("right column of the same")
        assert left_pos != -1
        assert right_pos != -1
        assert left_pos < right_pos

        left_end = text.find("columns together.")
        assert left_end != -1
        assert left_end < right_pos

    def test_not_flagged(self, tmp_path):
        result, _ = _run(tmp_path, "two_column_body.pdf")
        assert not result.flagged, result.flag_reasons


class TestDecorativeTitlePage:
    def test_excluded_from_main_output_and_flagged(self, tmp_path):
        # A small, purely decorative page (title/crest-style content) is
        # automatically excluded from the main output rather than left in
        # a garbled state -- for a downstream embedding/topic-modeling
        # pipeline, dropping noise is strictly better than including
        # scrambled fragments of it.
        result, text = _run(tmp_path, "decorative_title.pdf")
        assert text == ""
        assert result.flagged
        assert any("automatically excluded" in r for r in result.flag_reasons)
        assert result.auto_excluded_pages == [0]

    def test_original_text_preserved_in_sidecar_file(self, tmp_path):
        # Nothing is silently discarded: the original block text for an
        # excluded page must be fully recoverable from the sidecar file.
        result, _ = _run(tmp_path, "decorative_title.pdf")
        assert result.excluded_output_path is not None
        with open(result.excluded_output_path, encoding="utf-8") as f:
            excluded_text = f.read()
        assert "Synthetic Topic For\nPipeline Testing Purposes\nOnly" in excluded_text
        assert "Jane Fakeauthor\nUniversity of Testville" in excluded_text
        for word in ["FAKE", "REPORT", "ON", "A"]:
            assert word in excluded_text


class TestDecorativeLayoutSafetyCap:
    def test_large_decorative_looking_page_is_flagged_not_excluded(self, tmp_path):
        # A page that matches a decorative-layout signature (e.g. many
        # short blocks) but carries substantially more text than the
        # auto-exclusion safety threshold must be flagged for a human to
        # check, never silently dropped -- excluding real content by
        # mistake would be far worse than leaving a false-positive flag.
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        path = os.path.join(str(tmp_path), "large_fragmented.pdf")
        c = canvas.Canvas(path, pagesize=letter)
        width, height = letter
        # Start well below the top-of-page nav-strip zone (columns.py
        # treats short blocks hugging the very top of the page as likely
        # masthead/nav chrome, which is unrelated to what this test is
        # checking).
        y = height - 60
        # Many separate, short blocks (<=20 chars each, so this matches
        # the "many short fragments" decorative-layout signature) whose
        # combined text nonetheless exceeds the auto-exclusion safety cap
        # (confidence.MAX_CHARS_FOR_AUTO_EXCLUDE, 900 chars): 55 blocks
        # x 20 chars each = 1100 chars total, comfortably over the cap.
        for i in range(55):
            c.setFont("Helvetica", 8)
            c.drawString(72, y, f"Item number {i:02d} real.")
            y -= 13
        c.showPage()
        c.save()

        result = process_document(path, str(tmp_path), OverrideConfig(rules=[]))
        assert result.success
        assert result.auto_excluded_pages == []
        with open(result.output_path, encoding="utf-8") as f:
            text = f.read()
        assert "Item number 00" in text
        assert "real." in text
        assert any(
            "was NOT auto-excluded" in r for r in result.flag_reasons
        ), result.flag_reasons


class TestSparseHeadlineWithSidebar:
    def test_sidebar_teasers_removed(self, tmp_path):
        # Regression test for a real bug found via the samples/ feedback
        # loop: on a sparse page with no unambiguously long body block,
        # the old length-ranked main-column estimator could be corrupted
        # by short sidebar fragments, silently disabling sidebar removal
        # entirely. The main column must still be correctly identified as
        # the left-hand real content, and the right-hand teaser list must
        # be stripped.
        _, text = _run(tmp_path, "sparse_headline_with_sidebar.pdf")
        assert "Unrelated teaser" not in text
        assert "Second unrelated" not in text
        assert "Third teaser" not in text

    def test_real_headline_and_caption_preserved(self, tmp_path):
        _, text = _run(tmp_path, "sparse_headline_with_sidebar.pdf")
        assert "photo caption describing the article" in text
        assert "Sparse Headline That" in text
        assert "Barely Wraps To Two Lines" in text


class TestWebsiteChromePage:
    def test_nav_bar_and_footer_removed(self, tmp_path):
        # Regression test for a real bug found via the samples/ feedback
        # loop: a page that is almost entirely site navigation/footer
        # chrome (generic section names and footer links not present in
        # any phrase denylist) must have that chrome stripped via the
        # horizontal nav-row geometry heuristic, leaving only the small
        # amount of real content.
        _, text = _run(tmp_path, "website_chrome_page.pdf")
        for nav_item in ["Home", "Local", "World", "Sports", "Opinion", "Culture", "Podcasts"]:
            assert nav_item not in text
        for footer_item in ["About Us", "Careers", "Advertise", "Privacy Policy", "Terms"]:
            assert footer_item not in text

    def test_real_content_preserved(self, tmp_path):
        _, text = _run(tmp_path, "website_chrome_page.pdf")
        assert "Back" in text
        assert "A short real headline for this fixture page" in text

    def test_flagged_as_low_yield(self, tmp_path):
        # After stripping nearly all of this page's chrome, only a
        # couple of words of real content remain -- correctly flagged as
        # low text yield rather than silently producing a near-empty file
        # with no explanation.
        result, _ = _run(tmp_path, "website_chrome_page.pdf")
        assert result.flagged
        assert any("low text yield" in r for r in result.flag_reasons)


class TestOverrides:
    def test_skip_mode_flags_without_writing_output(self, tmp_path):
        overrides = OverrideConfig(rules=[])
        from pdf_extract.exceptions import OverrideRule

        overrides.rules.append(
            OverrideRule(match="report.pdf", mode="skip", reason="test skip")
        )
        src = os.path.join(FIXTURES_DIR, "report.pdf")
        result = process_document(src, str(tmp_path), overrides)
        assert result.success
        assert result.flagged
        assert result.output_path is None
        assert any("manual-only" in r for r in result.flag_reasons)

    def test_pages_mode_restricts_output(self, tmp_path):
        from pdf_extract.exceptions import OverrideRule

        overrides = OverrideConfig(
            rules=[OverrideRule(match="report.pdf", mode="pages", pages=[2])]
        )
        src = os.path.join(FIXTURES_DIR, "report.pdf")
        result = process_document(src, str(tmp_path), overrides)
        assert result.success
        with open(result.output_path, encoding="utf-8") as f:
            text = f.read()
        assert "page 2" in text
        assert "page 1" not in text
        assert "page 3" not in text

    def test_heading_range_mode(self, tmp_path):
        from pdf_extract.exceptions import OverrideRule

        overrides = OverrideConfig(
            rules=[
                OverrideRule(
                    match="report.pdf",
                    mode="heading_range",
                    start_heading="second synthetic paragraph specific to page 2",
                )
            ]
        )
        src = os.path.join(FIXTURES_DIR, "report.pdf")
        result = process_document(src, str(tmp_path), overrides)
        assert result.success
        with open(result.output_path, encoding="utf-8") as f:
            text = f.read()
        assert "second synthetic paragraph specific to page 2" in text


class TestConfidenceFlagging:
    def test_empty_pdf_is_flagged(self, tmp_path):
        # A PDF with a page but no text at all should be flagged as
        # low-yield / zero-content rather than silently producing an
        # empty file with no explanation.
        import fitz

        empty_path = os.path.join(str(tmp_path), "empty.pdf")
        doc = fitz.open()
        doc.new_page()
        doc.save(empty_path)
        doc.close()

        result = process_document(empty_path, str(tmp_path), OverrideConfig(rules=[]))
        assert result.success  # not an error, just empty
        assert result.flagged

    def test_corrupt_file_does_not_crash_batch(self, tmp_path):
        bad_path = os.path.join(str(tmp_path), "not_a_pdf.pdf")
        with open(bad_path, "wb") as f:
            f.write(b"this is not a valid pdf file at all")

        result = process_document(bad_path, str(tmp_path), OverrideConfig(rules=[]))
        assert result.success is False
        assert result.flagged
        assert result.error
