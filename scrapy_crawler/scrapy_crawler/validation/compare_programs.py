import json
import csv
import re
from pathlib import Path
from collections import Counter, defaultdict


BASE_DIR = Path(__file__).resolve().parent

EXPECTED_PATH = BASE_DIR / "programs.json"
ACTUAL_PATH = BASE_DIR.parent / "spider_outputs" / "programmes_with_curricula_enriched.json"
MANUAL_REVIEW_CSV = BASE_DIR / "manual_review.csv"

METRICS_DIR = BASE_DIR / "metrics" / "compare_programs"
METRICS_DIR.mkdir(exist_ok=True)

REPORT_JSON = METRICS_DIR / "program_comparison_metrics.json"
MISSING_CSV = METRICS_DIR / "missing_expected_programmes.csv"
EXTRA_CSV = METRICS_DIR / "extra_actual_programmes.csv"
ECTS_MISMATCH_CSV = METRICS_DIR / "ects_mismatches.csv"
DUPLICATES_CSV = METRICS_DIR / "duplicates_actual_programmes.csv"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_level(level):
    level = (level or "").strip().lower()

    if level in {"b", "ba", "bachelor"} or "bachelor" in level:
        return "bachelor"
    if level in {"m", "ma", "master"} or "master" in level:
        return "master"
    if level in {"d", "do", "phd", "doctorate", "doctorat"} or "doctor" in level:
        return "doctorate"

    return level


def normalize_text(value):
    if value is None:
        return ""
    value = str(value).strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def programme_key_from_expected(item):
    return (
        normalize_text(item.get("programme_url_en") or item.get("url")),
        normalize_level(item.get("level")),
    )


def programme_key_from_actual(item):
    return (
        normalize_text(item.get("programme_url_en") or item.get("programme_url")),
        normalize_level(item.get("level")),
    )


def expected_ects_set(item):
    ects = item.get("ects") or {}
    values = set()

    main = ects.get("main")
    if isinstance(main, int):
        values.add(main)

    for variant in ects.get("variants") or []:
        if isinstance(variant, int):
            values.add(variant)

    return values


def actual_ects_set(items):
    values = set()

    for item in items:
        ects = item.get("ects_points")
        if isinstance(ects, int):
            values.add(ects)

    return values


def write_csv(path, rows, fieldnames):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def pct(numerator, denominator):
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)


def load_manual_review(path: Path):
    if not path.exists():
        return {}

    review = {}

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            key = (
                normalize_text(row.get("url")),
                normalize_level(row.get("level")),
            )
            review[key] = row.get("manual_check", "")

    return review


def classify_manual_status(note: str) -> str:
    note = (note or "").strip().lower()

    if not note:
        return "unreviewed"

    if "not sure" in note or "unsure" in note or "unclear" in note:
        return "uncertain"

    if "didnt find" in note or "didn't find" in note or "not found" in note:
        return "uncertain"

    if "website incorrect" in note or "website wrong" in note:
        return "website_incorrect"

    if "official site incorrect" in note or "official website incorrect" in note:
        return "website_incorrect"

    if "actually incorrect" in note or "incorrect" in note or "wrong" in note:
        return "incorrect"

    if "correct" in note or "ok" in note or "accepted" in note:
        return "correct"

    return "unreviewed"


