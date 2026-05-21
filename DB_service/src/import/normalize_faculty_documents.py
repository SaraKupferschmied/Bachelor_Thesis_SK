#!/usr/bin/env python3
"""
normalize_faculty_documents.py

Normalize all faculty crawler outputs into ONE flat list with ONE ROW PER
DOCUMENT found by the faculty crawlers.

Compared with the first version, this version improves:
  - ECTS detection from filenames/URLs such as MUS_BA_120_DE.pdf, BA120,
    Plan_MSc_CH120_en.pdf, Plan_BCp30_MA_fr.pdf
  - multi-ECTS detection such as BA-120-60-30-ETCS and MA-60-und-30-ETCS
  - typo ETCS as well as ECTS
  - SCIMED program_name: prefers document label when the source programme is
    only a generic page/header such as Curricula 2025, Transition disposition,
    Offered teaching fields, etc.
  - removes accidental dict-string variants from program_name_variants

Output row schema:
{
  "faculty": "SCIMED",
  "language": "en",
  "category": "master",
  "level": "M",
  "year": 2025,
  "program_name": "...",
  "program_name_variants": ["...", "..."],
  "ects": 90,
  "ects_values": [90, 30],
  "page_url": "...",
  "document_url": "...",
  "document_label": "...",
  "file_url": "...",
  "path": "full/....pdf",
  "checksum": "...",
  "file_status": "downloaded",
  "source_file": "scimed.json",
  "source_index": 123
}
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value)).strip()
    return s or None


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def first_nonempty(*values: Any) -> Optional[str]:
    for value in values:
        s = clean_text(value)
        if s:
            return s
    return None


def first_int(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def uniq_keep_order(values: list[Any]) -> list[str]:
    seen = set()
    out: list[str] = []
    for value in values:
        s = clean_text(value)
        if not s:
            continue
        if looks_like_dict_string(s):
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def urlish_text(value: Any) -> str:
    return unquote(str(value or "")).replace("%20", " ")


def looks_like_dict_string(s: str) -> bool:
    s = s.strip()
    return s.startswith("{") and s.endswith("}") and ":" in s


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------

# Normal ECTS expressions:
#   120 ECTS, 90+30 ECTS, 90 + 30 ETCS, 60 credits, 30 Kreditpunkte
ECTS_RE = re.compile(
    r"\b(\d{1,3})(?:\s*\+\s*(\d{1,3}))?\s*(?:ects|etcs|kreditpunkte|credits?|punkte|points?)\b",
    re.IGNORECASE,
)

# Filename/code expressions:
#   BA120, BA_120, BA-120, BScSI30, MSc_CH120, MUS_MA_30, BCp30, MA-60-und-30-ETCS
CODE_ECTS_RE = re.compile(
    r"(?i)"
    r"(?:^|[^a-z0-9])"
    r"(?:"
    r"ba|bachelor|bsc|bscsi|basi|"
    r"ma|master|msc|spmsc|commsc|premsc|"
    r"bc|bcp|bp|br|major|minor|hauptfach|nebenfach|fach|"
    r"mus|esp|it[a-z]*|dt|fr|de|plan|studienplan"
    r")"
    r"[_\-\s]*"
    r"(\d{1,3})"
    r"(?:[^a-z0-9]|$)"
)

# Numeric cluster before/after ECTS/ETCS:
#   BA-120-60-30-ETCS, MA-60-und-30-ETCS, 120-60-30 ECTS
MULTI_BEFORE_ECTS_RE = re.compile(
    r"(?i)\b((?:\d{1,3})(?:\s*(?:-|_|/|und|et|and|\+)\s*\d{1,3})+)\s*(?:ects|etcs|credits?|kreditpunkte)\b"
)

YEAR_RE = re.compile(r"\b(20\d{2})\b")

LEVEL_PATTERNS = [
    ("D", re.compile(r"\b(doctorat|doktorat|doctorate|phd|doctoral)\b", re.I)),
    ("M", re.compile(r"\b(master|msc|ma\b|spmsc|commsc|premsc|m\s*sc|m\s*a)\b", re.I)),
    ("B", re.compile(r"\b(bachelor|bsc|ba\b|b\s*sc|b\s*a)\b", re.I)),
]


VALID_ECTS = {
    9, 14, 20, 30, 35, 50, 60, 90, 105, 106, 120, 150, 180, 240
}


def plausible_ects(value: int) -> bool:
    # Keep this permissive because EDUFORM has e.g. 9/14/20 extension diplomas.
    return 1 <= value <= 240


def add_ects(out: list[int], value: int) -> None:
    if plausible_ects(value) and value not in out:
        out.append(value)


def parse_ects_values(*texts: Any) -> list[int]:
    """
    Return all ECTS values found in text, label, URL, or filename.

    The order is important: label evidence comes first because it is usually
    cleaner than file path/date noise.
    """
    out: list[int] = []

    for raw in texts:
        direct = first_int(raw)
        if direct is not None:
            add_ects(out, direct)
            continue

        if not raw:
            continue

        text = urlish_text(raw)

        # 90+30 ECTS, 120 ECTS, 30 ETCS typo, etc.
        for match in ECTS_RE.finditer(text):
            add_ects(out, int(match.group(1)))
            if match.group(2):
                add_ects(out, int(match.group(2)))

        # BA-120-60-30-ETCS / MA-60-und-30-ETCS
        for match in MULTI_BEFORE_ECTS_RE.finditer(text):
            for num in re.findall(r"\d{1,3}", match.group(1)):
                add_ects(out, int(num))

        # BA120 / BA_120 / MUS_MA_30 / Plan_MSc_CH120 / Plan_BCp30_MA
        for match in CODE_ECTS_RE.finditer(text):
            add_ects(out, int(match.group(1)))

        # Conservative fallback for common study-plan filenames:
        # Slavistik_60_05032015 -> 60, but ignore dates/ids.
        filename = Path(text.split("?")[0]).name
        if any(k.lower() in filename.lower() for k in [
            "studienplan", "plan", "ple", "ba", "ma", "bsc", "msc", "ects", "etcs"
        ]):
            for num in re.findall(r"(?<!\d)(\d{1,3})(?!\d)", filename):
                n = int(num)
                if n in VALID_ECTS:
                    add_ects(out, n)

    return out


def first_ects(*texts: Any) -> Optional[int]:
    values = parse_ects_values(*texts)
    return values[0] if values else None


def infer_level(*texts: Any) -> Optional[str]:
    blob = " ".join(urlish_text(t) for t in texts if t)
    for level, pattern in LEVEL_PATTERNS:
        if pattern.search(blob):
            return level

    low = blob.lower()
    if "/doctorat/" in low or "/doktorat/" in low:
        return "D"
    if "/master/" in low or "/ma/" in low or "_ma" in low or "-ma" in low:
        return "M"
    if "/bachelor/" in low or "/ba/" in low or "_ba" in low or "-ba" in low:
        return "B"

    return None


def infer_year(*texts: Any) -> Optional[int]:
    for text in texts:
        direct = first_int(text)
        if direct is not None and 2000 <= direct <= 2100:
            return direct
        if not text:
            continue
        match = YEAR_RE.search(urlish_text(text))
        if match:
            return int(match.group(1))
    return None


def infer_language(*texts: Any) -> Optional[str]:
    blob = " ".join(urlish_text(t) for t in texts if t)

    match = re.search(r"/(de|fr|en|it|es)(?:/|$)", blob)
    if match:
        return match.group(1)

    low = blob.lower()
    if re.search(r"(^|[_\-\s])de($|[_\-\s.])", low) or "deutsch" in low or "allemand" in low:
        return "de"
    if re.search(r"(^|[_\-\s])fr($|[_\-\s.])", low) or "français" in low or "francais" in low or "french" in low:
        return "fr"
    if re.search(r"(^|[_\-\s])en($|[_\-\s.])", low) or "english" in low or "engl" in low:
        return "en"
    if re.search(r"(^|[_\-\s])it($|[_\-\s.])", low) or "italien" in low or "italiano" in low:
        return "it"
    if re.search(r"(^|[_\-\s])es($|[_\-\s.])", low) or "español" in low or "spanisch" in low:
        return "es"

    return None


def normalize_faculty(value: Any, source_file: str) -> Optional[str]:
    raw = clean_text(value)
    blob = f"{raw or ''} {source_file}".lower()

    if "eduform" in blob or "education" in blob or "erziehungs" in blob:
        return "EDUFORM"
    if "interfaculty" in blob or "interfak" in blob or "int" == source_file.lower().removesuffix(".json"):
        return "INTERFACULTY"
    if "law" in blob or "ius" in blob or "droit" in blob or "rechts" in blob:
        return "LAW"
    if "philo" in blob or "lettres" in blob or "arts" in blob:
        return "PHILO"
    if "scimed" in blob or "science" in blob or "medicine" in blob or "med" in blob:
        return "SCIMED"
    if "ses" in blob or "management" in blob or "economics" in blob or "social sciences" in blob:
        return "SES"
    if "theo" in blob or "theology" in blob or "théologie" in blob:
        return "THEO"

    return raw.upper() if raw else None


def normalize_category(value: Any, level: Optional[str], page_url: Optional[str], source_file: str, *texts: Any) -> Optional[str]:
    s = clean_text(value)
    if s:
        low = s.lower()
        if low in {"b", "ba", "bachelor"}:
            return "bachelor"
        if low in {"m", "ma", "master"}:
            return "master"
        if low in {"d", "doctorat", "doktorat", "doctorate", "phd"}:
            return "doctorate"
        if low in {"nebenfach", "minor", "minors"}:
            return "nebenfach"
        return s

    blob = " ".join(urlish_text(x) for x in (page_url, source_file, *texts) if x).lower()

    if "minor" in blob or "nebenfach" in blob or "nebenprogramm" in blob or "branche complementaire" in blob:
        return "nebenfach"
    if "zusatzfach" in blob or "+30" in blob or "plus30" in blob or "bcp30" in blob:
        return "zusatzfach"
    if "trans" in blob:
        return "transition"
    if "teach" in blob or "teacher" in blob or "enseignement" in blob:
        return "teaching"
    if level == "B":
        return "bachelor"
    if level == "M":
        return "master"
    if level == "D":
        return "doctorate"
    return None


# ---------------------------------------------------------------------------
# SCIMED / generic title handling
# ---------------------------------------------------------------------------

GENERIC_SCIMED_PATTERNS = [
    re.compile(r"^curricula\s+\d{4}", re.I),
    re.compile(r"transition disposition", re.I),
    re.compile(r"offered teaching fields", re.I),
    re.compile(r"preparation or complement", re.I),
]


DOC_LABEL_PREFIX_RE = re.compile(
    r"(?i)^(study plan|studienplan|plan d['’]études|plan d['’]etudes|plan de estudios|"
    r"master of science in|bachelor of science in|master of arts in|bachelor of arts in|"
    r"spmsc in|complement to the major in|preparation or complement to the major in|"
    r"branche complémentaire en|branche complementaire en)\s+"
)


def is_generic_scimed_program_name(name: Optional[str]) -> bool:
    if not name:
        return False
    return any(rx.search(name) for rx in GENERIC_SCIMED_PATTERNS)


def program_name_from_document_label(label: Optional[str]) -> Optional[str]:
    if not label:
        return None

    s = clean_text(label)
    if not s:
        return None

    s = re.sub(r"\([^)]*\b(?:fr|de|en|it|es)\b[^)]*\)", "", s, flags=re.I).strip()
    s = DOC_LABEL_PREFIX_RE.sub("", s).strip()
    s = re.sub(r"\b(?:BA|BSc|MA|MSc|SpMSc|ComMSc)\b", "", s, flags=re.I).strip()
    s = re.sub(r"\b\d{1,3}(?:\+\d{1,3})?\s*(?:ECTS|ETCS|credits?|Kreditpunkte)\b", "", s, flags=re.I).strip()
    s = re.sub(r"\s*[-–]\s*[A-Z]{1,6}$", "", s).strip()
    s = re.sub(r"\s+", " ", s)

    return s or None


# ---------------------------------------------------------------------------
# Shape-specific extraction
# ---------------------------------------------------------------------------

def get_program_dict(item: dict[str, Any]) -> dict[str, Any]:
    program = item.get("program")
    return program if isinstance(program, dict) else {}


def get_program_names(item: dict[str, Any]) -> list[str]:
    names: list[Any] = []
    program = get_program_dict(item)

    for key in ["name", "name_de", "name_fr", "name_en", "title", "program"]:
        names.append(program.get(key))

    for key in [
        "title",
        "program",
        "programme",
        "programme_name",
        "programme_name_de",
        "programme_name_fr",
        "programme_name_en",
        "name",
    ]:
        value = item.get(key)
        if not isinstance(value, dict):
            names.append(value)

    for value in as_list(item.get("name_variants")):
        names.append(value)

    return uniq_keep_order(names)


def get_page_url(item: dict[str, Any]) -> Optional[str]:
    program = get_program_dict(item)
    return first_nonempty(
        item.get("page_url"),
        item.get("page_url_de"),
        item.get("page_url_fr"),
        item.get("page_url_en"),
        item.get("studienplan_url"),
        program.get("page_url"),
        program.get("page_url_de"),
        program.get("page_url_fr"),
        program.get("page_url_en"),
        program.get("studienplan_url"),
    )


def get_documents(item: dict[str, Any]) -> list[dict[str, Optional[str]]]:
    docs: list[dict[str, Optional[str]]] = []

    for doc in as_list(item.get("documents")):
        if isinstance(doc, dict):
            url = first_nonempty(doc.get("url"), doc.get("href"), doc.get("file_url"), doc.get("document_url"))
            if not url:
                continue
            docs.append({
                "url": url,
                "label": first_nonempty(doc.get("label"), doc.get("text"), doc.get("title")),
            })
        else:
            url = clean_text(doc)
            if url:
                docs.append({"url": url, "label": None})

    seen = {doc["url"] for doc in docs if doc.get("url")}
    for url in as_list(item.get("file_urls")) + as_list(item.get("doc_urls")):
        url_s = clean_text(url)
        if url_s and url_s not in seen:
            docs.append({"url": url_s, "label": None})
            seen.add(url_s)

    return docs


def get_file_metadata_by_url(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}
    for file_obj in as_list(item.get("files")):
        if not isinstance(file_obj, dict):
            continue
        url = first_nonempty(file_obj.get("url"), file_obj.get("file_url"))
        if url:
            by_url[url] = file_obj
    return by_url


def normalize_item(item: dict[str, Any], source_file: str, source_index: int) -> list[dict[str, Any]]:
    base_program_names = get_program_names(item)
    base_program_name = base_program_names[0] if base_program_names else None
    program = get_program_dict(item)
    page_url = get_page_url(item)

    faculty = normalize_faculty(item.get("faculty"), source_file)

    file_meta_by_url = get_file_metadata_by_url(item)
    rows: list[dict[str, Any]] = []

    for doc in get_documents(item):
        doc_url = doc["url"]
        label = doc.get("label")

        if not doc_url:
            continue

        file_meta = file_meta_by_url.get(doc_url, {})

        program_name = base_program_name
        program_names = list(base_program_names)

        # SCIMED often uses generic page headers. The document label is more precise.
        if faculty == "SCIMED" and is_generic_scimed_program_name(base_program_name):
            label_name = program_name_from_document_label(label)
            if label_name:
                program_name = label_name
                program_names = uniq_keep_order([label_name] + base_program_names)

        evidence_texts = [
            label,
            doc_url,
            file_meta.get("path"),
            program_name,
            " ".join(program_names),
            item.get("category"),
            page_url,
        ]

        level = first_nonempty(item.get("level"), program.get("level"))
        if level not in {"B", "M", "D"}:
            level = infer_level(*evidence_texts)

        language = first_nonempty(
            item.get("lang"),
            item.get("language"),
            infer_language(page_url, item.get("page_url_de"), item.get("page_url_fr"), item.get("page_url_en"), doc_url, label),
        )

        item_ects_values = parse_ects_values(
            item.get("ects"),
            item.get("ects_points"),
            program.get("ects"),
            program.get("ects_points"),
        )
        doc_ects_values = parse_ects_values(label, doc_url, file_meta.get("path"))

        # If document-specific evidence exists, use it. Otherwise keep item/program evidence.
        ects_values = doc_ects_values or item_ects_values
        ects = ects_values[0] if ects_values else None

        # Zusatzfächer / complements: if SCIMED label says +30 or BCp30, it is often
        # a complement/minor; keep 30 explicitly and let matching attach to 30/60 later.
        category = normalize_category(item.get("category"), level, page_url, source_file, label, doc_url, program_name)

        year = infer_year(item.get("year"), label, doc_url, page_url, program_name)

        row = {
            "faculty": faculty,
            "language": language or infer_language(doc_url, label),
            "category": category,
            "level": level,
            "year": year,
            "program_name": program_name,
            "program_name_variants": program_names,
            "ects": ects,
            "ects_values": ects_values,
            "page_url": page_url,
            "document_url": doc_url,
            "document_label": label,
            "file_url": first_nonempty(file_meta.get("url"), file_meta.get("file_url"), doc_url),
            "path": file_meta.get("path"),
            "checksum": file_meta.get("checksum"),
            "file_status": file_meta.get("status"),
            "source_file": source_file,
            "source_index": source_index,
        }
        rows.append(row)

    return rows


# ---------------------------------------------------------------------------
# Validation / stats
# ---------------------------------------------------------------------------

def deduplicate_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}

    for row in rows:
        key = (
            row.get("faculty"),
            row.get("category"),
            row.get("level"),
            row.get("program_name"),
            tuple(row.get("ects_values") or []),
            row.get("page_url"),
            row.get("document_url"),
        )
        unique[key] = row

    return list(unique.values())


def print_stats(rows: list[dict[str, Any]]) -> None:
    by_faculty: dict[str, int] = {}
    missing_label = 0
    missing_ects = 0
    missing_program = 0
    multi_ects = 0

    for row in rows:
        faculty = row.get("faculty") or "UNKNOWN"
        by_faculty[faculty] = by_faculty.get(faculty, 0) + 1
        if not row.get("document_label"):
            missing_label += 1
        if row.get("ects") is None:
            missing_ects += 1
        if len(row.get("ects_values") or []) > 1:
            multi_ects += 1
        if not row.get("program_name"):
            missing_program += 1

    print("Rows by faculty:")
    for faculty, count in sorted(by_faculty.items()):
        print(f"  {faculty}: {count}")

    print(f"Rows missing document_label: {missing_label}")
    print(f"Rows missing ects: {missing_ects}")
    print(f"Rows with multiple ects_values: {multi_ects}")
    print(f"Rows missing program_name: {missing_program}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, help="Directory with faculty crawler JSON files")
    parser.add_argument("--out", required=True, help="Output JSON file")
    parser.add_argument(
        "--include",
        nargs="*",
        default=None,
        help="Optional list of JSON filenames to include, e.g. edu.json law.json",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    include = set(args.include) if args.include else None

    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    rows: list[dict[str, Any]] = []

    for path in sorted(input_dir.glob("*.json")):
        if include and path.name not in include:
            continue

        data = load_json(path)
        if not isinstance(data, list):
            raise ValueError(f"{path} must contain a JSON list, got {type(data).__name__}")

        before = len(rows)
        for idx, item in enumerate(data):
            if isinstance(item, dict):
                rows.extend(normalize_item(item, path.name, idx))

        print(f"{path.name}: added {len(rows) - before} document rows from {len(data)} source entries")

    rows = deduplicate_rows(rows)
    save_json(Path(args.out), rows)

    print(f"\nWrote {len(rows)} normalized document rows to {args.out}")
    print_stats(rows)


if __name__ == "__main__":
    main()
