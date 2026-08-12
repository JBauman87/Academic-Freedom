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

   The main-column/sidebar geometry estimate was refined after finding a
   real failure case via the feedback loop (see below): on a *sparse*
   page with no unambiguously long body paragraph (e.g. just a short
   headline and caption), ranking blocks purely by character length let
   short sidebar teaser fragments corrupt the estimate, silently
   disabling sidebar removal on that page. The estimator now requires
   blocks to be genuinely long (≥80 characters) before trusting them as
   body-text evidence, and falls back to a left-margin-clustering
   heuristic — preferring the leftmost multi-block cluster, matching how
   a primary reading column is conventionally positioned versus a
   sidebar/rail to its right — when a page has too few long blocks.

   A separate horizontal nav-row heuristic also detects a row of several
   short blocks (e.g. "Home", "News", "Opinion") spanning a wide portion
   of the page width, regardless of the specific section names used (a
   publication's own labels won't be in the generic phrase list). Blocks
   are clustered by overlapping vertical extent (so a two-line wrapped
   nav item like "Arts &\nCulture" is grouped correctly with its
   single-line neighbors) rather than a fixed position tolerance. This
   heuristic additionally requires the candidate row's font sizes to be
   near-uniform and modest (≤16pt, ≤1.3× ratio between largest/smallest)
   -- found necessary after a real CAUT report's decorative title-page
   fragments (font sizes ranging ~27-46pt, many short pieces spanning a
   wide portion of the page) were being consumed by this heuristic before
   the decorative-page detector (step 7 below) ever got to see them,
   hiding the whole page from that detector and leaving it garbled in
   the output instead of cleanly auto-excluded.

   A third heuristic catches a nav bar/footer/social-icon-strip that a
   PDF's own layout merged into a *single* multi-line text block (rather
   than one block per item) -- common on real news-site exports, e.g. an
   entire "Local / Watch / Trade War / ... / Sign In" nav bar as one
   block. Two complementary signals are used: (a) a fraction of the
   block's individual lines matching the generic chrome-phrase list, for
   cases where at least some items are in that list; and (b) a purely
   geometric "packed short lines" signal for cases where none of the
   items are (a publication's own section names, which can't be
   enumerated generically) -- comparing the block's actual height against
   what that many lines would occupy if they were genuinely stacked
   paragraph text at that font size. Side-by-side nav items packed onto
   what PyMuPDF reports as several separate "lines" occupy far less
   vertical space than the same number of genuinely stacked lines would,
   so a low height-per-line-vs-font-size ratio (< 0.7) is a reliable,
   wording-independent tell. This is additionally restricted to short
   lines (≤4 words each) with no digits or table-structure characters
   (`/`, `#`), so it can't be confused with a report's short table cells
   (e.g. a "Date / Item # / Event" row), which are also tightly packed
   but carry real tabular content.

   A fourth heuristic detects a "card grid" -- several narrow blocks
   (e.g. "related articles" teaser cards) sitting side by side, each
   internally containing a short multi-line teaser. Detected via
   union-find clustering on blocks that substantially overlap each
   other's vertical extent but don't overlap horizontally. This
   heuristic needed several additional safeguards after a real CAUT
   report's *own* torn-multi-column appendix letters and heavily
   fragmented decorative pages turned out to satisfy the same geometry
   test purely by coincidence of how many narrow columns happened to
   line up:
   - Blocks matching the garbled-text-layer signature (see step 7 below)
     are never eligible, so this heuristic can't hide that defect from
     its dedicated detector.
   - A cluster may contain at most 8 items -- no genuine card-grid row
     observed in a real corpus exceeded 7, while the report's
     false-positive clusters ran into the dozens or hundreds.
   - Card-grid detection is skipped entirely on any page where narrow
     blocks make up more than 80% of that page's blocks -- a real
     card-grid page is still a normal page of mostly-normal-width
     content with the teaser row as one small feature (≤77% narrow in
     every genuine example found), while a torn-text or decorative page
     is overwhelmingly narrow blocks (92-100% in every false-positive
     found).
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

   The detection signal itself was recalibrated twice after validating
   against real documents via the feedback loop (see below):
   - The original version used a single whole-page max/min font-size
     ratio, which produced false positives on completely ordinary pages
     -- any page with one section heading above a paragraph and a
     footnote block naturally has a large size ratio (one big heading vs.
     small footnote text) but is not remotely decorative. The signal now
     specifically counts *multiple* blocks that are both large relative
     to the page's dominant body-text size AND short -- which is what an
     actual fragmented decorative title looks like, and what a single
     normal heading does not.
   - A page made up mostly of website chrome (e.g. a "Related Bulletins"
     teaser list, a footer link list) can include one or two longer
     headline/caption blocks that pull the page's *average* block length
     just above the fragmentation threshold, even though most of the
     page's blocks are still short chrome fragments. A second signal now
     also flags a page where a large majority (≥65%) of blocks are short,
     even if the average alone wouldn't trigger. This threshold was
     chosen by comparing real chrome-heavy pages (which had ≥69% short
     blocks) against every genuine content page in a real 43-document
     batch, including dense historical reports with many short
     footnote-style blocks (all of which stayed under 50%).

   A closely related but distinct check catches a genuine **PDF text-layer
   defect** rather than a page layout: on a real CAUT report, several
   pages -- visually completely normal, clean prose when rendered to an
   image -- had a text layer where the extractor's own line/block
   clustering only recovered the first few characters of each real line
   (e.g. "You conclude your letter..." extracting as just "You"),
   scattering the truncated fragments into many narrow vertical-strip
   blocks. Crucially, this affected pages carrying substantial real
   content (e.g. a full appendix letter), so it must **never** be treated
   as a decorative page safe to auto-exclude the way a title page is --
   doing so would silently discard real substance. `is_garbled_text_layer_page()`
   detects this signature (≥2 blocks per page with ≥3 lines each,
   averaging ≤6 characters per line, at a modest font size ≤13pt to avoid
   colliding with the decorative-fragment signature above) and
   `is_safe_to_auto_exclude()` unconditionally returns `False` for any
   page matching it -- such a page is always flagged for manual review
   with a note explaining the defect is in the source PDF's own encoding
   and recommending OCR, never silently excluded or left in a garbled
   state with no explanation.
