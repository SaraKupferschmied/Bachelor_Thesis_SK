import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_COURSES_PATH = BASE_DIR.parent / "spider_outputs" / "courses.json"
METRICS_DIR = BASE_DIR / "metrics" / "validate_courses"


def get_path(item, *keys):
    current = item
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def first_present(item, paths):
    for path in paths:
        value = get_path(item, *path)
        if is_present(value):
            return value
    return None


def is_present(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def pct(numerator, denominator):
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)


def normalize_text(value):
    if value is None:
        return ""
    value = str(value).strip().lower()
    return re.sub(r"\s+", " ", value)


def normalized_semester(item):
    """Prefer parsed semester, but fall back to semester_raw/details.Semester.

    Current course output stores examples such as SS-2027 in course.semester_raw
    while course.semester is often null.
    """
    return first_present(item, [
        ("course", "semester"),
        ("course", "semester_raw"),
        ("details", "Semester"),
    ])


def detail_url(item):
    return first_present(item, [
        ("source", "detail_url"),
        ("source", "detail_page_url"),
    ])


REQUIRED_IDENTITY_FIELDS = {
    "course.code": lambda x: get_path(x, "course", "code"),
    "course.name": lambda x: get_path(x, "course", "name"),
    "course.semester_or_raw": normalized_semester,
    "source.detail_url": detail_url,
}

