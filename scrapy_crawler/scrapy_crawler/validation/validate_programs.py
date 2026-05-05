import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse, unquote


IDENTITY_FIELDS = [
    "program_name_en",
    "program_name_de",
    "program_name_fr",
]

ACADEMIC_CORE_FIELDS = [
    "level",
    "ects",
    "min_semesters",
    "commencement_of_studies",
]

ORGANIZATIONAL_FIELDS = [
    "faculty",
    "department",
    "director",
]

ACCESSIBILITY_FIELDS = [
    "language",
    "program_url",
]

DOCUMENT_FIELDS = [
    "curriculum_url",
    "documents",
]

ALL_FIELDS = (
    IDENTITY_FIELDS
    + ACADEMIC_CORE_FIELDS
    + ORGANIZATIONAL_FIELDS
    + ACCESSIBILITY_FIELDS
    + DOCUMENT_FIELDS
)

SHARED_DOC_INCORRECT_THRESHOLD = 3
PROGRAM_DOC_COUNT_SUSPICIOUS_THRESHOLD = 5
NAME_SIMILARITY_SUSPICIOUS_THRESHOLD = 0.18


FIELD_ALIASES = {
    "program_name_en": [
        "programme_name_en", "program_name_en", "name_en", "title_en", "names.en", "name.en"
    ],
    "program_name_de": [
        "programme_name_de", "program_name_de", "name_de", "title_de", "names.de", "name.de"
    ],
    "program_name_fr": [
        "programme_name_fr", "program_name_fr", "name_fr", "title_fr", "names.fr", "name.fr"
    ],

    "level": ["level", "degree_level", "study_level"],

    "ects": ["ects_points", "ects", "credits", "total_ects"],

    "min_semesters": ["min_semesters", "minimum_semesters", "duration_semesters"],

    "commencement_of_studies": [
        "studyplan_metadata.commencement_of_studies",
        "commencement_of_studies",
        "study_start",
        "start_of_studies",
        "start_semester",
        "commencement",
    ],

    "faculty": ["faculty", "faculty_name", "faculties"],

    "department": ["department", "department_name"],

    "director": [
        "study_director",
        "director",
        "program_director",
        "programme_director",
        "study_advisor",
        "responsible",
    ],

    "language": [
        "studyplan_metadata.languages_of_study",
        "language",
        "languages",
        "teaching_language",
        "study_languages",
    ],

    "program_url": [
        "programme_url",
        "program_url",
        "programme_url_en",
        "programme_url_de",
        "programme_url_fr",
        "url",
        "page_url",
        "source_url",
        "detail_url",
    ],

    "curriculum_url": [
        "curriculum_en_url",
        "curriculum_de_url",
        "curriculum_fr_url",
        "curriculum_unspecified_url",
        "curriculum_url",
        "curriculum",
        "curriculum_pdf",
        "study_plan_url",
        "study_plan",
    ],

    "documents": ["documents", "docs", "matched_documents", "downloaded_documents"],
}


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def is_non_empty(value):
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set)):
        return any(is_non_empty(v) for v in value)
    if isinstance(value, dict):
        return any(is_non_empty(v) for v in value.values())
    return True


