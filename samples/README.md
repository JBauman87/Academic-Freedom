# Sample QA Folder

This folder is the feedback loop for reviewing pipeline output on real
documents without hitting chat attachment limits.

## How to use this folder

1. **Add a problem PDF** to `samples/input/` (small batches at a time --
   e.g. 1-5 documents that show an issue -- keeps diffs reviewable).
2. **Run the pipeline against just this folder**:
   ```bash
   python extract.py --input samples/input --output samples/output
   ```
3. **Commit and push** the PDF, the generated `.txt` (and `.excluded.txt`
   sidecar if one was produced), and `samples/output/report.csv`.
4. Point Kiro at the pushed commit/branch. Since the files are now in the
   repository (not a chat attachment), there's no size/count limit, and
   the exact PDF that produced a given `.txt` is preserved for direct
   comparison -- no more re-describing or re-pasting output by hand.
5. Kiro reads the input PDF and output `.txt` side by side, diagnoses the
   issue, fixes the pipeline code, regenerates the output, and pushes the
   fix back on the same branch (or a new one) for you to review/merge.

## Folder layout

```
samples/
  input/     PDFs used for QA -- representative problem cases, not the
             full production batch. Keep this small and curated.
  output/    Generated .txt / .excluded.txt / report.csv for the PDFs in
             input/. Safe to regenerate and overwrite at any time.
```

## Notes

- This folder is for **QA samples only** -- it is not where you should
  point `--input` for your actual production batch of several hundred
  documents. Keep your real input/output elsewhere (e.g. your own local
  `input_pdfs/` / `output_text/`, which are gitignored) and only copy a
  document here when it needs to be reviewed together.
- Because these files are committed to git, avoid adding documents with
  sensitive/confidential content you wouldn't want in version history.
