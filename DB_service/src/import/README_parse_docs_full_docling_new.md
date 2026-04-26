# parse_docs_full_docling_new

This parser is a safer replacement for the old Docling parser.

## What it fixes

- parses **once per `doc_key`** instead of once per noisy manifest row
- writes into a **new folder**: `scrapy_crawler/outputs/parsed_fulltext_docling_new`
- supports **safe restart / resume**
- keeps a **deduplicated manifest snapshot** and a **persistent `_index.jsonl`**
- uses the exact manifest path when possible and falls back to `outputs/pdfs/<basename>`
- stores the raw Docling JSON in `_raw_docling_json/`
- does a **Docling preflight check once** before parsing

## Files

- `parse_docs_full_docling_new.ts` — main parser
- `parse_with_docling_robust.py` — Python helper for Docling

## Run

From your project root:

```bash
npx tsx parse_docs_full_docling_new.ts \
  --root scrapy_crawler/outputs \
  --manifest scrapy_crawler/outputs/_program_docs_manifest.json \
  --parsed-dir scrapy_crawler/outputs/parsed_fulltext_docling_new \
  --docling-helper parse_with_docling_robust.py \
  --python python
```

If you want to force reparsing existing outputs:

```bash
npx tsx parse_docs_full_docling_new.ts \
  --root scrapy_crawler/outputs \
  --parsed-dir scrapy_crawler/outputs/parsed_fulltext_docling_new \
  --docling-helper parse_with_docling_robust.py \
  --python python \
  --reparse-existing
```

## Outputs

Inside `parsed_fulltext_docling_new/`:

- `<doc_key>.txt` — final parsed text
- `_raw_docling_json/<doc_key>.json` — raw helper output
- `_index.jsonl` — persistent state/index
- `_manifest_dedup.json` — one entry per `doc_key`
- `_run_summary.json` — latest run stats

## Notes

- Install Docling in the Python environment passed via `--python`.
- The parser intentionally keeps the old metadata ambiguity as candidate arrays in the header:
  - `candidate_program_keys`
  - `candidate_total_ects`
- That means parsing and metadata correction stay separated.