def norm(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(norm(v) for v in value)
    if isinstance(value, dict):
        return " ".join(norm(v) for v in value.values())
    return re.sub(r"\s+", " ", str(value)).strip()


def get_nested(record, path):
    current = record
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def first_value(record, field):
    for alias in FIELD_ALIASES.get(field, [field]):
        value = get_nested(record, alias)
        if is_non_empty(value):
            return value
    return None


def as_list(value):
    if not is_non_empty(value):
        return []
    if isinstance(value, list):
        return value
    return [value]


def normalize_url(url):
    if not url:
        return None
    url = str(url).strip()
    if url.startswith("//"):
        url = "https:" + url
    return url or None


def url_to_text(url):
    if not url:
        return ""
    parsed = urlparse(str(url))
    text = unquote(parsed.path)
    text = re.sub(r"[/_.\-]+", " ", text)
    text = re.sub(r"\.(pdf|html|htm|docx?|xlsx?)$", "", text, flags=re.I)
    return norm(text)


def extract_doc_url(doc):
    if isinstance(doc, str):
        return normalize_url(doc)

    if isinstance(doc, dict):
        for key in [
            "url",
            "href",
            "document_url",
            "file_url",
            "download_url",
            "pdf_url",
            "link",
            "source_url",
        ]:
            if is_non_empty(doc.get(key)):
                return normalize_url(doc.get(key))

    return None


def extract_doc_title(doc):
    if isinstance(doc, str):
        return url_to_text(doc)

    if isinstance(doc, dict):
        for key in ["title", "name", "label", "filename", "file_name"]:
            if is_non_empty(doc.get(key)):
                return norm(doc.get(key))

        url = extract_doc_url(doc)
        return url_to_text(url)

    return ""


def collect_documents(program):
    docs = []

    for curriculum_key in [
        "curriculum_en_url",
        "curriculum_de_url",
        "curriculum_fr_url",
        "curriculum_unspecified_url",
    ]:
        value = program.get(curriculum_key)
        url = normalize_url(value)
        if url:
            docs.append({
                "url": url,
                "title": url_to_text(url),
                "source_field": curriculum_key,
            })

    documents_value = first_value(program, "documents")
    for doc in as_list(documents_value):
        url = extract_doc_url(doc)
        if url:
            docs.append({
                "url": url,
                "title": extract_doc_title(doc),
                "source_field": "documents",
            })

    seen = set()
    unique = []

    for doc in docs:
        if doc["url"] not in seen:
            unique.append(doc)
            seen.add(doc["url"])

    return unique


def program_name(program):
    names = []

    for field in IDENTITY_FIELDS:
        value = first_value(program, field)
        if is_non_empty(value):
            names.append(norm(value))

    return " | ".join(names)


def program_key(program, index):
    name = program_name(program)
    if name:
        return name

    url = first_value(program, "program_url")
    if is_non_empty(url):
        return norm(url)

    return f"program_{index}"


def tokens(text):
    text = norm(text).lower()
    text = re.sub(r"[^a-zA-ZÀ-ÿ0-9\s]", " ", text)

    stopwords = {
        "the", "and", "for", "with", "und", "der", "die", "das", "des",
        "pour", "avec", "les", "dans", "studies", "study", "programme",
        "program", "bachelor", "master", "doctorate", "of", "in", "de",
        "du", "la", "le", "en", "et",
    }

    return {t for t in text.split() if len(t) >= 3 and t not in stopwords}


def name_document_similarity(program, doc):
    doc_text = doc.get("title") or url_to_text(doc.get("url"))
    doc_tokens = tokens(doc_text)

    best = 0.0

    for field in IDENTITY_FIELDS:
        name = first_value(program, field)
        if not is_non_empty(name):
            continue

        name_text = norm(name)
        name_tokens = tokens(name_text)

        if name_tokens and doc_tokens:
            overlap = len(name_tokens & doc_tokens) / max(1, len(name_tokens))
        else:
            overlap = 0.0

        ratio = SequenceMatcher(None, name_text.lower(), doc_text.lower()).ratio()
        best = max(best, overlap, ratio * 0.6)

    return round(best, 4)


def load_json(path):
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        for key in ["programs", "items", "results", "data"]:
            if isinstance(data.get(key), list):
                return data[key]

    if not isinstance(data, list):
        raise ValueError(f"Expected a list in {path}")

    return data


def compute_field_completeness(programs):
    rows = []

    for field in ALL_FIELDS:
        present = 0
        missing_examples = []

        for i, program in enumerate(programs):
            if is_non_empty(first_value(program, field)):
                present += 1
            elif len(missing_examples) < 5:
                missing_examples.append(program_key(program, i))

        total = len(programs)

        rows.append({
            "field": field,
            "present": present,
            "missing": total - present,
            "total": total,
            "completeness": round(present / total, 4) if total else 0,
            "missing_examples": missing_examples,
        })

    return rows


def compute_group_completeness(programs, fields):
    complete = 0
    examples = []

    for i, program in enumerate(programs):
        if all(is_non_empty(first_value(program, f)) for f in fields):
            complete += 1
        elif len(examples) < 10:
            examples.append(program_key(program, i))

    total = len(programs)

    return {
        "fields": fields,
        "complete": complete,
        "incomplete": total - complete,
        "total": total,
        "rate": round(complete / total, 4) if total else 0,
        "incomplete_examples": examples,
    }


def compute_multilingual_coverage(programs):
    total = len(programs)
    per_language = {}

    for field in IDENTITY_FIELDS:
        present = sum(1 for p in programs if is_non_empty(first_value(p, field)))
        per_language[field] = {
            "present": present,
            "missing": total - present,
            "rate": round(present / total, 4) if total else 0,
        }

    at_least_two = 0
    all_three = 0

    for program in programs:
        count = sum(
            1 for field in IDENTITY_FIELDS
            if is_non_empty(first_value(program, field))
        )

        if count >= 2:
            at_least_two += 1
        if count == 3:
            all_three += 1

    return {
        "per_language": per_language,
        "at_least_two_names": {
            "count": at_least_two,
            "total": total,
            "rate": round(at_least_two / total, 4) if total else 0,
        },
        "all_three_names": {
            "count": all_three,
            "total": total,
            "rate": round(all_three / total, 4) if total else 0,
        },
    }


def compute_document_metrics(programs):
    doc_to_programs = defaultdict(list)
    suspicious_programs = []
    suspicious_name_matches = []

    total_links = 0
    programs_with_docs = 0

    for i, program in enumerate(programs):
        key = program_key(program, i)
        docs = collect_documents(program)

        if docs:
            programs_with_docs += 1

        total_links += len(docs)

        if len(docs) > PROGRAM_DOC_COUNT_SUSPICIOUS_THRESHOLD:
            suspicious_programs.append({
                "program": key,
                "document_count": len(docs),
                "reason": f"more_than_{PROGRAM_DOC_COUNT_SUSPICIOUS_THRESHOLD}_documents",
                "document_urls": [d["url"] for d in docs],
            })

        low_similarity = []

        for doc in docs:
            sim = name_document_similarity(program, doc)

            doc_to_programs[doc["url"]].append({
                "program": key,
                "similarity": sim,
                "source_field": doc["source_field"],
                "title": doc["title"],
            })

            if sim < NAME_SIMILARITY_SUSPICIOUS_THRESHOLD:
                low_similarity.append({
                    "url": doc["url"],
                    "title": doc["title"],
                    "similarity": sim,
                    "reason": "low_name_similarity_multilingual_heuristic",
                })

        if low_similarity:
            suspicious_name_matches.append({
                "program": key,
                "documents": low_similarity,
            })

    shared_docs = []
    correctly_shared_docs = []
    incorrectly_shared_docs = []

    for url, usages in doc_to_programs.items():
        if len(usages) <= 1:
            continue

        classification = (
            "incorrect_or_needs_manual_review"
            if len(usages) > SHARED_DOC_INCORRECT_THRESHOLD
            else "probably_correct_shared_document"
        )

        row = {
            "url": url,
            "program_count": len(usages),
            "classification": classification,
            "programs": usages,
        }

        shared_docs.append(row)

        if classification == "probably_correct_shared_document":
            correctly_shared_docs.append(row)
        else:
            incorrectly_shared_docs.append(row)

    total_programs = len(programs)

    return {
        "summary": {
            "programs_total": total_programs,
            "programs_with_documents": programs_with_docs,
            "document_coverage_rate": round(programs_with_docs / total_programs, 4) if total_programs else 0,
            "total_document_links": total_links,
            "unique_documents": len(doc_to_programs),
            "duplicate_document_links": total_links - len(doc_to_programs),
            "shared_documents_total": len(shared_docs),
            "probably_correct_shared_documents": len(correctly_shared_docs),
            "incorrect_or_needs_manual_review_shared_documents": len(incorrectly_shared_docs),
            "programs_with_too_many_documents": len(suspicious_programs),
        },
        "shared_documents": shared_docs,
        "correctly_shared_documents": correctly_shared_docs,
        "incorrect_or_needs_manual_review_shared_documents": incorrectly_shared_docs,
        "suspicious_programs": suspicious_programs,
        "suspicious_name_matches": suspicious_name_matches,
    }


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def write_field_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "field",
                "present",
                "missing",
                "total",
                "completeness",
                "missing_examples",
            ],
        )

        writer.writeheader()

        for row in rows:
            row = dict(row)
            row["missing_examples"] = " | ".join(row["missing_examples"])
            writer.writerow(row)


