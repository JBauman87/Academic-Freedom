"""
make_fixtures.py

Generates small synthetic PDF fixtures (via reportlab) that approximate the
structural patterns seen in the four example document types the pipeline
targets, WITHOUT reproducing any actual document content:

  1. legal_ruling.pdf   - multi-page, single main column, repeated
                          "Page: N" header and a rotated watermark-style
                          string down the right margin of every page (the
                          CanLII-style pattern).
  2. news_article.pdf   - a single page laid out with a narrow right-hand
                          "sidebar" column (short items, a duration-style
                          badge) alongside a wide main-column article body,
                          plus a top nav bar and short UI phrases.
  3. letter.pdf          - a short 2-page letter with a letterhead-style
                          sidebar of contact info on page 1 alongside the
                          body text (two-column layout on a single page).
  4. report.pdf          - multi-page report with a running header/footer
                          repeated on every page (title + date + page
                          number + org name), similar to the CAUT report.
  5. decorative_title.pdf - a single decorative cover/title page mixing a
                          large stylized headline (rendered as several
                          oversized, tall single-word blocks stacked in
                          the left margin) with normal-sized subtitle and
                          author text -- approximating the title-page /
                          acknowledgements pattern that previously caused
                          words to be interleaved/torn apart in output.
  6. two_column_body.pdf - a single page with a genuine two-column body
                          (no header/footer/sidebar), to confirm clean
                          side-by-side column text is still reassembled
                          correctly (left column fully, then right column
                          fully) rather than being interleaved line-by-line.

These fixtures are intentionally generic/invented text -- they are for
testing the pipeline's structural heuristics (repetition-by-position,
column geometry, chrome phrases), not for validating against any
particular real-world document.

Run directly to (re)generate the fixtures:
    python tests/make_fixtures.py
"""

from __future__ import annotations

import os

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def make_legal_ruling(path: str, num_pages: int = 4) -> None:
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter

    for page_num in range(1, num_pages + 1):
        # Repeated header
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(width / 2, height - 50, f"Page: {page_num}")

        # Rotated watermark down the right margin (repeats every page)
        c.saveState()
        c.translate(width - 20, height / 2)
        c.rotate(90)
        c.setFont("Helvetica", 8)
        c.drawCentredString(0, 0, "2024 EXCOURT 1234 (FAKECITE)")
        c.restoreState()

        # Body paragraphs (single column)
        c.setFont("Helvetica", 11)
        text_obj = c.beginText(72, height - 100)
        text_obj.setLeading(16)
        # Body content is deliberately made distinct per page (not just a
        # changing bracket number) -- a real ruling's substantive text
        # differs page to page, and fixtures that repeat verbatim body
        # text across every page would be indistinguishable, by the
        # pipeline's own repetition heuristics, from actual boilerplate.
        paragraphs = [
            f"[{page_num * 10 + 1}] This is synthetic paragraph number "
            f"{page_num}-a of ruling text used only to validate the "
            f"extraction pipeline. It describes fictional event number "
            f"{page_num} in a fictional dispute between fictional parties "
            "for testing purposes only.",
            f"[{page_num * 10 + 2}] The fictional tribunal considered "
            f"fictional argument number {page_num} and reached an interim "
            "fictional conclusion on that point, recorded here purely to "
            "give the layout extractor a realistic amount of distinct "
            "body text to work with.",
            f"[{page_num * 10 + 3}] Further fictional analysis specific to "
            f"page {page_num} continues in this synthetic paragraph, which "
            "exists only to exercise the pipeline's reading-order "
            "reassembly logic across multiple pages with distinct content.",
        ]
        for para in paragraphs:
            for line in _wrap(para, 90):
                text_obj.textLine(line)
            text_obj.textLine("")
        c.drawText(text_obj)

        c.showPage()

    c.save()