METADATA_FIELDS = {
    "course.ects": lambda x: get_path(x, "course", "ects"),
    "course.degree_level": lambda x: first_present(x, [("course", "degree_level"), ("course", "degree_level_raw"), ("details", "Level")]),
    "course.faculty": lambda x: first_present(x, [("course", "faculty"), ("details", "Faculty"), ("details", "Fakultät")]),
    "course.domain": lambda x: first_present(x, [("course", "domain"), ("details", "Domain"), ("details", "Bereich")]),
    "course.languages": lambda x: first_present(x, [("course", "languages"), ("details", "Languages"), ("details", "Sprachen")]),
    "course.course_type": lambda x: first_present(x, [("course", "course_type"), ("details", "Type of lesson"), ("details", "Veranstaltungstyp")]),
    "schedule.summary_schedule": lambda x: first_present(x, [("schedule", "Summary schedule"), ("schedule", "Vorlesungszeiten"), ("schedule", "Course dates")]),
    "schedule.structure": lambda x: first_present(x, [("schedule", "Struct. of the schedule"), ("schedule", "Structure of the schedule")]),
    "teaching.responsibles": lambda x: first_present(x, [("teaching", "Responsibles"), ("teaching", "Responsible"), ("teaching", "Verantwortliche")]),
    "teaching.teachers": lambda x: first_present(x, [("teaching", "Teachers"), ("teaching", "Lecturers"), ("teaching", "Dozenten-innen")]),
    "teaching.description": lambda x: first_present(x, [("teaching", "Description"), ("teaching", "Beschreibung")]),
    "teaching.learning_outcomes": lambda x: first_present(x, [("teaching", "Learning outcomes"), ("teaching", "Learning Outcomes"), ("teaching", "Lernziele")]),
    "dates": lambda x: get_path(x, "dates"),
    "assessment": lambda x: get_path(x, "assessment"),
    "affiliations": lambda x: get_path(x, "affiliations"),
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_csv(path: Path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def compute_field_completeness(courses, fields):
    rows = []
    metrics = {}
    total = len(courses)
    for field_name, getter in fields.items():
        present = sum(1 for item in courses if is_present(getter(item)))
        completeness = pct(present, total)
        metrics[field_name] = {
            "present": present,
            "missing": total - present,
            "total": total,
            "completeness_percent": completeness,
        }
        rows.append({
            "field": field_name,
            "present": present,
            "missing": total - present,
            "total": total,
            "completeness_percent": completeness,
        })
    return metrics, rows


def duplicate_rows(courses, key_func, key_name):
    groups = defaultdict(list)
    for item in courses:
        key = key_func(item)
        if isinstance(key, tuple):
            if all(key):
                groups[key].append(item)
        elif key:
            groups[key].append(item)

    rows = []
    duplicate_surplus = 0
    for key, items in sorted(groups.items(), key=lambda kv: str(kv[0])):
        if len(items) <= 1:
            continue
        duplicate_surplus += len(items) - 1
        first = items[0]
        rows.append({
            "duplicate_key_type": key_name,
            "duplicate_key": " | ".join(key) if isinstance(key, tuple) else str(key),
            "count": len(items),
            "duplicate_surplus": len(items) - 1,
            "example_name": get_path(first, "course", "name"),
            "example_url": detail_url(first),
        })
    return rows, duplicate_surplus


def count_by(courses, getter):
    c = Counter(getter(item) or "__missing__" for item in courses)
    return dict(sorted(c.items(), key=lambda kv: str(kv[0])))


def course_code(item):
    return normalize_text(get_path(item, "course", "code"))


def course_semester(item):
    return normalize_text(normalized_semester(item))


def normalized_detail_url(item):
    return normalize_text(detail_url(item))


def main():
    parser = argparse.ArgumentParser(description="Validate scraped Unifr timetable course JSON.")
    parser.add_argument("--courses", type=Path, default=DEFAULT_COURSES_PATH)
    parser.add_argument("--catalogue-found-count", type=int, default=None)
    parser.add_argument("--scrape-started-at", default=None)
    parser.add_argument("--output-prefix", default=None)
    args = parser.parse_args()

    METRICS_DIR.mkdir(parents=True, exist_ok=True)

    courses = load_json(args.courses)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prefix = args.output_prefix or "course_validation"

    identity_metrics, identity_rows = compute_field_completeness(courses, REQUIRED_IDENTITY_FIELDS)
    metadata_metrics, metadata_rows = compute_field_completeness(courses, METADATA_FIELDS)

    detail_duplicate_rows, detail_duplicate_surplus = duplicate_rows(courses, normalized_detail_url, "source.detail_url")
    code_semester_duplicate_rows, code_semester_duplicate_surplus = duplicate_rows(
        courses,
        lambda item: (course_code(item), course_semester(item)),
        "course.code + semester_or_raw",
    )
    duplicate_report_rows = detail_duplicate_rows + code_semester_duplicate_rows

    scraped_count = len(courses)
    catalogue_found_count = args.catalogue_found_count

    report = {
        "validation_created_at": now,
        "input_files": {"courses": str(args.courses)},
        "snapshot": {
            "scrape_started_at": args.scrape_started_at,
            "catalogue_found_count": catalogue_found_count,
            "scraped_course_count": scraped_count,
            "snapshot_course_coverage_percent": pct(scraped_count, catalogue_found_count) if catalogue_found_count else None,
            "note": "Coverage is only valid for the catalogue state observed at crawl time. The online catalogue is dynamic.",
        },
        "counts": {
            "course_records": scraped_count,
            "unique_detail_urls": len({normalized_detail_url(item) for item in courses if normalized_detail_url(item)}),
            "unique_code_semester_pairs": len({(course_code(item), course_semester(item)) for item in courses if course_code(item) and course_semester(item)}),
        },
        "identity_field_completeness": identity_metrics,
        "metadata_field_completeness": metadata_metrics,
        "duplicate_metrics": {
            "duplicate_detail_url_surplus_records": detail_duplicate_surplus,
            "duplicate_detail_url_rate_percent": pct(detail_duplicate_surplus, scraped_count),
            "duplicate_code_semester_surplus_records": code_semester_duplicate_surplus,
            "duplicate_code_semester_rate_percent": pct(code_semester_duplicate_surplus, scraped_count),
        },
        "breakdowns": {
            "by_semester_or_raw": count_by(courses, normalized_semester),
            "by_degree_level": count_by(courses, lambda x: first_present(x, [("course", "degree_level"), ("course", "degree_level_raw"), ("details", "Level")])),
            "by_faculty": count_by(courses, lambda x: first_present(x, [("course", "faculty"), ("details", "Faculty"), ("details", "Fakultät")])),
        },
    }

    report_json = METRICS_DIR / f"{prefix}_metrics.json"
    completeness_csv = METRICS_DIR / f"{prefix}_field_completeness.csv"
    duplicates_csv = METRICS_DIR / f"{prefix}_duplicates.csv"

    write_json(report_json, report)
    write_csv(completeness_csv, identity_rows + metadata_rows, ["field", "present", "missing", "total", "completeness_percent"])
    write_csv(duplicates_csv, duplicate_report_rows, ["duplicate_key_type", "duplicate_key", "count", "duplicate_surplus", "example_name", "example_url"])

    print("Course validation complete.")
    print(f"Metrics JSON: {report_json}")
    print(f"Field completeness CSV: {completeness_csv}")
    print(f"Duplicates CSV: {duplicates_csv}")
    print(f"Course records: {scraped_count}")
    if catalogue_found_count:
        print(f"Snapshot coverage: {report['snapshot']['snapshot_course_coverage_percent']}%")


if __name__ == "__main__":
    main()
