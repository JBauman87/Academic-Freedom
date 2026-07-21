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
   on every page along with its bounding box and page number.
2. **Cross-page boilerplate removal** (`pdf_extract/dedupe.py`) — a block
   that repeats at roughly the same position across most/all pages (a
   running header, footer, page number, or watermark) is stripped. This is
   detected structurally (by what actually repeats in *that* document), not
   by hardcoding any specific court, publisher, or citation format.
3. **Same-page chrome removal** (`pdf_extract/columns.py`) — for
   single/few-page documents (e.g. a printed web article) where nothing
   repeats across pages, navigation bars, sidebars ("Popular Now",
   "Trending Videos"), and generic UI phrases ("Sign In", "Advertisement",
   "Subscribe") are identified by page geometry and a small generic keyword
   list, and stripped. This heuristic is deliberately conservative — it
   only ever removes *short* blocks, so a real paragraph can never be
   deleted just because it mentions one of these words.
4. **Reassembly** (`pdf_extract/reassemble.py`) — surviving blocks are
   joined back into clean paragraphs in reading order. Order is determined
   per page using a simplified recursive "XY-cut": the page is split at any
   clean horizontal gap (nothing straddles it) into stacked bands, and at
   any clean vertical gap into side-by-side columns, recursing until no
   further clean split exists. This correctly handles both a full-width
   header sitting above a multi-column body, and genuine side-by-side
   columns (a two-column article body, a letter's letterhead column next
   to its body) — reading each column fully before moving to the next,
   rather than interleaving them line-by-line. It also avoids a subtler
   failure mode where a single unusually tall block (e.g. a large stylized
   headline on a title page) could have its *vertical center* land in the
   middle of several ordinary-height blocks next to it, causing those
   blocks' text to be sorted out of order; splits and the final fallback
   ordering are based on blocks' top edges, not centers, specifically to
   avoid this.
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

   The detection signal itself was recalibrated after validating against a
   batch of 21 real documents (see "Feedback loop" below): the original
   version used a single whole-page max/min font-size ratio, which
   produced false positives on completely ordinary pages -- any page with
   one section heading above a paragraph and a footnote block naturally
   has a large size ratio (one big heading vs. small footnote text) but is
   not remotely decorative. The signal now specifically counts *multiple*
   blocks that are both large relative to the page's dominant body-text
   size AND short -- which is what an actual fragmented decorative title
   looks like, and what a single normal heading does not.
8. **Text-level artifact cleanup** (`pdf_extract/artifacts.py`) — applied to
   the final reassembled text, after layout-based cleanup:
   - **URLs** (`http://`, `https://`, and bare `www.` links, including ones
     word-wrapped across a line break at a hyphen, e.g.
     `...statement-academic-\nfreedom.`) are removed.
   - **E-mail addresses** are removed.
   - **Decorative separator lines** (rows of repeated dashes/underscores/em
     dashes used as visual dividers, e.g. above a footnote block) are
     removed.

   This exists specifically because the extracted text is meant to feed a
   downstream word-embedding/topic-modeling pipeline (e.g. BERTopic), where
   URLs and e-mail addresses are high-entropy noise tokens that inflate
   vocabulary without contributing any topical signal. Trailing sentence
   punctuation glued to a removed URL (e.g. the closing "." in
   "...malaise/.") is preserved. Each cleanup is independently toggleable
   via CLI flags (`--keep-urls`, `--keep-emails`, `--keep-separator-lines`)
   if you'd rather keep any of these for a different downstream use.

   Also handled: when a URL or e-mail address sits on its own line
   directly after an introducer phrase (common in letterheads/footnotes,
   e.g. `"...Canada; e-mail: \n<address>"` or `"see our website at
   \n<url>"`), removing the link would otherwise leave the introducer
   phrase dangling with nothing after it. A small, deliberately narrow
   list of specific link/contact-info introducers (`e-mail:`, `website
   at`, `facebook:`, `twitter:`, etc.) is cleaned up in that case --
   **only** when a link was actually found and removed on that pass, and
   only for these specific unambiguous phrases. This is intentionally not
   a general "strip trailing short word at end of line" rule, since real
   prose legitimately word-wraps onto short words like "at" or "see" for
   reasons unrelated to any link, and a generic rule would corrupt that
   real content.
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
| `urls_removed` / `emails_removed` / `separator_lines_removed` | counts of each artifact type stripped from this document |

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
    --keep-decorative-pages  `# don't auto-exclude small decorative pages`