def make_news_article(path: str) -> None:
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter

    # Top nav bar
    c.setFont("Helvetica", 9)
    c.drawString(72, height - 40, "Menu")
    c.drawCentredString(width / 2, height - 40, "FAKE NEWS NETWORK")
    c.drawRightString(width - 72, height - 40, "Sign In")

    # Section label + headline (main column, left side)
    c.setFont("Helvetica", 9)
    c.drawString(72, height - 70, "Local")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(72, height - 95, "Fictional Person Returns To Fictional Job")

    c.setFont("Helvetica", 9)
    c.drawString(72, height - 115, "Fake News Staff - Posted: Jan 1, 2024")

    # "Listen to this article" widget (chrome, main column area but short)
    c.setFont("Helvetica", 8)
    c.drawString(72, height - 135, "Listen to this article")
    c.drawString(72, height - 148, "Estimated 2 minutes")

    # Main article body (wide left column, ~60% of page width)
    c.setFont("Helvetica", 10.5)
    text_obj = c.beginText(72, height - 175)
    text_obj.setLeading(14)
    body_paragraphs = [
        "A fictional person returned to a fictional workplace today after "
        "a brief and entirely made-up controversy involving a fictional "
        "restructuring plan. Colleagues gathered to offer support in this "
        "synthetic scenario created solely for testing purposes.",
        "\"This is placeholder quote text for pipeline testing,\" the "
        "fictional person said, according to this synthetic news article "
        "that does not describe any real events or persons.",
        "The fictional organization declined to comment further on the "
        "invented situation described in this fixture document.",
    ]
    for para in body_paragraphs:
        for line in _wrap(para, 68):
            text_obj.textLine(line)
        text_obj.textLine("")
    c.drawText(text_obj)

    # Right-hand sidebar ("Popular Now" style), starting well right of the
    # main column and containing short, unrelated items.
    sidebar_x = width - 200
    c.setFont("Helvetica-Bold", 10)
    c.drawString(sidebar_x, height - 175, "Popular Now")
    c.setFont("Helvetica", 9)
    sidebar_items = [
        "1  Unrelated headline one about something else entirely",
        "2  Unrelated headline two about a different fake topic",
    ]
    y = height - 195
    for item in sidebar_items:
        for line in _wrap(item, 28):
            c.drawString(sidebar_x, y, line)
            y -= 12
        y -= 6

    c.setFont("Helvetica-Bold", 9)
    c.drawString(sidebar_x, y - 10, "Trending Videos")
    y -= 25
    c.setFont("Helvetica", 8)
    for dur in ["2:57", "1:31"]:
        c.drawString(sidebar_x, y, dur)
        y -= 12
        c.drawString(sidebar_x, y, "Some unrelated video title")
        y -= 16

    c.showPage()
    c.save()


def make_letter(path: str) -> None:
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter

    # Left-hand letterhead sidebar (contact/board info), narrow column.
    c.setFont("Helvetica-Bold", 9)
    c.drawString(50, height - 90, "Board of Directors")
    c.setFont("Helvetica", 8)
    y = height - 105
    for name in ["Jane Doe, PhD (Fake U)", "John Roe, PhD (Fake U)"]:
        c.drawString(50, y, name)
        y -= 12

    # Main letter body, offset to the right of the sidebar column.
    body_x = 220
    c.setFont("Helvetica", 11)
    text_obj = c.beginText(body_x, height - 90)
    text_obj.setLeading(15)
    lines = [
        "1 January 2024",
        "",
        "Jane Recipient, PhD",
        "President, Fake University",
        "",
        "Dear President Recipient:",
        "",
        "This is a synthetic letter body used only to test the pipeline's",
        "ability to separate a letterhead sidebar column from the main",
        "body text of a letter. It does not describe any real events.",
        "",
        "Sincerely,",
        "Jane Doe, PhD",
    ]
    for line in lines:
        text_obj.textLine(line)
    c.drawText(text_obj)

    c.showPage()
    c.save()


def make_report(path: str, num_pages: int = 3) -> None:
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter

    for page_num in range(1, num_pages + 1):
        # Running header
        c.setFont("Helvetica-Bold", 8)
        c.drawString(72, height - 40, "FAKE Report on a Synthetic Topic")
        c.drawRightString(width - 72, height - 40, "January 2024")

        # Body
        c.setFont("Helvetica", 11)
        text_obj = c.beginText(72, height - 90)
        text_obj.setLeading(16)
        paragraphs = [
            f"This is synthetic report body text on page {page_num}, used "
            "only to validate that running headers and footers are "
            "correctly identified as repeated boilerplate and removed, "
            "while this paragraph itself is preserved.",
            f"A second synthetic paragraph specific to page {page_num} "
            "follows, continuing to provide realistic body content for "
            "the reading-order reassembly logic to work with across "
            "several report pages with genuinely distinct wording.",
        ]
        for para in paragraphs:
            for line in _wrap(para, 90):
                text_obj.textLine(line)
            text_obj.textLine("")
        c.drawText(text_obj)

        # Running footer
        c.setFont("Helvetica", 8)
        c.drawString(72, 40, "Fake Association of Testing Teachers")
        c.drawRightString(width - 72, 40, str(page_num))

        c.showPage()

    c.save()


