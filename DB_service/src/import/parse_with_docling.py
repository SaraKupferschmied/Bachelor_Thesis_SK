from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from docling.document_converter import DocumentConverter


TABLE_RE = re.compile(r"(?:^|\n)(\|.+\|(?:\n\|[-: ]+\|)?(?:\n\|.*\|)+)", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def split_markdown_pages(md: str) -> list[str]:
    text = md or ""
    pages: list[str] = []

    marker_patterns = [
        re.compile(r"\n(?=\s*<!--\s*page\s*=\s*\d+\s*-->)", re.IGNORECASE),
        re.compile(r"\n(?=\s*#\s*Page\s+\d+\b)", re.IGNORECASE),
        re.compile(r"\n(?=\s*---\s*PAGE\s+\d+\s*---)", re.IGNORECASE),
    ]

    for rx in marker_patterns:
        parts = [p.strip() for p in rx.split(text) if p.strip()]
        if len(parts) > 1:
            return parts

    return [text.strip()] if text.strip() else []


def extract_tables(markdown: str) -> list[str]:
    out: list[str] = []
    for m in TABLE_RE.finditer(markdown or ""):
        block = (m.group(1) or "").strip()
        if block.count("\n") >= 1 and block.count("|") >= 6:
            out.append(block)
    return out


def markdown_table_to_rows(table_md: str) -> list[dict[str, Any]]:
    lines = [ln.strip() for ln in (table_md or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return []

    def parse_row(line: str) -> list[str]:
        s = line.strip().strip("|")
        return [normalize_ws(x) for x in s.split("|")]

    header = parse_row(lines[0])
    sep = parse_row(lines[1])
    if not header or not sep:
        return []

    data_lines = lines[2:] if len(lines) >= 3 else []
    rows: list[dict[str, Any]] = []
    for idx, line in enumerate(data_lines):
        cells = parse_row(line)
        if not any(cells):
            continue
        while len(cells) < len(header):
            cells.append("")
        if len(cells) > len(header):
            cells = cells[: len(header)]
        row_map = {header[i] or f"col_{i+1}": cells[i] for i in range(len(header))}
        rows.append({"row_index": idx, "cells": row_map})
    return rows


def extract_headings(markdown: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for idx, line in enumerate((markdown or "").splitlines()):
        m = HEADING_RE.match(line.strip())
        if not m:
            continue
        headings.append({"level": len(m.group(1)), "text": normalize_ws(m.group(2)), "line_index": idx})
    return headings


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse a PDF with Docling and emit JSON.")
    ap.add_argument("pdf_path")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    pdf_path = Path(args.pdf_path)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    converter = DocumentConverter()
    result = converter.convert(str(pdf_path))
    doc = result.document

    markdown = doc.export_to_markdown()
    pages = split_markdown_pages(markdown)
    tables = extract_tables(markdown)
    heading_blocks = extract_headings(markdown)

    payload = {
        "status": "ok",
        "parser": "docling",
        "title": normalize_ws(getattr(doc, "title", "") or "") or None,
        "markdown": markdown,
        "pages": pages,
        "tables": [
            {
                "table_index": i,
                "markdown": t,
                "rows": markdown_table_to_rows(t),
            }
            for i, t in enumerate(tables)
        ],
        "headings": heading_blocks,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