def write_shared_docs_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "url",
                "program_count",
                "classification",
                "programs",
            ],
        )

        writer.writeheader()

        for row in rows:
            writer.writerow({
                "url": row["url"],
                "program_count": row["program_count"],
                "classification": row["classification"],
                "programs": " | ".join(p["program"] for p in row["programs"]),
            })


def write_suspicious_programs_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "program",
                "document_count",
                "reason",
                "document_urls",
            ],
        )

        writer.writeheader()

        for row in rows:
            writer.writerow({
                "program": row["program"],
                "document_count": row["document_count"],
                "reason": row["reason"],
                "document_urls": " | ".join(row["document_urls"]),
            })


def write_name_similarity_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "program",
                "url",
                "title",
                "similarity",
                "reason",
            ],
        )

        writer.writeheader()

        for row in rows:
            for doc in row["documents"]:
                writer.writerow({
                    "program": row["program"],
                    "url": doc["url"],
                    "title": doc["title"],
                    "similarity": doc["similarity"],
                    "reason": doc["reason"],
                })

def filter_out_doctorates(programs):
    filtered = []
    excluded = []

    for program in programs:
        level = str(first_value(program, "level") or "").strip().lower()

        if level in {"d", "do", "phd", "doctorate", "doctorat"}:
            excluded.append(program)
        else:
            filtered.append(program)

    return filtered, excluded


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--programs-file",
        default="../spider_outputs/programmes_with_curricula_enriched.json",
    )

    parser.add_argument(
        "--links-file",
        default=None,
    )

    parser.add_argument(
        "--output-dir",
        default="./metrics",
    )

    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent

    programs_path = (base_dir / args.programs_file).resolve()
    output_dir = (base_dir / args.output_dir).resolve()

    programs = load_json(programs_path)
    programs, excluded_doctorates = filter_out_doctorates(programs)

    links_path = None
    if args.links_file:
        links_path = (base_dir / args.links_file).resolve()
        if links_path.exists():
            links = load_json(links_path)
            links, excluded_link_doctorates = filter_out_doctorates(links)
            excluded_doctorates.extend(excluded_link_doctorates)
            programs.extend(links)

    field_completeness = compute_field_completeness(programs)

    multilingual_coverage = compute_multilingual_coverage(programs)

    academic_core_completeness = compute_group_completeness(
        programs,
        [
            "level",
            "ects",
            "min_semesters",
        ],
    )

    study_info_completeness = compute_group_completeness(
        programs,
        [
            "language",
            "commencement_of_studies",
        ],
    )

    curriculum_url_coverage = compute_group_completeness(
        programs,
        [
            "curriculum_url",
        ],
    )

    organizational_completeness = compute_group_completeness(
        programs,
        ORGANIZATIONAL_FIELDS,
    )

    document_metrics = compute_document_metrics(programs)

    report = {
        "metadata": {
            "generated_at": now_utc(),
            "programs_file": str(programs_path),
            "links_file": str(links_path) if links_path else None,
            "program_count": len(programs),
            "excluded_doctorate_programs": len(excluded_doctorates),
            "doctorate_exclusion_note": (
                "Doctorate programs were excluded from completeness metrics because "
                "they follow a different information schema and usually do not contain "
                "ECTS, minimum semesters, language, or commencement fields."
            ),
            "note": (
                "Shared-document correctness is heuristic. "
                "Documents linked to more than 3 programs are classified as "
                "incorrect_or_needs_manual_review for now."
            ),
        },
        "field_completeness": field_completeness,
        "multilingual_coverage": multilingual_coverage,
        "academic_core_completeness": academic_core_completeness,
        "study_info_completeness": study_info_completeness,
        "curriculum_url_coverage": curriculum_url_coverage,
        "organizational_completeness": organizational_completeness,
        "document_metrics": document_metrics,
    }

    write_json(output_dir / "program_validation_metrics.json", report)

    write_field_csv(
        output_dir / "program_validation_field_completeness.csv",
        field_completeness,
    )

    write_shared_docs_csv(
        output_dir / "program_validation_shared_documents.csv",
        document_metrics["shared_documents"],
    )

    write_shared_docs_csv(
        output_dir / "program_validation_incorrect_or_review_shared_documents.csv",
        document_metrics["incorrect_or_needs_manual_review_shared_documents"],
    )

    write_suspicious_programs_csv(
        output_dir / "program_validation_suspicious_program_doc_counts.csv",
        document_metrics["suspicious_programs"],
    )

    write_name_similarity_csv(
        output_dir / "program_validation_suspicious_name_matches.csv",
        document_metrics["suspicious_name_matches"],
    )

    print("\nProgram validation completed.")
    print(f"Programs analyzed: {len(programs)}")
    print(f"Output directory: {output_dir}")

    print("\nKey metrics:")
    print(f"- Multilingual coverage >=2 names: {multilingual_coverage['at_least_two_names']['rate']:.2%}")
    print(f"- Multilingual coverage all 3 names: {multilingual_coverage['all_three_names']['rate']:.2%}")
    print(f"- Academic core completeness: {academic_core_completeness['rate']:.2%}")
    print(f"- Study info completeness: {study_info_completeness['rate']:.2%}")
    print(f"- Curriculum URL coverage: {curriculum_url_coverage['rate']:.2%}")
    print(f"- Organizational completeness: {organizational_completeness['rate']:.2%}")
    print(f"- Document coverage: {document_metrics['summary']['document_coverage_rate']:.2%}")
    print(f"- Shared documents: {document_metrics['summary']['shared_documents_total']}")
    print(f"- Incorrect/review shared documents: {document_metrics['summary']['incorrect_or_needs_manual_review_shared_documents']}")
    print(f"- Programs with >5 docs: {document_metrics['summary']['programs_with_too_many_documents']}")


if __name__ == "__main__":
    main()