# Academic Freedom — PDF Text Extraction Pipeline

A batch tool for extracting the substantive body text from exported/printed
PDFs (news articles, letters, reports, legal rulings) while stripping
non-substantive content: repeated headers/footers/watermarks, and
web-page navigation/sidebar/promo chrome.

Built for a batch of several hundred documents where most should be
processed fully automatically, with a small number of genuinely
exceptional documents handled via explicit overrides or flagged for manual
extraction.

## How it works

Rather than treating a PDF as a flat string of text, the pipeline extracts
each page as a set of positioned text blocks (via [PyMuPDF](https://pymupdf.readthedocs.io/))
and uses that position information to distinguish real content from noise:

1. **Layout extraction** (`pdf_extract/layout.py`) — pull every text block
   on every page along with its bounding box and page number. Bounding
   boxes are normalized into the page's displayed orientation, so a page
   embedded at 90/270 degrees (e.g. a landscape letter inside an
   otherwise-portrait PDF) is handled correctly by every later geometry
   heuristic rather than producing out-of-range coordinates. A page is
   marked as having no text layer (and routed to OCR) if it yields fewer
   than a small character threshold.
2. **Cross-page boilerplate removal** (`pdf_extract/dedupe.py`) — a block
   that repeats at roughly the same position across most/all pages (a
   running header, footer, page number, or watermark) is stripped. This is
   detected structurally (by what actually repeats in *that* document), not
   by hardcoding any specific court, publisher, or citation format.
   Position matching tolerates gradual drift across a long document (e.g.
   a centered page number shifting slightly as its digit count grows).
   Documents under 3 pages skip the general repeat-detection (too few
   pages to judge what "repeats"), but a browser "print to PDF" stamp
   (a timestamp/title header and a "Page N of M" + URL footer) is still
   recognized and removed by shape alone, since that pattern is
   distinctive enough to be safe even on a 1-2 page document. Running
   headers/footers that alternate between recto and verso pages (common
   in printed journals) are matched against same-parity pages rather than
   the whole document.
3. **Same-page chrome removal** (`pdf_extract/columns.py`) — for
   single/few-page documents (e.g. a printed web article) where nothing
   repeats across pages, navigation bars, sidebars ("Popular Now",
   "Trending Videos"), comment counts, native-ad labels, newsletter/paywall
   prompts, and generic UI phrases ("Sign In", "Advertisement",
   "Subscribe") are identified by page geometry and a small generic keyword
   list, and stripped. Detection runs in two passes: phrase- and
   geometry-based chrome that can be identified from a block's own text or
   position (nav bars, merged chrome blocks, packed-short-line rows, a
   top-of-first-page masthead strip) is stripped first, so it can't widen
   or otherwise corrupt the main-column estimate used by the remaining
   heuristics. This heuristic is deliberately conservative — it only ever
   removes *short* blocks, so a real paragraph can never be deleted just
   because it mentions one of these words.

   The main-column estimate prefers blocks long enough (≥80 characters) to
   be unambiguously real body text, and falls back to left-margin
   clustering (favoring whichever left edge the most blocks share) when a
   page has too few long blocks — e.g. a sparse headline-and-caption page
   next to a sidebar of short teaser fragments.

   A horizontal nav-row heuristic detects a row of several short blocks
   (e.g. "Home", "News", "Opinion") spanning a wide portion of the page
   width, clustered by overlapping vertical extent so a wrapped two-line
   nav item groups correctly with its single-line neighbors. This
   heuristic requires near-uniform, modest font sizes so it cannot mistake
   a decorative title page's large, non-uniform fragments for a real nav
   bar.

   A second heuristic catches a nav bar/footer/social-icon-strip that a
   PDF's own layout merged into a single multi-line text block, using
   either a fraction of the block's lines matching the generic
   chrome-phrase list, or a purely geometric "packed short lines" signal
   (lines sitting much closer together than genuinely stacked text would)
   for outlet-specific section names that can't be enumerated generically.
   This is restricted to short lines with no digits or table-structure
   characters, so it can't be confused with a report's table cells.

   A "card grid" heuristic detects several narrow, side-by-side blocks
   (e.g. "related articles" teaser cards): each column's stacked lines are
   first grouped into a single whole "card" (category label + headline +
   byline/date), then checked for genuine grid alignment across columns.
   A card is only eligible if it stays short and low — this is what
   distinguishes an actual teaser card from a column of narrow
   newspaper/journal-style body text, which would otherwise satisfy the
   same alignment geometry. A cluster is also capped at a small number of
   items and skipped entirely on pages dominated by narrow blocks, so a
   torn-text-layer defect or heavily fragmented decorative page is never
   mistaken for a card grid.
4. **Reassembly** (`pdf_extract/reassemble.py`) — surviving blocks are
   joined back into clean paragraphs in reading order. Order is determined
   per page using a simplified recursive "XY-cut": the page is split at any
   clean horizontal gap (nothing straddles it) into stacked bands, and at
   any clean vertical gap into side-by-side columns, recursing until no
   further clean split exists. This correctly handles both a full-width
   header sitting above a multi-column body, and genuine side-by-side
   columns (a two-column article body, a letter's letterhead column next
   to its body) — reading each column fully before moving to the next,
   rather than interleaving them line-by-line. Splits and the final
   fallback ordering are based on blocks' top edges, not centers, so a
   single unusually tall block (e.g. a large stylized headline) can't pull
   ordinary-height neighbors out of order.
5. **OCR fallback** (`pdf_extract/ocr.py`) — pages with no extractable text
   layer (i.e. scanned images) are OCR'd if `tesseract` is available;
   otherwise they're flagged for manual review rather than silently
   producing empty output.
6. **Exception overrides** (`pdf_extract/exceptions.py`) — a small YAML
   config lets you tell the pipeline, per-file, to only extract certain
   pages, only extract text between two headings, or skip a file entirely
   for fully manual handling.
7. **Auto-exclusion of unrecoverable decorative pages** (`pdf_extract/confidence.py`,
   wired through `pdf_extract/pipeline.py`) — a page whose surviving blocks
   show the geometric signature of a title page, university crest/seal, or
   imprint block is automatically removed from the main output **if and
   only if** the total text at stake is small (≤ 900 characters by
   default). This is the right behavior for two reasons: such pages are
   rarely part of a document's substantive content anyway, and some
   decorative layouts (e.g. curved/circular text around a seal graphic)
   have no reading order that can be reconstructed at all, so attempting
   to "fix" them just produces confidently-wrong scrambled text -- worse
   for a downstream embedding pipeline than simply not including it.
   Nothing is ever silently lost: excluded pages' original text is written
   to a `<name>.excluded.txt` sidecar file next to the main output, and the
   exclusion is recorded in `report.csv`. A page matching the same
   decorative signature but carrying *more* text than the safety threshold
   is flagged for manual review instead of being excluded, since at that
   point there's real content that shouldn't be dropped without a human
   look. Use `--keep-decorative-pages` to disable auto-exclusion entirely
   (pages will be flagged but left in the main output instead).

   The decorative-layout signal counts *multiple* blocks that are both
   large relative to the page's dominant body-text size AND short —
   which is what an actual fragmented decorative title looks like, as
   opposed to a single normal heading above a paragraph. A second signal
   flags a page where a large majority (≥65%) of blocks are short, even
   if the average block length alone wouldn't trigger — this catches
   website-chrome-heavy pages (e.g. a footer link list) that include one
   or two longer headline/caption blocks pulling the average up.

   A closely related but distinct check, `is_garbled_text_layer_page()`,
   catches a genuine **PDF text-layer defect** rather than a decorative
   layout: a page whose extractor-level line/block clustering only
   recovered the first few characters of each real line, scattering
   truncated fragments into many narrow blocks. Because this can affect
   pages carrying substantial real content, such a page is *never*
   auto-excluded — `is_safe_to_auto_exclude()` unconditionally returns
   `False` for it, and it's always flagged for manual review with a note
   recommending OCR.
8. **Text-level artifact cleanup** (`pdf_extract/artifacts.py`) — applied to
   the final reassembled text, after layout-based cleanup:
   - **URLs** (`http://`, `https://`, and bare `www.` links, including ones
     word-wrapped across a line break at a hyphen) are removed.
   - **E-mail addresses** are removed.
   - **Decorative separator lines** (rows of repeated dashes/underscores/em
     dashes used as visual dividers, e.g. above a footnote block) are
     removed.
   - **Typographic ligatures** (e.g. U+FB01 "ﬁ", U+FB02 "ﬂ") are normalized
     back to their constituent plain letters ("fi", "fl") -- PDF fonts
     commonly encode these letter pairs as a single glyph for kerning
     reasons, which would otherwise make a word like "fired" extract as
     the visually-identical-but-distinct token "ﬁred".
   - **Icon/symbol-font glyphs** (e.g. a social-share icon font's
     Facebook/Twitter/share-arrow icons embedded as ordinary text runs)
     are stripped outright, since they land in Unicode's Private Use Area
     or symbol/dingbat ranges with no real textual equivalent.
   - **Leaked raw HTML tag markup** (e.g. a literal `<a href="...">` anchor
     tag that ended up in the extracted text) is removed, matched
     generically by tag shape rather than as a specific literal string.

   This exists specifically because the extracted text is meant to feed a
   downstream word-embedding/topic-modeling pipeline (e.g. BERTopic), where
   URLs, e-mail addresses, icon glyphs, and HTML markup are high-entropy or
   meaningless noise tokens that inflate vocabulary without contributing
   any topical signal, and ligature codepoints needlessly split what
   should be a single vocabulary token into two visually-identical forms.
   Trailing sentence punctuation glued to a removed URL is preserved. Each
   cleanup is independently toggleable via CLI flags (`--keep-urls`,
   `--keep-emails`, `--keep-separator-lines`, `--keep-ligatures`,
   `--keep-icon-glyphs`, `--keep-html-tags`) if you'd rather keep any of
   these for a different downstream use.

   A link/e-mail address sitting on its own line directly after an
   introducer phrase (e.g. `"...Canada; e-mail: \n<address>"`) has that
   introducer cleaned up too, but only for a small, explicit list of
   unambiguous introducers (`e-mail:`, `website at`, `facebook:`, etc.),
   and only when a link was actually found and removed — a general "strip
   trailing short word" rule would corrupt real prose that legitimately
   wraps onto a short word for unrelated reasons.
9. **Confidence flagging** (`pdf_extract/confidence.py`) — every document is
   checked for low text yield, unusually high boilerplate removal, failed
   OCR, processing errors, or a decorative-layout page that couldn't be
   safely auto-excluded, and flagged with a reason if something looks off.
   This lets you triage a batch of hundreds of documents by looking at the
   flagged ones rather than spot-checking everything.

Every document is processed independently inside its own try/except, so one
corrupt or encrypted PDF can't halt a batch run — it's recorded as a failure
in the report and the rest of the batch continues.

## Setup

```bash
pip install -r requirements.txt
```

### OCR support (optional)

OCR is only used as a fallback for scanned/image-only pages. Per the
project's assumption that source documents are exported/printed (not
scanned), this should rarely trigger — but if you do have scanned
documents in the batch, install the `tesseract` OCR engine:

- macOS: `brew install tesseract`
- Ubuntu/Debian: `sudo apt install tesseract-ocr`
- Windows: see the [tesseract releases page](https://github.com/UB-Mannheim/tesseract/wiki)

If `tesseract` isn't installed, the pipeline still runs fine — affected
pages are simply flagged for manual review instead of being OCR'd.

## Usage

```bash
python extract.py --input ./input_pdfs --output ./output_text
```

This recursively finds every `.pdf` under `./input_pdfs`, writes one
`.txt` file per document to `./output_text`, and writes a `report.csv`
summary (also to `./output_text` by default).

### Checking the results

After a run, open `output_text/report.csv` first. It has one row per
document with:

| column | meaning |
|---|---|
| `success` | `False` if the file errored out entirely (corrupt/encrypted PDF, etc.) |
| `flagged` | `True` if this document should be reviewed manually |
| `flag_reasons` | why it was flagged (low text yield, high boilerplate removal, OCR needed/failed, heading not found, decorative/cover-page layout excluded or needing review, etc.) |
| `ocr_pages_used` | 1-indexed pages that required OCR fallback |
| `auto_excluded_pages` | 1-indexed pages automatically dropped as decorative layout (see sidecar file) |
| `excluded_output_file` | path to the `<name>.excluded.txt` sidecar file, if any pages were auto-excluded |
| `urls_removed` / `emails_removed` / `separator_lines_removed` / `ligatures_normalized` / `icon_glyphs_removed` / `html_tags_removed` | counts of each artifact type stripped from this document |

Filter to `flagged == True` (or `success == False`) to get your manual
review queue instead of spot-checking the whole batch. If `excluded_output_file`
is non-empty, that document had content moved to a sidecar file -- open it
if you want to confirm nothing substantive was dropped.

### CLI flags for artifact cleanup and decorative-page handling

All of the following default to the recommended behavior for feeding a
downstream embedding/topic-modeling pipeline; pass the flag to opt back
into the old/raw behavior for a given run:

```bash
python extract.py --input ./input_pdfs --output ./output_text \
    --keep-urls              `# don't strip http(s)/www links` \
    --keep-emails            `# don't strip e-mail addresses` \
    --keep-separator-lines   `# don't strip dash/underscore divider lines` \
    --keep-ligatures         `# don't normalize ligature characters (e.g. U+FB01 -> "fi")` \
    --keep-icon-glyphs       `# don't strip icon-font/symbol glyphs` \
    --keep-html-tags         `# don't strip leaked raw HTML tag markup` \
    --keep-decorative-pages  `# don't auto-exclude small decorative pages`
```

### Handling exceptional documents

For documents that need only certain portions extracted, or that need to
be excluded from automatic processing entirely, create an overrides file
(copy `overrides.example.yaml`) and pass it in:

```bash
python extract.py --input ./input_pdfs --output ./output_text \
    --overrides overrides.yaml
```

Three override modes are supported per file (matched by exact filename or
glob pattern):

- **`pages`** — only extract specific 1-indexed pages.
- **`heading_range`** — only extract text from a starting heading through
  an (optional) ending heading.
- **`skip`** — don't run automatic extraction at all; the file is recorded
  as flagged/manual-only in the report so you remember to handle it by hand.

Documents not listed in the overrides file run through the fully-automatic
pipeline as normal. See `overrides.example.yaml` for the exact syntax.

## Reviewing problem documents with Kiro

Chat attachments have size/count limits, and pasted text loses the
original PDF's layout information that's often needed to diagnose an
extraction issue. The `samples/` folder exists to work around that: add a
small number of problem PDFs to `samples/input/`, run the pipeline against
just that folder, and commit the PDFs alongside the generated output so
Kiro can read the input and output side by side directly from the
repository. See `samples/README.md` for the full workflow. Keep your
actual production batch of several hundred documents in your own local
(gitignored) input/output directories, not in `samples/`.

## Testing

The test suite (`tests/test_pipeline.py`) runs against small synthetic PDF
fixtures (generated with `reportlab`, not real documents) that reproduce
the *structural* patterns this tool targets — a repeated running
header/watermark, a printed web article with a nav bar and sidebar, a
letter with a letterhead column, a multi-page report with a running
header/footer, a genuine two-column body, a decorative title page mixing
oversized headline blocks with normal-sized text, a larger
fragmented-but-substantive page (checking the safety cap prevents
auto-exclusion of real content), a sparse headline-plus-sidebar page, and
a mostly-chrome nav/footer page.

`tests/test_columns.py` and `tests/test_confidence.py` exercise several
finer-grained heuristics directly against `TextBlock`/`PageLayoutStats`
objects (nav-row font-uniformity, packed-short-lines chrome detection,
card-grid safeguards, and the garbled/truncated text-layer defect
detector), since these check structural/geometric properties that are
easier to construct precisely by hand than to reproduce via a rendered PDF
fixture. `tests/test_artifacts.py` covers ligature normalization,
icon-glyph removal, and HTML tag stripping.

```bash
pip install -r requirements.txt
python tests/make_fixtures.py   # regenerate fixtures if needed
python -m pytest tests/ -v
```

## Known limitations / artifacts not yet auto-cleaned

- **Single-page layouts with a non-repeating sidebar** (e.g. a short letter
  with a narrow letterhead column that isn't stripped because it isn't
  short generic UI chrome and doesn't repeat across pages) will have that
  column's text placed as its own reading-order block rather than
  discarded — the column-aware reassembly keeps it intact and separate
  from the main body rather than interleaving it, but it isn't removed.
  Body text is always preserved; the letterhead is just included in the
  output alongside it.
- **Inline footnote markers glued to words** (e.g. "...academic
  freedom.5" where "5" is a superscript footnote number with no space
  before it in the extracted text) are *not* automatically stripped. This
  is a deliberate choice: reliably distinguishing "a footnote marker"
  from "a number that's actually part of the sentence" (a year, a section
  number, a dollar figure) from plain text alone is error-prone, and for a
  topic-modeling pipeline a single stray digit fused to a word is far less
  damaging than a heuristic that occasionally mangles real numeric
  content. If this matters for your downstream use in practice, the
  safest fix is a post-processing step scoped to your actual
  footnote-numbering convention (see `overrides.yaml`).
- **OCR quality** depends entirely on `tesseract` and scan quality; this
  path is a fallback, not the primary focus, per the stated assumption
  that documents are exported/printed.
- The chrome keyword list in `pdf_extract/columns.py` is intentionally
  short and generic to avoid overfitting to any one publisher's wording.
  If you process many documents from the same source and notice a
  recurring piece of chrome that isn't being caught, it's better handled
  via an `overrides.yaml` rule (or a small addition to that list) than by
  guessing.
- The auto-exclusion safety threshold
  (`confidence.MAX_CHARS_FOR_AUTO_EXCLUDE`, default 900 characters) is a
  single global constant. If your batch includes unusually dense
  decorative pages that legitimately exceed this while still being pure
  noise, or conversely if you'd rather be more conservative, this is a
  one-line change to tune or make configurable per-run.
- A very minor cosmetic residual: when a URL/e-mail was embedded inside a
  parenthetical whose closing paren sits on a following line after the
  link, the closing `")"` can be lost along with the URL, leaving an
  unmatched opening parenthesis earlier in the sentence. This doesn't
  corrupt any real words and is left as-is rather than adding a more
  invasive text-rewriting rule for a purely cosmetic issue.
- A very small number of real documents include Wikipedia-style inline
  citation markers (e.g. `[1]`, `[2]: 45–49`) copied along with quoted
  Wikipedia article text. These are left untouched for the same reason
  footnote markers are (see above) -- a generic bracket-stripping rule
  risks deleting a real editorial insertion like `[sic]` that happens to
  share the same bracket syntax.
