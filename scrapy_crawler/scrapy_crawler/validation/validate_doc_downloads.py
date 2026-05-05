#!/usr/bin/env python3
"""
Validate programme-document acquisition quality.

This script does TWO things:

1. Stage/evolution evaluation
   Captures how document quality changed across your pipeline stages:
   - initial curriculum/programme output
   - merged study-plan documents
   - enriched unmatched documents
   - optional faculty-normalized source
   - final download manifest

2. Final download quality
   Computes the final document-download quality score from _program_docs_manifest.json.

Place this file here:
  scrapy_crawler/scrapy_crawler/validation/validate_doc_downloads.py

Typical command from scrapy_crawler/scrapy_crawler:

  python validation\validate_doc_downloads.py ^
    --spider-outputs spider_outputs ^
    --manifest ..\outputs\_program_docs_manifest.json ^
    --out validation\metrics\documents_downloads\document_download_quality.json
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ----------------------------
# IO
# ----------------------------

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ----------------------------
# Helpers
# ----------------------------

def present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def first_present(record: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if present(value):
            return value
    return None


def get_program_name(record: Dict[str, Any]) -> str:
    value = first_present(
        record,
        [
            "program_name",
            "programme_name_en",
            "programme",
            "title",
            "name",
            "program_clean",
            "program_base_clean",
            "program_short_clean",
        ],
    )

    if isinstance(value, dict):
        value = first_present(value, ["name_en", "name_de", "name_fr", "name"])

    return norm(value)


def get_program_key(record: Dict[str, Any]) -> Tuple[str, str, str, str]:
    faculty = first_present(record, ["faculty", "faculty_canonical"]) or ""
    level = first_present(record, ["degree_level", "level", "category", "programme_level"]) or ""
    ects = first_present(record, ["total_ects", "ects_points", "ects", "credit_points"]) or ""
    name = get_program_name(record)
    return (norm(faculty), norm(level), str(ects).strip(), name)


def iter_doc_urls(record: Dict[str, Any]) -> List[str]:
    urls: List[str] = []

    docs = record.get("documents")
    if isinstance(docs, list):
        for doc in docs:
            if isinstance(doc, dict):
                url = doc.get("url") or doc.get("source_url")
                if present(url):
                    urls.append(str(url).strip())
            elif isinstance(doc, str) and doc.strip():
                urls.append(doc.strip())

    for key in [
        "doc_urls",
        "file_urls",
        "curriculum_url",
        "curriculum_de_url",
        "curriculum_fr_url",
        "curriculum_en_url",
        "curriculum_unspecified_url",
    ]:
        value = record.get(key)

        if isinstance(value, list):
            urls.extend(str(v).strip() for v in value if present(v))
        elif isinstance(value, str) and value.strip():
            urls.append(value.strip())

    # Dedupe within one record.
    out = []
    seen = set()
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)

    return out


def has_curriculum_url(record: Dict[str, Any]) -> bool:
    return any(
        present(record.get(key))
        for key in [
            "curriculum_url",
            "curriculum_de_url",
            "curriculum_fr_url",
            "curriculum_en_url",
            "curriculum_unspecified_url",
        ]
    )


# ----------------------------
# Stage metrics
# ----------------------------

def summarize_program_stage(path: Path, label: str) -> Dict[str, Any]:
    data = load_json(path)

    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list, got {type(data).__name__}")

    records = [r for r in data if isinstance(r, dict)]
    total = len(records)

    doc_urls_by_program: Dict[Tuple[str, str, str, str], List[str]] = defaultdict(list)
    all_doc_urls: List[str] = []

    with_docs = 0
    with_curriculum_url = 0

    for record in records:
        pkey = get_program_key(record)

        urls = iter_doc_urls(record)
        if urls:
            with_docs += 1
            doc_urls_by_program[pkey].extend(urls)
            all_doc_urls.extend(urls)

        if has_curriculum_url(record):
            with_curriculum_url += 1

    unique_programs = len(set(get_program_key(r) for r in records))
    duplicate_program_records = total - unique_programs

    unique_doc_urls = len(set(all_doc_urls))
    duplicate_doc_refs = len(all_doc_urls) - unique_doc_urls

    url_to_programs: Dict[str, set] = defaultdict(set)
    for program_key, urls in doc_urls_by_program.items():
        for url in urls:
            url_to_programs[url].add(program_key)

    shared_docs = {url: programs for url, programs in url_to_programs.items() if len(programs) > 1}
    suspicious_shared_docs = {
        url: programs for url, programs in shared_docs.items() if len(programs) > 3
    }

    match_type_counts = Counter(
        str(r.get("match_type") or "missing_match_type")
        for r in records
    )

    matched = sum(
        count
        for key, count in match_type_counts.items()
        if key in {"matched", "matched_by_doc", "matched_by_id", "fuzzy", "exact"}
    )
    unmatched = match_type_counts.get("unmatched", 0)

    return {
        "stage": label,
        "file": str(path),
        "records_total": total,
        "unique_program_keys": unique_programs,
        "duplicate_program_records": duplicate_program_records,
        "program_duplicate_rate": round(safe_div(duplicate_program_records, total), 6),
        "records_with_curriculum_url": with_curriculum_url,
        "curriculum_url_coverage": round(safe_div(with_curriculum_url, total), 6),
        "records_with_documents_or_curriculum_links": with_docs,
        "document_coverage": round(safe_div(with_docs, total), 6),
        "total_document_references": len(all_doc_urls),
        "unique_document_urls": unique_doc_urls,
        "duplicate_document_references": duplicate_doc_refs,
        "document_reference_duplication_rate": round(safe_div(duplicate_doc_refs, len(all_doc_urls)), 6),
        "shared_document_urls": len(shared_docs),
        "suspicious_shared_document_urls_more_than_3_programs": len(suspicious_shared_docs),
        "average_document_refs_per_record": round(safe_div(len(all_doc_urls), total), 6),
        "match_type_counts": dict(match_type_counts),
        "matched_records": matched,
        "unmatched_records": unmatched,
        "match_success_rate": round(safe_div(matched, matched + unmatched), 6) if matched + unmatched else None,
    }


def add_stage_deltas(stages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    numeric_keys = [
        "curriculum_url_coverage",
        "document_coverage",
        "total_document_references",
        "unique_document_urls",
        "duplicate_document_references",
        "document_reference_duplication_rate",
        "shared_document_urls",
        "suspicious_shared_document_urls_more_than_3_programs",
        "average_document_refs_per_record",
        "match_success_rate",
    ]

    out = []

    previous: Optional[Dict[str, Any]] = None
    for stage in stages:
        stage = dict(stage)

        if previous is None:
            stage["delta_from_previous_stage"] = None
        else:
            delta = {}
            for key in numeric_keys:
                current = stage.get(key)
                old = previous.get(key)
                if isinstance(current, (int, float)) and isinstance(old, (int, float)):
                    delta[key] = round(current - old, 6)
            stage["delta_from_previous_stage"] = delta

        out.append(stage)
        previous = stage

    return out


# ----------------------------
# Manifest metrics
# ----------------------------

def summarize_manifest(path: Path) -> Dict[str, Any]:
    data = load_json(path)

    if not isinstance(data, list):
        raise ValueError(f"Manifest must be a JSON list, got {type(data).__name__}")

    total = len(data)
    status = Counter(str(r.get("status") or "missing_status") for r in data if isinstance(r, dict))
    source_type = Counter(str(r.get("source_type") or "missing_source_type") for r in data if isinstance(r, dict))

    successful_statuses = {"downloaded", "already_present"}
    attempted_source_types = {"pdf", "calameo"}

    valid_downloads = sum(
        1
        for r in data
        if isinstance(r, dict)
        and r.get("status") in successful_statuses
        and present(r.get("local_path"))
        and present(r.get("sha256"))
    )

    attempted = sum(
        1
        for r in data
        if isinstance(r, dict) and r.get("source_type") in attempted_source_types
    )

    skipped = status.get("skipped_non_pdf", 0)
    failed = (
        status.get("failed", 0)
        + status.get("calameo_no_direct_pdf", 0)
        + status.get("download_failed", 0)
    )

    by_program: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in data:
        if not isinstance(r, dict):
            continue
        key = str(r.get("program_key") or "missing_program_key")
        by_program[key].append(r)

    programs = len(by_program)
    programs_with_valid = sum(
        1
        for records in by_program.values()
        if any(r.get("status") in successful_statuses for r in records)
    )

    doc_keys = [r.get("doc_key") for r in data if isinstance(r, dict) and present(r.get("doc_key"))]
    unique_docs = len(set(doc_keys))
    duplicate_doc_refs = total - unique_docs if total else 0

    metadata_fields = [
        "program_key",
        "doc_key",
        "program_name",
        "source_url",
        "doc_label",
        "fetched_at",
        "status",
    ]

    metadata_completeness = (
        statistics.mean(
            [
                safe_div(
                    sum(1 for r in data if isinstance(r, dict) and present(r.get(field))),
                    total,
                )
                for field in metadata_fields
            ]
        )
        if total
        else 0.0
    )

    components = {
        "download_success_all_refs": safe_div(valid_downloads, total),
        "download_success_attempted_pdf_or_calameo": safe_div(valid_downloads, attempted),
        "program_doc_availability": safe_div(programs_with_valid, programs),
        "actionable_source_type": safe_div(attempted, total),
        "deduplication_by_doc_key": safe_div(unique_docs, total),
        "metadata_completeness": metadata_completeness,
    }

    weights = {
        "download_success_all_refs": 0.30,
        "program_doc_availability": 0.25,
        "actionable_source_type": 0.15,
        "deduplication_by_doc_key": 0.10,
        "metadata_completeness": 0.20,
    }

    score = sum(weights[key] * components[key] for key in weights)

    return {
        "file": str(path),
        "metric_scope": "final_document_download_quality",
        "description": (
            "Measures final document reference actionability, download success, programme-level "
            "document availability, deduplication, and manifest metadata completeness. "
            "It does not measure PDF parsing correctness."
        ),
        "total_refs": total,
        "programs": programs,
        "status": dict(status),
        "source_type": dict(source_type),
        "valid_downloads": valid_downloads,
        "attempted_pdf_or_calameo": attempted,
        "skipped_non_pdf": skipped,
        "failed": failed,
        "programs_with_valid_download": programs_with_valid,
        "unique_docs": unique_docs,
        "duplicate_doc_refs": duplicate_doc_refs,
        "components": {k: round(v, 6) for k, v in components.items()},
        "weights": weights,
        "score": round(score, 6),
    }


# ----------------------------
# Main
# ----------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute document-download quality and stage-by-stage document acquisition improvement."
    )

    parser.add_argument(
        "--spider-outputs",
        type=Path,
        default=Path("spider_outputs"),
        help="Path to spider_outputs folder. Default: spider_outputs",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("../outputs/_program_docs_manifest.json"),
        help="Path to _program_docs_manifest.json.",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        default=Path("validation/metrics/documents_downloads/document_download_quality.json"),
        help="Output JSON path.",
    )

    args = parser.parse_args()

    stage_candidates = [
        ("01_curricula_links_enriched", args.spider_outputs / "programmes_with_curricula_enriched.json"),
        ("02_program_links_with_ects", args.spider_outputs / "program_links_with_ects.json"),
        ("03_merged_with_faculty_docs", args.spider_outputs / "program_links_with_ects_and_docs.json"),
        ("04_enriched_unmatched_docs", args.spider_outputs / "program_links_with_ects_and_docs_enriched.json"),
        ("source_faculty_programs_normalized", args.spider_outputs / "faculty_programs_normalized.json"),
    ]

    stages = []
    missing_stage_files = []

    for label, path in stage_candidates:
        if path.exists():
            stages.append(summarize_program_stage(path, label))
        else:
            missing_stage_files.append(str(path))

    stages = add_stage_deltas(stages)

    manifest_summary = None
    if args.manifest.exists():
        manifest_summary = summarize_manifest(args.manifest)

    result = {
        "metric_scope": "document_acquisition_and_download_quality",
        "stage_evolution": stages,
        "final_download_quality": manifest_summary,
        "missing_stage_files": missing_stage_files,
        "notes": [
            "Stage evolution captures improvements from curriculum links to merged/enriched document references.",
            "Final download quality captures whether the resulting references were actually downloadable.",
            "High duplicate document references can be expected when one study plan is valid for multiple programme variants, but very high sharing is useful as a manual-review signal.",
            "PDF parsing correctness should be evaluated separately in the next validation layer.",
        ],
    }

    save_json(args.out, result)

    print("=== DOCUMENT ACQUISITION + DOWNLOAD QUALITY ===")
    print(f"Output: {args.out}")

    if stages:
        print("\nStage evolution:")
        for stage in stages:
            print(
                f"- {stage['stage']}: "
                f"doc_coverage={stage['document_coverage']}, "
                f"doc_refs={stage['total_document_references']}, "
                f"unique_docs={stage['unique_document_urls']}, "
                f"suspicious_shared={stage['suspicious_shared_document_urls_more_than_3_programs']}"
            )

    if manifest_summary:
        print("\nFinal download quality:")
        print(f"- score={manifest_summary['score']}")
        print(f"- valid_downloads={manifest_summary['valid_downloads']} / {manifest_summary['total_refs']}")
        print(
            f"- programs_with_valid_download="
            f"{manifest_summary['programs_with_valid_download']} / {manifest_summary['programs']}"
        )


if __name__ == "__main__":
    main()
