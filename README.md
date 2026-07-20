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
7. **Confidence flagging** (`pdf_extract/confidence.py`) — every document is
   checked for low text yield, unusually high boilerplate removal, failed
   OCR, processing errors, or a page whose block geometry suggests a
   decorative/cover-page layout (large mixes of font sizes, or many very
   short text fragments — typical of title pages, imprint blocks, and
   acknowledgements sections), and flagged with a reason if something looks
   off. This lets you triage a batch of hundreds of documents by looking at
   the flagged ones rather than spot-checking everything. Note that
   decorative front-matter pages (title pages, copyright/imprint blocks,
   acknowledgements) are typically not part of a document's substantive
   content anyway — if you don't want them in the output at all, the
   `heading_range` override (below) is often the cleanest way to skip
   straight to where the real content starts.

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
| `flag_reasons` | why it was flagged (low text yield, high boilerplate removal, OCR needed/failed, heading not found, decorative/cover-page layout, etc.) |
| `ocr_pages_used` | 1-indexed pages that required OCR fallback |

Filter to `flagged == True` (or `success == False`) to get your manual
review queue instead of spot-checking the whole batch.

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

## Testing

The test suite runs against small synthetic PDF fixtures (generated with
`reportlab`, not real documents) that reproduce the *structural* patterns
this tool targets — a repeated running header/watermark, a printed web
article with a nav bar and sidebar, a letter with a letterhead column, a
multi-page report with a running header/footer, a genuine two-column body
(to check columns are read fully in order, not interleaved), and a
decorative title page mixing oversized headline blocks with normal-sized
text (to check reassembly doesn't tear words apart, and that the page gets
flagged for manual review).

```bash
pip install -r requirements.txt
python tests/make_fixtures.py   # regenerate fixtures if needed
python -m pytest tests/ -v
```

## Known limitations

- **Single-page layouts with a non-repeating sidebar** (e.g. a short letter
  with a narrow letterhead column that isn't stripped because it isn't
  short generic UI chrome and doesn't repeat across pages) will have that
  column's text placed as its own reading-order block rather than
  discarded — the column-aware reassembly keeps it intact and separate
  from the main body rather than interleaving it, but it isn't removed.
  Body text is always preserved; the letterhead is just included in the
  output alongside it.
- **OCR quality** depends entirely on `tesseract` and scan quality; this
  path is a fallback, not the primary focus, per the stated assumption
  that documents are exported/printed.
- The chrome keyword list in `pdf_extract/columns.py` is intentionally
  short and generic to avoid overfitting to any one publisher's wording.
  If you process many documents from the same source and notice a
  recurring piece of chrome that isn't being caught, it's better handled
  via an `overrides.yaml` rule (or a small addition to that list) than by
  guessing.
