#!/usr/bin/env python3
"""
Validate parsing quality for downloaded programme documents.

Place this file here:
  scrapy_crawler/scrapy_crawler/validation/validate_doc_parsing.py

Run from:
  scrapy_crawler/scrapy_crawler

Example:
  python validation\validate_doc_parsing.py ^
    --outputs-root ..\outputs ^
    --manifest ..\outputs\_program_docs_manifest.json ^
    --out validation\metrics\documents_parsing\document_parsing_quality.json

What this measures:
  1. Parser-stage coverage across multiple parsed_fulltext* folders
  2. Unique-doc parsing coverage using doc_key
  3. Metadata-block completeness and consistency
  4. Text/content usability
  5. Optional import/matching skip reports
  6. Optional metadata correction summary

This does NOT judge semantic correctness perfectly. It gives measurable proxy metrics
for a thesis-quality parsing evaluation layer.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


META_RE = re.compile(
    r"---METADATA_JSON---\s*(\{.*?\})\s*---/METADATA_JSON---",
    re.S,
)

DEFAULT_PARSED_DIRS = [
    "parsed_fulltext",
    "parsed_fulltext_docling",
    "parsed_fulltext_docling_new",
    "parsed_fulltext_docling_new_clean",
    "parsed_fulltext_docling_new_clean_language_suffixed",
    "parsed_fulltext_docling_new_metadata_backup",
]

REQUIRED_METADATA_FIELDS = [
    "doc_key",
    "program_key",
    "program_name",
    "degree_level",
    "total_ects",
    "source_url",
    "source_type",
    "local_path",
    "sha256",
    "parser",
    "parsed_at",
]

CORE_METADATA_FIELDS = [
    "doc_key",
    "program_name",
    "degree_level",
    "total_ects",
    "source_url",
    "sha256",
]


# ----------------------------
# Basic IO/helpers
# ----------------------------

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> List[Any]:
    rows = []
    with path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def get_doc_key_from_filename(path: Path) -> Optional[str]:
    stem = path.stem
    if re.fullmatch(r"[0-9a-f]{40}", stem, flags=re.I):
        return stem.lower()

    m = re.search(r"([0-9a-f]{40})", stem, flags=re.I)
    if m:
        return m.group(1).lower()

    return None


def extract_metadata_and_body(path: Path) -> Tuple[Optional[Dict[str, Any]], str, str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        # On Windows, glob() may find files whose full path is too long for normal open().
        # Treat these as unreadable parsing outputs instead of crashing the whole evaluation.
        return {
            "_read_error": True,
            "_read_error_type": type(exc).__name__,
            "_read_error_message": str(exc),
        }, "", ""

    match = META_RE.search(text)

    if not match:
        return None, "", text

    try:
        meta = json.loads(match.group(1))
    except Exception:
        return None, "", text

    body = text[match.end():]
    return meta, match.group(1), body


def count_headings(body: str) -> int:
    return len(re.findall(r"(?m)^#{1,6}\s+\S+", body))


def count_ects_mentions(body: str) -> int:
    return len(re.findall(r"\b\d{1,3}\s*(?:ECTS|credits?|crédits?|Kreditpunkte?)\b", body, flags=re.I))


def text_quality_bucket(char_count: int, word_count: int) -> str:
    if char_count < 200 or word_count < 30:
        return "very_low"
    if char_count < 1000 or word_count < 150:
        return "low"
    if char_count < 5000 or word_count < 700:
        return "medium"
    return "high"


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.mean(values) if values else 0.0


def median(values: Iterable[float]) -> float:
    values = list(values)
    return statistics.median(values) if values else 0.0


# ----------------------------
# Manifest baseline
# ----------------------------

def summarize_manifest_baseline(manifest_path: Path) -> Dict[str, Any]:
    rows = load_json(manifest_path)
    if not isinstance(rows, list):
        raise ValueError(f"Manifest must be a JSON list: {manifest_path}")

    successful_statuses = {"downloaded", "already_present"}
    downloaded = [
        r for r in rows
        if isinstance(r, dict)
        and r.get("status") in successful_statuses
        and present(r.get("doc_key"))
    ]

    all_doc_keys = [str(r.get("doc_key")).lower() for r in downloaded if present(r.get("doc_key"))]
    unique_doc_keys = sorted(set(all_doc_keys))

    refs_by_doc_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in downloaded:
        refs_by_doc_key[str(row.get("doc_key")).lower()].append(row)

    multiplicities = [len(v) for v in refs_by_doc_key.values()]
    reused_docs = {k: v for k, v in refs_by_doc_key.items() if len(v) > 1}

    return {
        "manifest_file": str(manifest_path),
        "downloaded_references": len(downloaded),
        "downloaded_unique_doc_keys": len(unique_doc_keys),
        "unique_doc_keys": unique_doc_keys,
        "reused_doc_keys": len(reused_docs),
        "max_references_per_doc_key": max(multiplicities) if multiplicities else 0,
        "mean_references_per_doc_key": round(mean(multiplicities), 6),
        "metadata_loss_risk_note": (
            "If parsing is deduplicated by doc_key, one parsed text file may represent multiple "
            "programme references. Metadata must therefore be validated against all manifest rows, "
            "not only the first row for a doc_key."
        ),
    }


# ----------------------------
# Parsed directory evaluation
# ----------------------------

def evaluate_parsed_file(path: Path) -> Dict[str, Any]:
    meta, raw_meta, body = extract_metadata_and_body(path)
    filename_doc_key = get_doc_key_from_filename(path)

    char_count = len(body.strip())
    word_count = len(re.findall(r"\w+", body))
    heading_count = count_headings(body)
    ects_mentions = count_ects_mentions(body)

    read_error = bool(meta and meta.get("_read_error"))
    metadata_present = meta is not None and not read_error
    meta = meta or {}

    metadata_completeness = safe_div(
        sum(1 for field in REQUIRED_METADATA_FIELDS if present(meta.get(field))),
        len(REQUIRED_METADATA_FIELDS),
    )

    core_metadata_completeness = safe_div(
        sum(1 for field in CORE_METADATA_FIELDS if present(meta.get(field))),
        len(CORE_METADATA_FIELDS),
    )

    metadata_doc_key = str(meta.get("doc_key") or "").lower()
    doc_key_consistent = (
        bool(filename_doc_key)
        and bool(metadata_doc_key)
        and filename_doc_key == metadata_doc_key
    )

    return {
        "file": path.name,
        "doc_key": metadata_doc_key or filename_doc_key,
        "filename_doc_key": filename_doc_key,
        "metadata_present": metadata_present,
        "doc_key_consistent_with_filename": doc_key_consistent,
        "metadata_completeness": metadata_completeness,
        "core_metadata_completeness": core_metadata_completeness,
        "char_count": char_count,
        "word_count": word_count,
        "heading_count": heading_count,
        "ects_mentions": ects_mentions,
        "text_quality_bucket": text_quality_bucket(char_count, word_count),
        "parser": meta.get("parser"),
        "program_name": meta.get("program_name"),
        "degree_level": meta.get("degree_level"),
        "total_ects": meta.get("total_ects"),
        "source_url": meta.get("source_url"),
        "sha256": meta.get("sha256"),
        "read_error": read_error,
        "read_error_type": meta.get("_read_error_type"),
        "read_error_message": meta.get("_read_error_message"),
        "flags": {
            "degree_level_filename_rule_applied": bool(meta.get("degree_level_filename_rule_applied")),
            "programme_url_manifest_fix_applied": bool(meta.get("programme_url_manifest_fix_applied")),
        },
    }


def evaluate_parsed_dir(parsed_dir: Path, baseline_unique_doc_keys: set[str]) -> Dict[str, Any]:
    files = sorted(parsed_dir.glob("*.txt"))
    rows = [evaluate_parsed_file(path) for path in files]

    parsed_doc_keys = {
        str(row.get("doc_key")).lower()
        for row in rows
        if present(row.get("doc_key"))
    }

    expected = baseline_unique_doc_keys
    missing_expected = sorted(expected - parsed_doc_keys)
    extra_doc_keys = sorted(parsed_doc_keys - expected)

    metadata_present_count = sum(1 for row in rows if row["metadata_present"])
    read_error_count = sum(1 for row in rows if row.get("read_error"))
    doc_key_consistent_count = sum(1 for row in rows if row["doc_key_consistent_with_filename"])

    quality_counts = Counter(row["text_quality_bucket"] for row in rows)
    parser_counts = Counter(str(row.get("parser") or "missing_parser") for row in rows)

    flag_counts = Counter()
    for row in rows:
        for flag_name, value in row["flags"].items():
            if value:
                flag_counts[flag_name] += 1

    duplicate_doc_keys = len(rows) - len(parsed_doc_keys)

    components = {
        "unique_doc_parse_coverage": safe_div(len(parsed_doc_keys & expected), len(expected)),
        "file_to_expected_unique_ratio": safe_div(len(rows), len(expected)),
        "metadata_block_coverage": safe_div(metadata_present_count, len(rows)),
        "doc_key_consistency": safe_div(doc_key_consistent_count, len(rows)),
        "metadata_completeness": mean(row["metadata_completeness"] for row in rows),
        "core_metadata_completeness": mean(row["core_metadata_completeness"] for row in rows),
        "usable_text_rate": safe_div(
            sum(1 for row in rows if row["text_quality_bucket"] in {"medium", "high"}),
            len(rows),
        ),
        "heading_detection_rate": safe_div(sum(1 for row in rows if row["heading_count"] > 0), len(rows)),
        "ects_signal_rate": safe_div(sum(1 for row in rows if row["ects_mentions"] > 0), len(rows)),
        "deduplicated_doc_key_rate": safe_div(len(parsed_doc_keys), len(rows)),
    }

    weights = {
        "unique_doc_parse_coverage": 0.25,
        "metadata_block_coverage": 0.15,
        "doc_key_consistency": 0.10,
        "core_metadata_completeness": 0.15,
        "usable_text_rate": 0.15,
        "heading_detection_rate": 0.10,
        "ects_signal_rate": 0.05,
        "deduplicated_doc_key_rate": 0.05,
    }

    score = sum(weights[key] * components[key] for key in weights)

    return {
        "parsed_dir": str(parsed_dir),
        "files_total": len(rows),
        "parsed_unique_doc_keys": len(parsed_doc_keys),
        "expected_unique_doc_keys": len(expected),
        "missing_expected_doc_keys": len(missing_expected),
        "extra_doc_keys_not_in_manifest": len(extra_doc_keys),
        "duplicate_doc_key_files": duplicate_doc_keys,
        "metadata_present_files": metadata_present_count,
        "read_error_files": read_error_count,
        "doc_key_consistent_files": doc_key_consistent_count,
        "text_quality_buckets": dict(quality_counts),
        "parser_counts": dict(parser_counts),
        "manual_fix_flag_counts": dict(flag_counts),
        "content_statistics": {
            "median_chars": round(median(row["char_count"] for row in rows), 2),
            "mean_chars": round(mean(row["char_count"] for row in rows), 2),
            "median_words": round(median(row["word_count"] for row in rows), 2),
            "mean_words": round(mean(row["word_count"] for row in rows), 2),
            "median_headings": round(median(row["heading_count"] for row in rows), 2),
            "mean_headings": round(mean(row["heading_count"] for row in rows), 2),
            "median_ects_mentions": round(median(row["ects_mentions"] for row in rows), 2),
            "mean_ects_mentions": round(mean(row["ects_mentions"] for row in rows), 2),
        },
        "components": {k: round(v, 6) for k, v in components.items()},
        "weights": weights,
        "score": round(score, 6),
        "examples": {
            "missing_expected_doc_keys_first_20": missing_expected[:20],
            "extra_doc_keys_first_20": extra_doc_keys[:20],
            "very_low_text_files_first_20": [
                row["file"] for row in rows if row["text_quality_bucket"] == "very_low"
            ][:20],
            "metadata_missing_files_first_20": [
                row["file"] for row in rows if not row["metadata_present"] and not row.get("read_error")
            ][:20],
            "read_error_files_first_20": [
                {
                    "file": row["file"],
                    "error": row.get("read_error_message"),
                }
                for row in rows if row.get("read_error")
            ][:20],
        },
    }


# ----------------------------
# Existing report integration
# ----------------------------

def load_optional_report(path: Path) -> Optional[Any]:
    if not path.exists():
        return None
    try:
        if path.suffix.lower() == ".jsonl":
            return load_jsonl(path)
        return load_json(path)
    except Exception as exc:
        return {"error": f"Could not load {path}: {exc}"}


def summarize_skip_report(name: str, data: Any) -> Dict[str, Any]:
    if data is None:
        return {"name": name, "available": False}

    if isinstance(data, list):
        reasons = Counter()
        for row in data:
            if isinstance(row, dict):
                reasons[str(row.get("reason") or row.get("status") or "missing_reason")] += 1
        return {
            "name": name,
            "available": True,
            "rows": len(data),
            "reasons": dict(reasons),
        }

    if isinstance(data, dict):
        summary = {
            "name": name,
            "available": True,
        }

        for key in [
            "total_docs",
            "changed_total",
            "recommended_total",
            "unchanged_total",
            "trusted_unique_download_total",
            "manual_check_total",
        ]:
            if key in data:
                summary[key] = data[key]

        if "by_confidence" in data:
            summary["by_confidence"] = data["by_confidence"]

        if "counts" in data:
            summary["counts"] = data["counts"]

        return summary

    return {"name": name, "available": True, "type": type(data).__name__}


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate parsed document quality.")
    parser.add_argument(
        "--outputs-root",
        type=Path,
        default=Path("../outputs"),
        help="Path to scrapy_crawler/outputs. Default when run from scrapy_crawler/scrapy_crawler: ../outputs",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("../outputs/_program_docs_manifest.json"),
        help="Path to _program_docs_manifest.json.",
    )
    parser.add_argument(
        "--parsed-dirs",
        nargs="*",
        default=DEFAULT_PARSED_DIRS,
        help="Parsed output directories under outputs-root to compare.",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        default=Path("validation/metrics/documents_parsing/document_parsing_quality.json"),
        help="Output JSON path.",
    )
    args = parser.parse_args()

    if not args.manifest.exists():
        raise FileNotFoundError(f"Manifest not found: {args.manifest}")

    baseline = summarize_manifest_baseline(args.manifest)
    expected_doc_keys = set(baseline["unique_doc_keys"])

    parsed_results = []
    missing_parsed_dirs = []

    for dirname in args.parsed_dirs:
        parsed_dir = args.outputs_root / dirname
        if parsed_dir.exists():
            parsed_results.append(evaluate_parsed_dir(parsed_dir, expected_doc_keys))
        else:
            missing_parsed_dirs.append(str(parsed_dir))

    # Optional reports/files you already created in the parsing/import process.
    optional_report_paths = {
        "docling_import_skipped": args.outputs_root / "_docling_import_skipped.json",
        "docs_matched": args.outputs_root / "_docs_matched.json",
        "docs_skipped_low_score": args.outputs_root / "_docs_skipped_low_score.json",
        "docs_skipped_missing_txt": args.outputs_root / "_docs_skipped_missing_txt.json",
        "docs_skipped_not_eligible": args.outputs_root / "_docs_skipped_not_eligible.json",
        "metadata_validation_report": args.outputs_root / "_metadata_validation_report.json",
        "program_metadata_correction_summary": args.outputs_root / "program_metadata_correction_summary.json",
        "program_metadata_correction_proposal": args.outputs_root / "program_metadata_correction_proposal.json",
    }

    optional_reports = {}
    for name, path in optional_report_paths.items():
        optional_reports[name] = summarize_skip_report(name, load_optional_report(path))

    best_by_score = None
    if parsed_results:
        best_by_score = max(parsed_results, key=lambda r: r["score"])

    result = {
        "metric_scope": "document_parsing_quality",
        "description": (
            "Evaluates parser output folders against the unique downloaded document baseline. "
            "It measures parse coverage, metadata presence/completeness, doc_key consistency, "
            "text usability, heading extraction, ECTS signal extraction, and deduplication. "
            "It also summarizes optional import/metadata correction reports when present."
        ),
        "manifest_baseline": {
            k: v for k, v in baseline.items() if k != "unique_doc_keys"
        },
        "parsed_directory_results": parsed_results,
        "best_directory_by_score": {
            "parsed_dir": best_by_score["parsed_dir"],
            "score": best_by_score["score"],
        } if best_by_score else None,
        "optional_reports": optional_reports,
        "missing_parsed_dirs": missing_parsed_dirs,
        "thesis_interpretation": [
            "Parsing should be evaluated on unique doc_key coverage, because the same document may be linked to many programmes.",
            "A lower number of parsed files is not automatically worse if the parser deduplicates correctly by doc_key.",
            "However, deduplication creates metadata-loss risk when one doc_key belongs to multiple programme contexts.",
            "Therefore this script separates content parsing quality from programme-metadata correctness.",
            "Manual metadata correction should be reported as a post-processing step, not hidden inside the parser score.",
        ],
    }

    save_json(args.out, result)

    print("=== DOCUMENT PARSING QUALITY ===")
    print(f"Output: {args.out}")
    print(f"Expected unique downloaded docs: {baseline['downloaded_unique_doc_keys']}")

    for row in parsed_results:
        print(
            f"- {Path(row['parsed_dir']).name}: "
            f"files={row['files_total']}, "
            f"unique_doc_keys={row['parsed_unique_doc_keys']}, "
            f"coverage={row['components']['unique_doc_parse_coverage']}, "
            f"metadata={row['components']['metadata_block_coverage']}, "
            f"usable_text={row['components']['usable_text_rate']}, "
            f"score={row['score']}"
        )

    if best_by_score:
        print(f"\nBest by proxy score: {Path(best_by_score['parsed_dir']).name} ({best_by_score['score']})")


if __name__ == "__main__":
    main()