```

### Handling exceptional documents

For the documents you mentioned that need only certain portions extracted,
create an overrides file (copy `overrides.example.yaml`) and pass it in:

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

## Feedback loop: reviewing real problem documents with Kiro

Chat attachments have size/count limits, and pasted text loses the
original PDF's layout information that's often needed to actually diagnose
an issue (as happened with the decorative title-page garbling this project
started from). The `samples/` folder in this repository exists to work
around that:

1. Add a small number of problem PDFs to `samples/input/`.
2. Run `python extract.py --input samples/input --output samples/output`.
3. Commit and push the PDF(s), the generated `.txt` / `.excluded.txt`
   files, and `samples/output/report.csv`.
4. Point Kiro at the branch/commit. Kiro can read the input PDF and the
   output `.txt` side by side directly from the repository -- no
   attachment limits, no lossy copy/paste, and the exact PDF that produced
   a given `.txt` is always available for comparison.
5. Kiro diagnoses the issue against the real file, fixes the pipeline
   code, regenerates output, and pushes the fix back for review.

See `samples/README.md` for more detail. This folder is for curated QA
samples only -- keep your actual production batch of several hundred
documents in your own local (gitignored) input/output directories.

As of this writing, `samples/` contains a real 21-document batch (news
articles, letters, and a report, all concerning the same real-world
academic freedom case) that was used to recalibrate the decorative-layout
detection and fix a URL-removal edge case -- both found by comparing real
input PDFs against their real output `.txt` files, which is a much more
reliable calibration signal than synthetic fixtures alone. If you add more
real documents here over time, it's worth periodically re-running the
full batch and skimming `report.csv` for newly-flagged documents.

## Testing

The test suite runs against small synthetic PDF fixtures (generated with
`reportlab`, not real documents) that reproduce the *structural* patterns
this tool targets — a repeated running header/watermark, a printed web
article with a nav bar and sidebar, a letter with a letterhead column, a
multi-page report with a running header/footer, a genuine two-column body
(to check columns are read fully in order, not interleaved), a decorative
title page mixing oversized headline blocks with normal-sized text (to
check it's auto-excluded and preserved in a sidecar file rather than left
garbled), a larger fragmented-but-substantive page (to check the safety
cap prevents auto-exclusion of real content), and text-level checks for
URL/e-mail/separator-line cleanup including a URL wrapped across a line
break.

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
  is a deliberate choice, not an oversight: reliably distinguishing "a
  footnote marker" from "a number that's actually part of the sentence"
  (a year, a section number, a dollar figure) from plain text alone is
  error-prone, and for a topic-modeling pipeline a single stray digit
  fused to a word is far less damaging than a heuristic that
  occasionally mangles real numeric content. If this turns out to matter
  for your BERTopic results in practice, the safest fix is a
  post-processing step scoped to your actual footnote-numbering
  convention (see `overrides.yaml`, or ask for a dedicated
  `footnotes.py` heuristic if you want one tuned to a specific pattern
  you're seeing across the batch).
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
  (`confidence.MAX_CHARS_FOR_AUTO_EXCLUDE`, default 900 characters,
  calibrated against real title/acknowledgements pages -- see "Feedback
  loop" above) is a single global constant. If your batch includes
  unusually dense decorative pages that legitimately exceed this while
  still being pure noise, or conversely if you'd rather be more
  conservative, this is a one-line change -- flag it and we can tune it
  or make it configurable per-run.
