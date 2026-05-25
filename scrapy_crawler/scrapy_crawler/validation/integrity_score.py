#!/usr/bin/env python3
"""
Compute a JSON integrity score from a single metrics JSON OR from a whole metrics folder.

Place this file here:
  scrapy_crawler/scrapy_crawler/validation/integrity_score.py

Typical folder-based command:

  python validation\integrity_score.py ^
    --metrics-dir validation\metrics ^
    --out validation\metrics\scores\json_integrity_score.json

The folder mode scans recursively for *.json files and extracts known metrics from:
  - validate_programs_docs/program_validation_metrics.json
  - validate_programs_curricula/program_validation_metrics.json
  - validate_courses/courses_metrics.json

compare_programs is no longer required and is intentionally ignored if old files are still present.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_WEIGHTS = {
    "program_field_completeness": 0.30,
    "academic_core_completeness": 0.20,
    "curriculum_url_coverage": 0.15,
    "document_coverage": 0.25,
    "deduplication": 0.10,
}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def pct_to_rate(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        value = float(value)
        return value / 100.0 if value > 1.0 else value
    if isinstance(value, str):
        value = value.strip().replace("%", "")
        if not value:
            return None
        number = float(value)
        return number / 100.0 if number > 1.0 else number
    return None


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def find_json_files(metrics_dir: Path) -> List[Path]:
    return sorted(metrics_dir.rglob("*.json"))


def classify_metrics_file(path: Path, data: Dict[str, Any]) -> str:
    path_text = str(path).replace("\\", "/").lower()

    # compare_programs was removed from the validation pipeline. Ignore old files if present.
    if "program_comparison_metrics" in path.name.lower() or "compare_programs" in path_text:
        return "ignored_compare_programs"

    if "program_validation_metrics" in path.name.lower() and "validate_programs_docs" in path_text:
        return "program_validation_docs"

    if "program_validation_metrics" in path.name.lower() and "validate_programs_curricula" in path_text:
        return "program_validation_curricula"

    if "courses_metrics" in path.name.lower() or "validate_courses" in path_text:
        return "course_validation"

    return "unknown"


def average_field_completeness(rows: Any) -> Optional[float]:
    if not isinstance(rows, list):
        return None

    values = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = pct_to_rate(row.get("completeness"))
        if value is not None:
            values.append(value)

    return statistics.mean(values) if values else None


def extract_from_program_comparison(data: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}

    coverage = data.get("coverage_metrics", {})
    if isinstance(coverage, dict):
        value = pct_to_rate(coverage.get("programme_import_coverage_percent"))
        if value is not None:
            out["program_coverage"] = value

    adjusted = data.get("ects_metrics_manual_adjusted", {})
    raw = data.get("ects_metrics_raw", {})

    if isinstance(adjusted, dict):
        value = pct_to_rate(
            adjusted.get("manual_adjusted_ects_correct_rate_percent")
            or adjusted.get("ects_correct_after_manual_review_rate_percent")
            or adjusted.get("ects_exact_or_accepted_rate_percent")
        )
        if value is not None:
            out["manual_adjusted_ects_correctness"] = value

    if "manual_adjusted_ects_correctness" not in out and isinstance(raw, dict):
        value = pct_to_rate(raw.get("ects_exact_match_rate_percent"))
        if value is not None:
            out["manual_adjusted_ects_correctness"] = value

    duplicate = data.get("duplicate_metrics", {})
    if isinstance(duplicate, dict):
        # Prefer a direct uniqueness/duplicate-free rate if present.
        direct = None
        for key in [
            "duplicate_free_rate_percent",
            "unique_programme_rate_percent",
            "unique_actual_programmes_rate_percent",
        ]:
            direct = pct_to_rate(duplicate.get(key))
            if direct is not None:
                break

        if direct is not None:
            out["deduplication"] = direct
        else:
            counts = data.get("counts", {})
            duplicate_records = duplicate.get("duplicate_records") or duplicate.get("duplicates")
            total_records = counts.get("actual_programme_records")
            if duplicate_records is not None and total_records:
                out["deduplication"] = 1.0 - (float(duplicate_records) / float(total_records))

    return out


def extract_from_program_validation(data: Dict[str, Any]) -> Dict[str, float]:
    out: Dict[str, float] = {}

    field_avg = average_field_completeness(data.get("field_completeness"))
    if field_avg is not None:
        out["program_field_completeness"] = field_avg

    for metric_name, output_name in [
        ("academic_core_completeness", "academic_core_completeness"),
        ("curriculum_url_coverage", "curriculum_url_coverage"),
    ]:
        section = data.get(metric_name)
        if isinstance(section, dict):
            value = pct_to_rate(section.get("rate"))
            if value is not None:
                out[output_name] = value

    docs = data.get("document_metrics", {})
    if isinstance(docs, dict):
        summary = docs.get("summary", {})
        if isinstance(summary, dict):
            value = pct_to_rate(summary.get("document_coverage_rate"))
            if value is not None:
                out["document_coverage"] = value

            total_links = summary.get("total_document_links")
            duplicate_links = summary.get("duplicate_document_links")
            if total_links:
                out["deduplication"] = 1.0 - (float(duplicate_links or 0) / float(total_links))

    return out


def merge_metric_sources(sources: List[Tuple[str, Path, Dict[str, float]]]) -> Dict[str, Any]:
    """
    Prefer:
      - program_validation_docs over program_validation_curricula for document-related fields

    compare_programs is intentionally excluded from the score.
    """
    priority = {
        "program_validation_docs": 3,
        "program_validation_curricula": 2,
        "course_validation": 1,
        "ignored_compare_programs": -1,
        "unknown": 0,
    }

    selected: Dict[str, Dict[str, Any]] = {}

    for source_type, path, metrics in sources:
        for name, value in metrics.items():
            current = selected.get(name)
            new_priority = priority.get(source_type, 0)

            if current is None or new_priority > current["priority"]:
                selected[name] = {
                    "value": clamp01(float(value)),
                    "source_type": source_type,
                    "source_file": str(path),
                    "priority": new_priority,
                }

    return selected


def collect_metrics_from_folder(metrics_dir: Path) -> Dict[str, Any]:
    sources: List[Tuple[str, Path, Dict[str, float]]] = []

    for path in find_json_files(metrics_dir):
        try:
            data = load_json(path)
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        file_type = classify_metrics_file(path, data)

        extracted: Dict[str, float] = {}

        if file_type in {"program_validation_docs", "program_validation_curricula"}:
            extracted = extract_from_program_validation(data)

        if extracted:
            sources.append((file_type, path, extracted))

    selected = merge_metric_sources(sources)

    return {
        "selected_metrics": selected,
        "scanned_json_files": [str(p) for p in find_json_files(metrics_dir)],
        "used_sources": [
            {"source_type": source_type, "file": str(path), "metrics": metrics}
            for source_type, path, metrics in sources
        ],
    }


def collect_metrics_from_single_file(input_path: Path) -> Dict[str, Any]:
    data = load_json(input_path)

    if not isinstance(data, dict):
        raise ValueError("Input metrics JSON must be an object/dict.")

    # Accept either direct components or known validation files.
    selected: Dict[str, Dict[str, Any]] = {}

    if isinstance(data.get("components"), dict):
        for key, value in data["components"].items():
            rate = pct_to_rate(value)
            if rate is not None:
                selected[key] = {
                    "value": clamp01(rate),
                    "source_type": "components",
                    "source_file": str(input_path),
                    "priority": 1,
                }

    file_type = classify_metrics_file(input_path, data)
    extracted: Dict[str, float] = {}

    if file_type in {"program_validation_docs", "program_validation_curricula"}:
        extracted = extract_from_program_validation(data)

    for key, value in extracted.items():
        selected[key] = {
            "value": clamp01(value),
            "source_type": file_type,
            "source_file": str(input_path),
            "priority": 10,
        }

    # Direct flat metrics fallback.
    for key in DEFAULT_WEIGHTS:
        if key in data:
            rate = pct_to_rate(data[key])
            if rate is not None:
                selected[key] = {
                    "value": clamp01(rate),
                    "source_type": "flat_input",
                    "source_file": str(input_path),
                    "priority": 20,
                }

    return {
        "selected_metrics": selected,
        "scanned_json_files": [str(input_path)],
        "used_sources": [{"source_type": file_type, "file": str(input_path), "metrics": extracted}],
    }


def compute_score(selected_metrics: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    score = 0.0
    used_weight_sum = 0.0
    details: Dict[str, Any] = {}
    missing = []

    for metric_name, weight in DEFAULT_WEIGHTS.items():
        item = selected_metrics.get(metric_name)

        if item is None:
            missing.append(metric_name)
            continue

        value = clamp01(float(item["value"]))
        contribution = value * weight

        details[metric_name] = {
            "value": round(value, 6),
            "weight": weight,
            "contribution": round(contribution, 6),
            "source_type": item.get("source_type"),
            "source_file": item.get("source_file"),
        }

        score += contribution
        used_weight_sum += weight

    renormalized = score / used_weight_sum if used_weight_sum else 0.0

    return {
        "metric_scope": "json_integrity_score",
        "score": round(score, 6),
        "score_renormalized_if_missing_metrics": round(renormalized, 6),
        "used_weight_sum": round(used_weight_sum, 6),
        "missing_metrics": missing,
        "weights": DEFAULT_WEIGHTS,
        "details": details,
        "interpretation_note": (
            "The score is a weighted aggregation over programme field completeness, academic core completeness, "
            "curriculum/document coverage, and deduplication. compare_programs is excluded because that "
            "validation step is no longer part of the pipeline."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute weighted JSON integrity score.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", "-i", type=Path, help="Single input metrics JSON.")
    group.add_argument("--metrics-dir", type=Path, help="Folder containing validation metrics JSON files.")

    parser.add_argument(
        "--out",
        "-o",
        required=True,
        type=Path,
        help="Output JSON path.",
    )

    args = parser.parse_args()

    if args.metrics_dir:
        collected = collect_metrics_from_folder(args.metrics_dir)
    else:
        collected = collect_metrics_from_single_file(args.input)

    result = compute_score(collected["selected_metrics"])
    result["scanned_json_files"] = collected["scanned_json_files"]
    result["used_sources"] = collected["used_sources"]

    save_json(args.out, result)

    print("=== JSON INTEGRITY SCORE ===")
    print(f"Output: {args.out}")
    print(f"Score:  {result['score']}")

    if result["missing_metrics"]:
        print(f"Missing metrics: {', '.join(result['missing_metrics'])}")
        print(f"Renormalized score: {result['score_renormalized_if_missing_metrics']}")

    print("\nUsed metrics:")
    for name, detail in result["details"].items():
        print(f"- {name}: {detail['value']} from {detail['source_type']}")


if __name__ == "__main__":
    main()