def make_decorative_title(path: str) -> None:
    """
    Reproduces the structural pattern that caused garbled output on real
    cover/title pages: several large, tall, single-word blocks stacked
    down the left margin (simulating a stylized drop-cap-style headline),
    positioned beside normal-sized subtitle/author text that wraps across
    multiple ordinary-height lines. Before the column-aware reassembly
    fix, a tall block's vertical center could land in the middle of the
    shorter blocks next to it, causing words to be sorted out of order.
    """
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter

    # Large stylized headline "letters", each its own tall block, stacked
    # down the left margin -- mimicking a decorative title treatment.
    big_words = ["FAKE", "REPORT", "ON", "A"]
    y = height - 100
    for word in big_words:
        c.setFont("Helvetica-Bold", 36)
        c.drawString(60, y, word)
        y -= 55  # tall block spacing, much larger than normal line height

    # Normal-sized subtitle text, positioned to the right, wrapping across
    # several ordinary-height lines whose combined vertical span overlaps
    # the tall headline blocks above.
    c.setFont("Helvetica", 12)
    subtitle_lines = [
        "Synthetic Topic For",
        "Pipeline Testing Purposes",
        "Only",
    ]
    y2 = height - 110
    for line in subtitle_lines:
        c.drawString(260, y2, line)
        y2 -= 16

    # Author block, further down, also normal-sized.
    c.setFont("Helvetica", 11)
    author_lines = ["Jane Fakeauthor", "University of Testville"]
    y3 = height - 320
    for line in author_lines:
        c.drawString(60, y3, line)
        y3 -= 15

    c.showPage()
    c.save()


def make_two_column_body(path: str) -> None:
    """
    A single page with a genuine two-column body of ordinary-sized text
    and no header/footer/sidebar -- used to confirm the column-aware
    reassembly still produces clean output for a real two-column layout
    (left column read fully top-to-bottom, then right column), rather
    than interleaving the two columns line-by-line.
    """
    c = canvas.Canvas(path, pagesize=letter)
    width, height = letter

    left_paragraph = (
        "This is the left column of a synthetic two column body used to "
        "validate that the pipeline reassembles genuine side by side "
        "columns correctly, reading the entire left column before moving "
        "to the right column, rather than interleaving lines from both "
        "columns together."
    )
    right_paragraph = (
        "This is the right column of the same synthetic two column body, "
        "containing entirely different placeholder content so that any "
        "interleaving with the left column would be immediately obvious "
        "in the reassembled output text."
    )

    c.setFont("Helvetica", 11)
    left_text = c.beginText(72, height - 100)
    left_text.setLeading(15)
    for line in _wrap(left_paragraph, 40):
        left_text.textLine(line)
    c.drawText(left_text)

    right_text = c.beginText(320, height - 100)
    right_text.setLeading(15)
    for line in _wrap(right_paragraph, 40):
        right_text.textLine(line)
    c.drawText(right_text)

    c.showPage()
    c.save()


def _wrap(text: str, width: int):
    words = text.split()
    lines = []
    current = []
    current_len = 0
    for word in words:
        if current_len + len(word) + 1 > width:
            lines.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += len(word) + 1
    if current:
        lines.append(" ".join(current))
    return lines


def main() -> None:
    os.makedirs(FIXTURES_DIR, exist_ok=True)
    make_legal_ruling(os.path.join(FIXTURES_DIR, "legal_ruling.pdf"))
    make_news_article(os.path.join(FIXTURES_DIR, "news_article.pdf"))
    make_letter(os.path.join(FIXTURES_DIR, "letter.pdf"))
    make_report(os.path.join(FIXTURES_DIR, "report.pdf"))
    make_decorative_title(os.path.join(FIXTURES_DIR, "decorative_title.pdf"))
    make_two_column_body(os.path.join(FIXTURES_DIR, "two_column_body.pdf"))
    print(f"Wrote fixtures to {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