def main():
    expected = load_json(EXPECTED_PATH)
    actual = load_json(ACTUAL_PATH)
    manual_review = load_manual_review(MANUAL_REVIEW_CSV)

    expected_by_key = {}
    for item in expected:
        key = programme_key_from_expected(item)
        expected_by_key[key] = item

    actual_by_key = defaultdict(list)
    for item in actual:
        key = programme_key_from_actual(item)
        actual_by_key[key].append(item)

    expected_keys = set(expected_by_key.keys())
    actual_keys = set(actual_by_key.keys())

    matched_keys = expected_keys & actual_keys
    missing_keys = expected_keys - actual_keys
    extra_keys = actual_keys - expected_keys

    missing_rows = []
    for key in sorted(missing_keys):
        item = expected_by_key[key]
        missing_rows.append({
            "name": item.get("name"),
            "level": normalize_level(item.get("level")),
            "expected_ects": sorted(expected_ects_set(item)),
            "url": item.get("programme_url_en") or item.get("url"),
        })

    extra_rows = []
    for key in sorted(extra_keys):
        items = actual_by_key[key]
        first = items[0]
        extra_rows.append({
            "programme": first.get("programme") or first.get("programme_name_en"),
            "level": normalize_level(first.get("level")),
            "actual_ects": sorted(actual_ects_set(items)),
            "url": first.get("programme_url_en") or first.get("programme_url"),
        })

    ects_mismatch_rows = []
    ects_exact_matches = 0
    ects_partial_matches = 0
    ects_mismatches = 0

    manual_accepted = 0
    manual_rejected = 0
    manual_uncertain = 0
    manual_unreviewed = 0

    for key in sorted(matched_keys):
        exp_item = expected_by_key[key]
        act_items = actual_by_key[key]

        exp_ects = expected_ects_set(exp_item)
        act_ects = actual_ects_set(act_items)

        if exp_ects == act_ects:
            ects_exact_matches += 1
            continue

        if exp_ects & act_ects:
            ects_partial_matches += 1
            match_type = "partial"
        else:
            match_type = "none"

        ects_mismatches += 1

        manual_note = manual_review.get(key, "")
        manual_status = classify_manual_status(manual_note)

        if manual_status in {"correct", "website_incorrect"}:
            manual_accepted += 1
        elif manual_status == "incorrect":
            manual_rejected += 1
        elif manual_status == "uncertain":
            manual_uncertain += 1
        else:
            manual_unreviewed += 1

        ects_mismatch_rows.append({
            "name": exp_item.get("name"),
            "level": normalize_level(exp_item.get("level")),
            "expected_ects": sorted(exp_ects),
            "actual_ects": sorted(act_ects),
            "missing_ects": sorted(exp_ects - act_ects),
            "extra_ects": sorted(act_ects - exp_ects),
            "url": exp_item.get("programme_url_en") or exp_item.get("url"),
            "match_type": match_type,
            "manual_status": manual_status,
            "manual_check": manual_note,
        })

    actual_record_keys = [
        (
            normalize_text(item.get("programme_url_en") or item.get("programme_url")),
            normalize_level(item.get("level")),
            item.get("ects_points"),
        )
        for item in actual
    ]

    duplicate_counter = Counter(actual_record_keys)

    duplicate_rows = []
    duplicate_records = 0

    for record_key, count in duplicate_counter.items():
        if count > 1:
            duplicate_records += count - 1
            url, level, ects = record_key
            duplicate_rows.append({
                "url": url,
                "level": level,
                "ects_points": ects,
                "count": count,
                "duplicate_surplus": count - 1,
            })

    adjusted_ects_mismatches = ects_mismatches - manual_accepted

    report = {
        "input_files": {
            "expected_programs": str(EXPECTED_PATH),
            "actual_programs": str(ACTUAL_PATH),
            "manual_review": str(MANUAL_REVIEW_CSV),
        },
        "counts": {
            "expected_programmes": len(expected_by_key),
            "actual_programmes_unique": len(actual_by_key),
            "actual_programme_records": len(actual),
            "matched_programmes": len(matched_keys),
            "missing_expected_programmes": len(missing_keys),
            "extra_actual_programmes": len(extra_keys),
        },
        "coverage_metrics": {
            "programme_import_coverage_percent": pct(len(matched_keys), len(expected_keys)),
            "missing_programme_rate_percent": pct(len(missing_keys), len(expected_keys)),
            "extra_programme_rate_percent": pct(len(extra_keys), len(actual_keys)),
        },
        "ects_metrics_raw": {
            "matched_programmes_checked": len(matched_keys),
            "ects_exact_matches": ects_exact_matches,
            "ects_partial_matches": ects_partial_matches,
            "ects_mismatches": ects_mismatches,
            "ects_exact_match_rate_percent": pct(ects_exact_matches, len(matched_keys)),
            "ects_mismatch_rate_percent": pct(ects_mismatches, len(matched_keys)),
        },
        "ects_metrics_manual_adjusted": {
            "raw_ects_mismatches": ects_mismatches,
            "manual_accepted_mismatches": manual_accepted,
            "manual_rejected_mismatches": manual_rejected,
            "manual_uncertain_mismatches": manual_uncertain,
            "manual_unreviewed_mismatches": manual_unreviewed,
            "adjusted_ects_mismatches": adjusted_ects_mismatches,
            "adjusted_ects_mismatch_rate_percent": pct(
                adjusted_ects_mismatches,
                len(matched_keys),
            ),
            "adjusted_ects_match_rate_percent": pct(
                len(matched_keys) - adjusted_ects_mismatches,
                len(matched_keys),
            ),
        },
        "duplicate_metrics": {
            "duplicate_actual_records": duplicate_records,
            "duplicate_actual_record_rate_percent": pct(duplicate_records, len(actual)),
            "duplicate_groups": len(duplicate_rows),
        },
        "level_breakdown": {},
    }

    for level in ["bachelor", "master", "doctorate"]:
        expected_level_keys = {k for k in expected_keys if k[1] == level}
        actual_level_keys = {k for k in actual_keys if k[1] == level}
        matched_level_keys = expected_level_keys & actual_level_keys

        report["level_breakdown"][level] = {
            "expected": len(expected_level_keys),
            "actual_unique": len(actual_level_keys),
            "matched": len(matched_level_keys),
            "missing": len(expected_level_keys - actual_level_keys),
            "extra": len(actual_level_keys - expected_level_keys),
            "coverage_percent": pct(len(matched_level_keys), len(expected_level_keys)),
        }

    with REPORT_JSON.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    write_csv(
        MISSING_CSV,
        missing_rows,
        ["name", "level", "expected_ects", "url"],
    )

    write_csv(
        EXTRA_CSV,
        extra_rows,
        ["programme", "level", "actual_ects", "url"],
    )

    write_csv(
        ECTS_MISMATCH_CSV,
        ects_mismatch_rows,
        [
            "name",
            "level",
            "expected_ects",
            "actual_ects",
            "missing_ects",
            "extra_ects",
            "url",
            "match_type",
            "manual_status",
            "manual_check",
        ],
    )

    write_csv(
        DUPLICATES_CSV,
        duplicate_rows,
        ["url", "level", "ects_points", "count", "duplicate_surplus"],
    )

    print("Validation complete.")
    print(f"Metrics written to: {REPORT_JSON}")
    print(f"Missing programmes: {len(missing_rows)}")
    print(f"Extra programmes: {len(extra_rows)}")
    print(f"Raw ECTS mismatches: {ects_mismatches}")
    print(f"Manual accepted mismatches: {manual_accepted}")
    print(f"Adjusted ECTS mismatches: {adjusted_ects_mismatches}")
    print(f"Duplicate groups: {len(duplicate_rows)}")


if __name__ == "__main__":
    main()