8. **Text-level artifact cleanup** (`pdf_extract/artifacts.py`) — applied to
   the final reassembled text, after layout-based cleanup:
   - **URLs** (`http://`, `https://`, and bare `www.` links, including ones
     word-wrapped across a line break at a hyphen, e.g.
     `...statement-academic-\nfreedom.`) are removed.
   - **E-mail addresses** are removed.
   - **Decorative separator lines** (rows of repeated dashes/underscores/em
     dashes used as visual dividers, e.g. above a footnote block) are
     removed.
   - **Typographic ligatures** (e.g. U+FB01 "ﬁ", U+FB02 "ﬂ") are normalized
     back to their constituent plain letters ("fi", "fl") -- PDF fonts
     commonly encode these letter pairs as a single glyph for kerning
     reasons, which would otherwise make a word like "fired" extract as
     the visually-identical-but-distinct token "ﬁred", splitting it from
     "fired" in a downstream vocabulary.
   - **Icon/symbol-font glyphs** (e.g. a social-share icon font's
     Facebook/Twitter/share-arrow icons, embedded as ordinary text runs in
     some web-article PDF exports) are stripped outright. These land in
     Unicode's Private Use Area or symbol/dingbat ranges and have no real
     textual equivalent -- they're meaningless noise once separated from
     the specific icon font that gave them a visual glyph.
   - **Leaked raw HTML tag markup** (e.g. a literal `<a href="...">` anchor
     tag that ended up in the extracted text instead of just its rendered
     link text -- observed in one real web-article export, likely from a
     copy-paste-from-browser or incomplete PDF-export step) is removed.
     Matched generically by tag shape rather than as a specific literal
     string, so it generalizes to other leaked tags, and tolerates the
     mismatched/unescaped quote characters seen in the real observed case.

   This exists specifically because the extracted text is meant to feed a
   downstream word-embedding/topic-modeling pipeline (e.g. BERTopic), where
   URLs, e-mail addresses, icon glyphs, and HTML markup are high-entropy or
   meaningless noise tokens that inflate vocabulary without contributing
   any topical signal, and ligature codepoints needlessly split what
   should be a single vocabulary token into two visually-identical forms.
   Trailing sentence punctuation glued to a removed URL (e.g. the closing
   "." in "...malaise/.") is preserved. Each cleanup is independently
   toggleable via CLI flags (`--keep-urls`, `--keep-emails`,
   `--keep-separator-lines`, `--keep-ligatures`, `--keep-icon-glyphs`,
   `--keep-html-tags`) if you'd rather keep any of these for a different
   downstream use.

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

This loop has been run three times so far, each time against a larger and
more varied real batch (most recently 21 documents spanning news articles,
letters, NGO correspondence, CAUT investigative reports, and a full
100+ page CAUT investigatory report). Each round found and fixed real
issues that synthetic fixtures alone hadn't surfaced:

- **Round 1/2**: a website-chrome page (nav bar + footer + newsletter
  signup) that leaked almost entirely into main output uncaught, and a
  sparse news page whose real headline/caption was too short to reliably
  distinguish from a sidebar teaser list under the original
  column-estimation logic.
