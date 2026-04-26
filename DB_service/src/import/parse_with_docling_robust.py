from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


TABLE_RE = re.compile(r"(?:^|\n)(\|.+\|(?:\n\|[-: ]+\|)?(?:\n\|.*\|)+)", re.MULTILINE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def split_markdown_pages(md: str) -> list[str]:
    text = md or ""

    marker_patterns = [
        re.compile(r"\n(?=\s*<!--\s*page\s*=\s*\d+\s*-->)", re.IGNORECASE),
        re.compile(r"\n(?=\s*#\s*Page\s+\d+\b)", re.IGNORECASE),
        re.compile(r"\n(?=\s*---\s*PAGE\s+\d+\s*---)", re.IGNORECASE),
        re.compile(r"\n(?=\s*<PARSED TEXT FOR PAGE:\s*\d+\s*/\s*\d+>)", re.IGNORECASE),
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


def extract_headings_from_markdown(markdown: str) -> list[dict[str, Any]]:
    headings: list[dict[str, Any]] = []
    for idx, line in enumerate((markdown or "").splitlines()):
        m = HEADING_RE.match(line.strip())
        if not m:
            continue
        headings.append(
            {
                "level": len(m.group(1)),
                "text": normalize_ws(m.group(2)),
                "line_index": idx,
                "source": "markdown_regex",
            }
        )
    return headings


def dedupe_dict_items(items: list[dict[str, Any]], key_fields: list[str]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = tuple(item.get(f) for f in key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def try_import_docling() -> tuple[Any, Any]:
    try:
        from docling.document_converter import DocumentConverter  # type: ignore
        return DocumentConverter, None
    except Exception as exc:
        return None, exc


def preflight() -> int:
    DocumentConverter, err = try_import_docling()
    if DocumentConverter is None:
        print(
            json.dumps(
                {
                    "status": "error",
                    "stage": "import",
                    "message": f"Could not import docling: {err}",
                },
                ensure_ascii=False,
            )
        )
        return 2

    try:
        _ = DocumentConverter()
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "stage": "instantiate",
                    "message": f"Could not instantiate DocumentConverter: {exc}",
                },
                ensure_ascii=False,
            )
        )
        return 3

    print(json.dumps({"status": "ok", "message": "docling preflight successful"}, ensure_ascii=False))
    return 0


def safe_get_title(doc: Any, markdown: str) -> str | None:
    for attr in ("title", "name"):
        value = getattr(doc, attr, None)
        if isinstance(value, str) and normalize_ws(value):
            return normalize_ws(value)

    for line in (markdown or "").splitlines():
        line = line.strip()
        if line.startswith("# "):
            title = normalize_ws(line[2:])
            if title:
                return title

    return None


def export_markdown(doc: Any) -> str:
    exporters = ["export_to_markdown", "to_markdown"]
    last_err: Exception | None = None
    for name in exporters:
        fn = getattr(doc, name, None)
        if callable(fn):
            try:
                md = fn()
                if isinstance(md, str):
                    return md
            except Exception as exc:
                last_err = exc
    if last_err:
        raise last_err
    raise RuntimeError("Docling document has no usable markdown exporter")


def extract_docling_headings(doc: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for attr in ("headings", "sections"):
        value = getattr(doc, attr, None)
        if not value:
            continue
        try:
            for idx, item in enumerate(value):
                level = getattr(item, "level", None)
                text = getattr(item, "text", None) or getattr(item, "title", None)
                if text:
                    out.append(
                        {
                            "level": int(level) if isinstance(level, int) else None,
                            "text": normalize_ws(str(text)),
                            "line_index": None,
                            "source": f"docling_{attr}",
                            "item_index": idx,
                        }
                    )
        except Exception:
            continue

    return dedupe_dict_items(out, ["level", "text", "source"])


def extract_docling_tables(doc: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tables = getattr(doc, "tables", None)
    if not tables:
        return out

    for table_index, table in enumerate(tables):
        entry: dict[str, Any] = {"table_index": table_index}
        markdown = None

        for meth_name in ("export_to_markdown", "to_markdown"):
            meth = getattr(table, meth_name, None)
            if callable(meth):
                try:
                    maybe_md = meth()
                    if isinstance(maybe_md, str) and maybe_md.strip():
                        markdown = maybe_md.strip()
                        break
                except Exception:
                    pass

        if markdown:
            entry["markdown"] = markdown
            entry["rows"] = markdown_table_to_rows(markdown)
        else:
            entry["markdown"] = None
            entry["rows"] = []

        out.append(entry)

    return out


def parse_pdf(pdf_path: Path, out_path: Path) -> int:
    DocumentConverter, err = try_import_docling()
    if DocumentConverter is None:
        payload = {
            "status": "error",
            "stage": "import",
            "message": f"Could not import docling: {err}",
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 2

    try:
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        doc = result.document

        markdown = export_markdown(doc)
        pages = split_markdown_pages(markdown)

        native_tables = extract_docling_tables(doc)
        existing_markdowns = {nt.get("markdown") for nt in native_tables if nt.get("markdown")}
        regex_tables = extract_tables(markdown)
        regex_table_payload = [
            {
                "table_index": len(native_tables) + i,
                "markdown": t,
                "rows": markdown_table_to_rows(t),
                "source": "markdown_regex",
            }
            for i, t in enumerate(regex_tables)
            if t not in existing_markdowns
        ]

        for nt in native_tables:
            nt.setdefault("source", "docling_native")

        native_headings = extract_docling_headings(doc)
        markdown_headings = extract_headings_from_markdown(markdown)
        headings = dedupe_dict_items(native_headings + markdown_headings, ["level", "text"])

        payload = {
            "status": "ok",
            "parser": "docling",
            "title": safe_get_title(doc, markdown),
            "markdown": markdown,
            "pages": pages,
            "tables": native_tables + regex_table_payload,
            "headings": headings,
        }

        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0

    except Exception as exc:
        payload = {
            "status": "error",
            "stage": "convert",
            "message": f"{type(exc).__name__}: {exc}",
            "pdf_path": str(pdf_path),
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 1


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse a PDF with Docling and emit JSON.")
    ap.add_argument("pdf_path", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--preflight", action="store_true")
    args = ap.parse_args()

    if args.preflight:
        raise SystemExit(preflight())

    if not args.pdf_path:
        print("missing required argument: pdf_path", file=sys.stderr)
        raise SystemExit(2)
    if not args.out:
        print("missing required argument: --out", file=sys.stderr)
        raise SystemExit(2)

    pdf_path = Path(args.pdf_path)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        payload = {
            "status": "error",
            "stage": "input",
            "message": f"PDF not found: {pdf_path}",
            "pdf_path": str(pdf_path),
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        raise SystemExit(4)

    raise SystemExit(parse_pdf(pdf_path, out_path))


if __name__ == "__main__":
    main()
