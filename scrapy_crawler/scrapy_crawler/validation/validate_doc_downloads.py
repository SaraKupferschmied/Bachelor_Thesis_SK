#!/usr/bin/env python3
"""
Validate programme-document acquisition quality.

This script does TWO things:

1. Merge-pipeline evaluation
   Captures document quality across the current merge pipeline:
   - Step 15: normalized faculty document JSONs
   - Step 16: merged study-plan documents
   - Step 17: unmatched-document patch output
   - audit files for automatic matches and patch additions
   - remaining unmatched faculty documents
   - final download manifest or Scrapy FilesPipeline metadata

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
        "document_url",
        "file_url",
        "url",
        "source_url",
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


def first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for path in paths:
        if path.exists():
            return path
    return None


def summarize_audit_file(path: Path, label: str) -> Dict[str, Any]:
    data = load_json(path)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list, got {type(data).__name__}")

    rows = [r for r in data if isinstance(r, dict)]
    total = len(rows)

    urls = []
    scores = []
    programmes = set()
    reasons = Counter()
    faculties = Counter()
    statuses = Counter()

    for row in rows:
        url = row.get("document_url") or row.get("file_url") or row.get("url")
        if present(url):
            urls.append(str(url))

        score = row.get("score") or row.get("match_score")
        if isinstance(score, (int, float)):
            scores.append(float(score))

        programme = row.get("programme") or row.get("programme_name_en") or row.get("program_name")
        if present(programme):
            programmes.add(norm(programme))

        row_reasons = row.get("reasons") or row.get("match_reasons") or row.get("reason")
        if isinstance(row_reasons, list):
            reasons.update(str(v) for v in row_reasons if present(v))
        elif present(row_reasons):
            reasons.update([str(row_reasons)])

        faculty = row.get("faculty") or row.get("programme_faculty")
        if present(faculty):
            faculties.update([str(faculty)])

        status = row.get("file_status") or row.get("status")
        if present(status):
            statuses.update([str(status)])

    return {
        "label": label,
        "file": str(path),
        "records_total": total,
        "unique_programmes": len(programmes),
        "total_document_references": len(urls),
        "unique_document_urls": len(set(urls)),
        "duplicate_document_references": len(urls) - len(set(urls)),
        "average_score": round(statistics.mean(scores), 6) if scores else None,
        "min_score": min(scores) if scores else None,
        "max_score": max(scores) if scores else None,
        "reason_counts": dict(reasons),
        "faculty_counts": dict(faculties),
        "status_counts": dict(statuses),
    }


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



def summarize_scrapy_files_downloads(spider_outputs: Path, downloads_root: Path) -> Dict[str, Any]:
    """Summarize Scrapy FilesPipeline downloads when no custom manifest exists.

    Newer spiders write file metadata into spider_outputs/faculty_documents_normalized.json
    and faculty_programs/*.json, while PDFs are stored below downloads/<faculty>/full.
    This fallback keeps the validation usable without _program_docs_manifest.json.
    """
    rows: List[Dict[str, Any]] = []

    normalized = spider_outputs / "faculty_documents_normalized.json"
    if normalized.exists():
        data = load_json(normalized)
        if isinstance(data, list):
            for r in data:
                if not isinstance(r, dict):
                    continue
                source_file = r.get("source_file")
                faculty = Path(str(source_file)).stem.lower() if source_file else str(r.get("faculty") or "").lower()
                rel_path = r.get("path")
                local_path = downloads_root / faculty / rel_path if faculty and rel_path else None
                rows.append({
                    "program_key": "|".join(str(r.get(k) or "") for k in ["faculty", "level", "ects", "program_name"]),
                    "program_name": r.get("program_name"),
                    "doc_key": Path(str(rel_path)).stem if rel_path else None,
                    "source_url": r.get("document_url") or r.get("file_url"),
                    "doc_label": r.get("document_label"),
                    "source_type": "pdf" if str(r.get("document_url") or r.get("file_url") or "").lower().split("?")[0].endswith(".pdf") else "other",
                    "local_path": str(local_path) if local_path else None,
                    "sha256": r.get("sha256") or r.get("checksum"),
                    "status": "downloaded" if local_path and local_path.exists() else (r.get("file_status") or "missing_local_file"),
                })

    faculty_dir = spider_outputs / "faculty_programs"
    if not rows and faculty_dir.exists():
        for path in sorted(faculty_dir.glob("*.json")):
            data = load_json(path)
            if not isinstance(data, list):
                continue
            faculty_slug = path.stem.lower()
            for r in data:
                if not isinstance(r, dict):
                    continue
                files = r.get("files") if isinstance(r.get("files"), list) else []
                for f in files:
                    if not isinstance(f, dict):
                        continue
                    rel_path = f.get("path")
                    local_path = downloads_root / faculty_slug / rel_path if rel_path else None
                    rows.append({
                        "program_key": "|".join(str(r.get(k) or "") for k in ["faculty", "title"]),
                        "program_name": r.get("title"),
                        "doc_key": Path(str(rel_path)).stem if rel_path else None,
                        "source_url": f.get("url"),
                        "doc_label": None,
                        "source_type": "pdf" if str(f.get("url") or "").lower().split("?")[0].endswith(".pdf") else "other",
                        "local_path": str(local_path) if local_path else None,
                        "sha256": f.get("sha256") or f.get("checksum"),
                        "status": "downloaded" if local_path and local_path.exists() else (f.get("status") or "missing_local_file"),
                    })

    if rows:
        return summarize_manifest_rows(rows, f"Scrapy FilesPipeline fallback from {spider_outputs}")

    pdfs = sorted(downloads_root.rglob("*.pdf")) if downloads_root.exists() else []
    rows = [
        {
            "program_key": p.parts[-3] if len(p.parts) >= 3 else "unknown",
            "program_name": None,
            "doc_key": p.stem,
            "source_url": None,
            "doc_label": p.name,
            "source_type": "pdf",
            "local_path": str(p),
            "sha256": p.stem,
            "status": "downloaded",
        }
        for p in pdfs
    ]
    return summarize_manifest_rows(rows, f"PDF fallback from {downloads_root}")


def summarize_manifest_rows(data: List[Dict[str, Any]], source_label: str) -> Dict[str, Any]:
    tmp = {"__rows__": data}
    # Inline the same metric logic as summarize_manifest without requiring a file.
    total = len(data)
    status = Counter(str(r.get("status") or "missing_status") for r in data if isinstance(r, dict))
    source_type = Counter(str(r.get("source_type") or "missing_source_type") for r in data if isinstance(r, dict))
    successful_statuses = {"downloaded", "already_present", "uptodate"}
    attempted_source_types = {"pdf", "calameo"}
    valid_downloads = sum(1 for r in data if isinstance(r, dict) and r.get("status") in successful_statuses and present(r.get("local_path")) and present(r.get("sha256")))
    attempted = sum(1 for r in data if isinstance(r, dict) and r.get("source_type") in attempted_source_types)
    skipped = status.get("skipped_non_pdf", 0)
    failed = status.get("failed", 0) + status.get("calameo_no_direct_pdf", 0) + status.get("download_failed", 0) + status.get("missing_local_file", 0)
    by_program: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in data:
        if isinstance(r, dict):
            by_program[str(r.get("program_key") or "missing_program_key")].append(r)
    programs = len(by_program)
    programs_with_valid = sum(1 for records in by_program.values() if any(r.get("status") in successful_statuses for r in records))
    doc_keys = [r.get("doc_key") for r in data if isinstance(r, dict) and present(r.get("doc_key"))]
    unique_docs = len(set(doc_keys))
    duplicate_doc_refs = total - unique_docs if total else 0
    metadata_fields = ["program_key", "doc_key", "program_name", "source_url", "doc_label", "status"]
    metadata_completeness = statistics.mean([safe_div(sum(1 for r in data if isinstance(r, dict) and present(r.get(field))), total) for field in metadata_fields]) if total else 0.0
    components = {
        "download_success_all_refs": safe_div(valid_downloads, total),
        "download_success_attempted_pdf_or_calameo": safe_div(valid_downloads, attempted),
        "program_doc_availability": safe_div(programs_with_valid, programs),
        "actionable_source_type": safe_div(attempted, total),
        "deduplication_by_doc_key": safe_div(unique_docs, total),
        "metadata_completeness": metadata_completeness,
    }
    weights = {"download_success_all_refs": 0.30, "program_doc_availability": 0.25, "actionable_source_type": 0.15, "deduplication_by_doc_key": 0.10, "metadata_completeness": 0.20}
    score = sum(weights[key] * components[key] for key in weights)
    return {
        "file": source_label,
        "metric_scope": "final_document_download_quality",
        "description": "Fallback summary for Scrapy FilesPipeline document downloads.",
        "total_refs": total, "programs": programs, "status": dict(status), "source_type": dict(source_type),
        "valid_downloads": valid_downloads, "attempted_pdf_or_calameo": attempted, "skipped_non_pdf": skipped, "failed": failed,
        "programs_with_valid_download": programs_with_valid, "unique_docs": unique_docs, "duplicate_doc_refs": duplicate_doc_refs,
        "components": {k: round(v, 6) for k, v in components.items()}, "weights": weights, "score": round(score, 6),
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
        help="Path to _program_docs_manifest.json. If absent, the script falls back to Scrapy FilesPipeline metadata/downloads.",
    )
    parser.add_argument(
        "--downloads-root",
        type=Path,
        default=Path("downloads"),
        help="Path to the Scrapy FilesPipeline downloads folder, used when no manifest exists. Default: downloads",
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
        (
            "15_merge_normalize_faculty_jsons",
            first_existing([
                args.spider_outputs / "faculty_documents_normalized.json",
                args.spider_outputs / "faculty_programs_normalized.json",
                args.spider_outputs / "faculty_documents_normalized(2).json",
            ]),
        ),
        (
            "16_merge_studyplans",
            first_existing([
                args.spider_outputs / "program_links_with_ects_and_docs.json",
                args.spider_outputs / "program_links_with_ects_and_docs_enriched.json",
                args.spider_outputs / "program_links_with_ects_and_docs_enriched(1).json",
            ]),
        ),
        (
            "17_unmatched_patch",
            first_existing([
                args.spider_outputs / "programmes_with_faculty_documents_patched.json",
                args.spider_outputs / "programmes_with_faculty_documents_patched(1).json",
                args.spider_outputs / "program_links_with_ects_and_docs_enriched.json",
            ]),
        ),
    ]

    stages = []
    missing_stage_files = []

    for label, path in stage_candidates:
        if path and path.exists():
            stages.append(summarize_program_stage(path, label))
        else:
            missing_stage_files.append(label)

    stages = add_stage_deltas(stages)

    audit_files = {
        "document_program_match_audit": first_existing([
            args.spider_outputs / "document_program_match_audit.json",
            args.spider_outputs / "validation" / "document_program_match_audit.json",
        ]),
        "document_program_patch_audit": first_existing([
            args.spider_outputs / "document_program_patch_audit.json",
            args.spider_outputs / "validation" / "document_program_patch_audit.json",
        ]),
        "unmatched_faculty_documents_remaining": first_existing([
            args.spider_outputs / "unmatched_faculty_documents_remaining.json",
            args.spider_outputs / "unmatched_faculty_documents_remaining(1).json",
        ]),
    }

    audit_summaries = {}
    for label, path in audit_files.items():
        if path and path.exists():
            audit_summaries[label] = summarize_audit_file(path, label)
        else:
            missing_stage_files.append(label)

    manifest_summary = None
    if args.manifest.exists():
        manifest_summary = summarize_manifest(args.manifest)
    else:
        manifest_summary = summarize_scrapy_files_downloads(args.spider_outputs, args.downloads_root)

    result = {
        "metric_scope": "document_acquisition_and_download_quality",
        "stage_evolution": stages,
        "audit_summaries": audit_summaries,
        "final_download_quality": manifest_summary,
        "missing_stage_files": missing_stage_files,
        "notes": [
            "Stage evolution captures the current merge pipeline: normalized faculty JSONs, merged study plans, and unmatched patch output.",
            "Final download quality captures whether the resulting references were actually downloadable.",
            "High duplicate document references can be expected when one study plan is valid for multiple programme variants, but very high sharing is useful as a manual-review signal.",
            "PDF parsing correctness should be evaluated separately in the next validation layer.",
        ],
    }

    save_json(args.out, result)

    print("=== DOCUMENT ACQUISITION + DOWNLOAD QUALITY ===")
    print(f"Output: {args.out}")

    if stages:
        print("\nMerge-pipeline stage evolution:")
        for stage in stages:
            print(
                f"- {stage['stage']}: "
                f"doc_coverage={stage['document_coverage']}, "
                f"doc_refs={stage['total_document_references']}, "
                f"unique_docs={stage['unique_document_urls']}, "
                f"suspicious_shared={stage['suspicious_shared_document_urls_more_than_3_programs']}"
            )

    if audit_summaries:
        print("\nAudit summaries:")
        for label, summary in audit_summaries.items():
            print(
                f"- {label}: "
                f"records={summary['records_total']}, "
                f"unique_docs={summary['unique_document_urls']}, "
                f"unique_programmes={summary['unique_programmes']}"
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