- **Round 3**: a decorative title page's fragments being consumed by the
  nav-row heuristic before the decorative-page detector could see them
  (fixed via a font-size uniformity/size constraint on that heuristic); a
  genuine PDF text-layer defect on several pages of a real report,
  scattering truncated line fragments into narrow blocks -- serious
  because, unlike a decorative page, those pages carried substantial real
  content and could never be safely auto-excluded (fixed via a dedicated
  `is_garbled_text_layer_page()` detector, always flagged-only, never
  auto-excludable); ligature characters (e.g. "ﬁ") and icon-font glyphs
  splitting/polluting a downstream vocabulary; a nav bar/social-icon-strip
  merged into a single PDF text block by several different outlets'
  exports, whose individual items didn't match the generic chrome-phrase
  list (fixed via a wording-independent "packed short lines" geometric
  signal); a "card grid" teaser-row detector that, after being added,
  turned out to also match a real report's own torn-multi-column
  appendix letters and fragmented decorative pages purely by coincidence
  of geometry (fixed via a cluster-size cap and a page-level
  narrow-block-fraction gate); and a leaked raw HTML anchor tag in one
  real article's extracted text.

All of the above are now covered by dedicated regression tests (see
"Testing" below) so they can't silently regress.

Per-repository convention: after each review round, `samples/input/` and
`samples/output/` are cleared back to empty (aside from `.gitkeep`) before
pushing, so the repository doesn't accumulate every batch of test
documents indefinitely. The lessons learned are captured in the code
itself (heuristics, thresholds, comments) and in this README, not by
keeping the documents around. If you want to re-run a similar review
later, add a fresh batch of PDFs to `samples/input/` following the same
workflow.

## Testing

The test suite (`tests/test_pipeline.py`) runs against small synthetic PDF
fixtures (generated with `reportlab`, not real documents) that reproduce
the *structural* patterns this tool targets — a repeated running
header/watermark, a printed web article with a nav bar and sidebar, a
letter with a letterhead column, a multi-page report with a running
header/footer, a genuine two-column body (to check columns are read fully
in order, not interleaved), a decorative title page mixing oversized
headline blocks with normal-sized text (to check it's auto-excluded and
preserved in a sidecar file rather than left garbled), a larger
fragmented-but-substantive page (to check the safety cap prevents
auto-exclusion of real content), a sparse headline-plus-sidebar page and a
mostly-chrome nav/footer page (both reproducing real bugs found via the
feedback loop -- see above).

Three additional test files exercise the finer-grained heuristics added
during the third feedback round directly against `TextBlock`/
`PageLayoutStats` objects, since these check purely structural/geometric
properties that are easier to construct precisely by hand than to
reproduce faithfully via a rendered PDF fixture:

- `tests/test_columns.py` — the nav-row font-uniformity guard (a
  decorative title's fragments must not be mistaken for a nav row), the
  packed-short-lines merged-chrome-block signal (a merged nav bar must be
  caught; a genuine paragraph, a decorative fragment, and a table cell
  with digits must not be), and the card-grid safeguards (garbled-text
  blocks must be excluded from eligibility; a page dominated by narrow
  blocks must skip card-grid detection entirely; an oversized cluster
  must be rejected; a genuine card grid must still be detected).
- `tests/test_confidence.py` — the garbled/truncated PDF text-layer
  defect detector (a real truncated-line block must be flagged; a normal
  paragraph and a large-font decorative fragment must not be; a garbled
  page must never be considered safe to auto-exclude, even if it also
  matches the decorative-layout signature and is small enough).
- `tests/test_artifacts.py` (extended) — ligature normalization,
  icon-glyph removal, and HTML tag stripping (including the malformed/
  mismatched-quote variant seen in the real observed case, and a check
  that a real "<" comparison operator in body text is never mistaken for
  a tag).

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
- A very minor cosmetic residual: when a URL/e-mail was embedded inside a
  parenthetical whose closing paren sits on a following line after the
  link (e.g. `"...see our website at \nwww.example.com.) \n..."`), the
  closing `")"` can be lost along with the URL, leaving `"...see our \n..."`
  with an unmatched opening parenthesis earlier in the sentence. This
  doesn't corrupt any real words (a stray punctuation mark is inert for a
  topic-modeling pipeline) and is left as-is rather than adding a more
  invasive text-rewriting rule for a purely cosmetic issue.
- A very small number of real documents include Wikipedia-style inline
  citation markers (e.g. `[1]`, `[2]: 45–49`) copied along with quoted
  Wikipedia article text. These are left untouched for the same reason
  footnote markers are (see above) -- a generic bracket-stripping rule
  risks deleting a real editorial insertion like `[sic]` that happens to
  share the same bracket syntax.
