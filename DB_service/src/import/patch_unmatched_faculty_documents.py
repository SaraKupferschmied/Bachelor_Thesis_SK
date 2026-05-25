#!/usr/bin/env python3
"""
patch_unmatched_faculty_documents.py

Post-processing patch step for the clean generic matcher.

Purpose
-------
Do NOT change the generic matcher.
Instead, take:

  1) programmes_with_faculty_documents.json
     output of the old/good generic matcher

  2) unmatched_faculty_documents.json
     docs that the generic matcher did not attach

and append only deterministic domain-rule matches for known special cases:
  - EDUFORM leftovers -> teacher education programmes
  - SCIMED category=teaching -> teacher education programmes
  - THEO DAES / DEEM -> teacher education programmes
  - LAW leftovers -> general Law programme at same level
  - Medicine docs -> Human Medicine
  - Environmental Sciences propedeutic/complement docs -> smallest Environmental Sciences
  - Environmental Sciences and Humanities minor/generic docs -> smallest Environmental Humanities programme
  - Interreligious Studies variants -> Interreligious Studies

This script is intentionally conservative:
  - It does not try fuzzy matching for everything.
  - It only patches cases explicitly listed above.
  - Remaining unmatched docs stay unmatched.

Usage
-----
python DB_service/src/import/patch_unmatched_faculty_documents.py ^
  --programmes scrapy_crawler/scrapy_crawler/spider_outputs/programmes_with_faculty_documents.json ^
  --unmatched-docs scrapy_crawler/scrapy_crawler/spider_outputs/unmatched_faculty_documents.json ^
  --out scrapy_crawler/scrapy_crawler/spider_outputs/programmes_with_faculty_documents_patched.json ^
  --patch-audit-out scrapy_crawler/scrapy_crawler/spider_outputs/document_program_patch_audit.json ^
  --remaining-unmatched-out scrapy_crawler/scrapy_crawler/spider_outputs/unmatched_faculty_documents_remaining.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


SYNONYMS = {
    "sciences de lenvironnement": "environmental sciences",
    "sciences de l environnement": "environmental sciences",
    "umweltwissenschaften": "environmental sciences",
    "environmental sciences and humanities": "environmental humanities sustainability studies",
    "environmental sciences humanities": "environmental humanities sustainability studies",
    "umweltgeisteswissenschaften und nachhaltigkeitsforschung": "environmental humanities sustainability studies",
    "interreligiose studien": "interreligious studies",
    "interreligioese studien": "interreligious studies",
    "interreligiose": "interreligious",
    "humanmedizin": "human medicine",
    "medecine humaine": "human medicine",
    "médecine humaine": "human medicine",
    "medizin": "medicine",
    "medecine": "medicine",
    "unterricht auf der sekundarstufe i und an maturitatsschulen": "teacher education secondary level i and baccalaureate schools",
    "unterricht an maturitatsschulen": "teacher education baccalaureate schools",
    "unterricht auf der sekundarstufe i": "teacher education secondary level i",
    "unterricht auf der primarstufe": "teacher education primary level",
}


def norm_text(value: Any) -> str:
    s = strip_accents(str(value or "").lower())
    s = s.replace("&", " and ")
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    for src, dst in SYNONYMS.items():
        src_n = strip_accents(src.lower())
        src_n = re.sub(r"[^a-z0-9]+", " ", src_n).strip()
        if src_n:
            s = re.sub(rf"\b{re.escape(src_n)}\b", dst, s)

    return re.sub(r"\s+", " ", s).strip()


def doc_blob(doc: dict[str, Any]) -> str:
    return norm_text(" ".join(str(x) for x in [
        doc.get("faculty"),
        doc.get("category"),
        doc.get("level"),
        doc.get("program_name"),
        " ".join(doc.get("program_name_variants") or []),
        doc.get("document_label"),
        doc.get("document_url"),
        doc.get("page_url"),
    ] if x))


def programme_blob(p: dict[str, Any]) -> str:
    return norm_text(" ".join(str(x) for x in [
        p.get("faculty"),
        p.get("department"),
        p.get("programme"),
        p.get("programme_name_en"),
        p.get("programme_name_de"),
        p.get("programme_name_fr"),
        p.get("programme_url"),
        p.get("programme_url_en"),
        p.get("programme_url_de"),
        p.get("programme_url_fr"),
    ] if x))


def canonical_faculty(value: Any) -> Optional[str]:
    s = norm_text(value)
    upper = str(value or "").upper()

    if upper in {"EDUFORM", "SCIMED", "LAW", "THEO", "PHILO", "SES", "INTERFACULTY"}:
        return upper

    if "science and medicine" in s or "scimed" in s:
        return "SCIMED"
    if "education" in s or "erziehungs" in s or "eduform" in s:
        return "EDUFORM"
    if "law" in s or "ius" in s or "droit" in s:
        return "LAW"
    if "theology" in s or "theologie" in s or "theo" in s:
        return "THEO"
    if "humanities" in s or "philo" in s or "lettres" in s:
        return "PHILO"
    if "swiss centre for islam" in s or "interfaculty" in s:
        return "INTERFACULTY"
    return None


def level_norm(value: Any) -> Optional[str]:
    if value in {"B", "M", "D"}:
        return str(value)
    s = norm_text(value)
    if "doctor" in s or "phd" in s:
        return "D"
    if "master" in s or "msc" in s or re.search(r"\bma\b", s):
        return "M"
    if "bachelor" in s or "bsc" in s or re.search(r"\bba\b", s):
        return "B"
    return None


def parse_int(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def programme_ects(p: dict[str, Any]) -> Optional[int]:
    return parse_int(p.get("ects_points"))


def doc_ects_values(doc: dict[str, Any]) -> list[int]:
    vals = doc.get("ects_values")
    if isinstance(vals, list):
        out = []
        for v in vals:
            vi = parse_int(v)
            if vi is not None:
                out.append(vi)
        return out
    vi = parse_int(doc.get("ects"))
    return [vi] if vi is not None else []


def candidate_programmes(
    programmes: list[dict[str, Any]],
    phrase: str,
    level: Optional[str] = None,
    ects: Optional[int] = None,
) -> list[int]:
    phrase_n = norm_text(phrase)
    out = []

    for idx, p in enumerate(programmes):
        blob = programme_blob(p)
        if phrase_n not in blob:
            continue
        if level and level_norm(p.get("level")) != level:
            continue
        if ects is not None and programme_ects(p) != ects:
            continue
        out.append(idx)

    return out


def choose_highest_ects(indices: list[int], programmes: list[dict[str, Any]]) -> list[int]:
    if not indices:
        return []
    max_ects = max(programme_ects(programmes[i]) or -1 for i in indices)
    return [i for i in indices if (programme_ects(programmes[i]) or -1) == max_ects]


def choose_smallest_ects(indices: list[int], programmes: list[dict[str, Any]]) -> list[int]:
    valid = [i for i in indices if programme_ects(programmes[i]) is not None]
    if not valid:
        return indices[:1]
    min_ects = min(programme_ects(programmes[i]) for i in valid)
    return [i for i in valid if programme_ects(programmes[i]) == min_ects]


def target_teacher_education(programmes: list[dict[str, Any]], doc: dict[str, Any]) -> tuple[list[int], str]:
    blob = doc_blob(doc)
    level = level_norm(doc.get("level"))

    # Very specific combined case first. This fixes LDSM.
    if (
        "ldsm" in blob
        or "daes" in blob
        or "deem" in blob
        or "secondary level i and baccalaureate schools" in blob
        or "sekundarstufe i und an maturitatsschulen" in blob
    ):
        idxs = candidate_programmes(programmes, "Teacher Education for Secondary Level I and Baccalaureate Schools", level=level)
        if idxs:
            return choose_highest_ects(idxs, programmes), "patch_teacher_education_combined"

    if "ldp" in blob or "primary level" in blob or "primarstufe" in blob:
        idxs = candidate_programmes(programmes, "Teacher Education for Primary Level", level=level)
        if idxs:
            return choose_highest_ects(idxs, programmes), "patch_teacher_education_primary"

    # LDSM contains LDM as substring, so this must stay after LDSM.
    if "ldm" in blob or "baccalaureate schools" in blob or "maturitatsschulen" in blob:
        idxs = candidate_programmes(programmes, "Teacher Education for Baccalaureate Schools", level=level)
        if idxs:
            return choose_highest_ects(idxs, programmes), "patch_teacher_education_baccalaureate"

    if "lds" in blob or "secondary level i" in blob or "sekundarstufe i" in blob:
        idxs = candidate_programmes(programmes, "Teacher Education for Secondary Level I", level=level)
        if idxs:
            return choose_highest_ects(idxs, programmes), "patch_teacher_education_secondary_i"

    # Generic teacher education fallback for EDUFORM leftovers.
    idxs = []
    for i, p in enumerate(programmes):
        pblob = programme_blob(p)
        if "teacher education" not in pblob and "enseignement" not in pblob and "unterricht" not in pblob:
            continue
        if level and level_norm(p.get("level")) != level:
            continue
        idxs.append(i)

    if idxs:
        return choose_highest_ects(idxs, programmes), "patch_teacher_education_generic"

    return [], "patch_teacher_education_no_target"


def patch_targets(programmes: list[dict[str, Any]], doc: dict[str, Any]) -> tuple[list[int], Optional[str]]:
    fac = canonical_faculty(doc.get("faculty"))
    blob = doc_blob(doc)
    level = level_norm(doc.get("level"))
    dvals = doc_ects_values(doc)
    first_doc_ects = dvals[0] if dvals else None

    # EDUFORM leftovers -> teacher education.
    if fac == "EDUFORM":
        return target_teacher_education(programmes, doc)

    # SCIMED teaching-category -> teacher education.
    if fac == "SCIMED" and norm_text(doc.get("category")) in {"teaching", "teach"}:
        return target_teacher_education(programmes, doc)

    # THEO DAES / DEEM -> teacher education.
    if fac == "THEO" and ("daes" in blob or "deem" in blob):
        return target_teacher_education(programmes, doc)

    # Medicine -> Human Medicine.
    if fac == "SCIMED" and ("medicine" in blob or "human medicine" in blob):
        idxs = candidate_programmes(programmes, "Human Medicine", level=level, ects=first_doc_ects)
        if not idxs:
            idxs = candidate_programmes(programmes, "Human Medicine", level=level)
            idxs = choose_highest_ects(idxs, programmes)
        if idxs:
            return idxs, "patch_human_medicine"

    # Environmental Sciences propedeutics/complements -> smallest Environmental Sciences.
    if (
        "environmental sciences" in blob
        and (
            "propedeutiques" in blob
            or "propedeutic" in blob
            or "branches complementaires" in blob
            or "branche complementaire" in blob
            or "bcp" in blob
        )
    ):
        idxs = candidate_programmes(programmes, "Environmental Sciences", level=level)
        idxs = choose_smallest_ects(idxs, programmes)
        if idxs:
            return idxs, "patch_environmental_sciences_smallest"

    # Environmental Humanities / Sciences and Humanities -> smallest EHSS.
    if (
        "environmental sciences and humanities" in blob
        or "environmental humanities" in blob
        or "environmental humanities sustainability studies" in blob
    ):
        idxs = candidate_programmes(programmes, "Environmental Humanities and Sustainability Studies", level=level, ects=first_doc_ects)
        if not idxs:
            idxs = candidate_programmes(programmes, "Environmental Humanities and Sustainability Studies", level=level)
            idxs = choose_smallest_ects(idxs, programmes)
        if idxs:
            return idxs, "patch_environmental_humanities"

    # Interreligious Studies variants.
    if "interreligious studies" in blob:
        idxs = candidate_programmes(programmes, "Interreligious Studies", level=level, ects=first_doc_ects)
        if not idxs:
            idxs = candidate_programmes(programmes, "Interreligious Studies", level=level)
            idxs = choose_highest_ects(idxs, programmes)
        if idxs:
            return idxs, "patch_interreligious_studies"

    # LAW leftovers -> general Law programme same level, ECTS if known, else highest/main.
    if fac == "LAW":
        idxs = candidate_programmes(programmes, "Law", level=level, ects=first_doc_ects)
        if not idxs:
            idxs = candidate_programmes(programmes, "Law", level=level)
            idxs = choose_highest_ects(idxs, programmes)
        if idxs:
            return idxs, "patch_law_fallback"

    return [], None


def document_payload(doc: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "url": doc.get("document_url"),
        "label": doc.get("document_label"),
        "source_type": "faculty_crawler_patch",
        "faculty": doc.get("faculty"),
        "language": doc.get("language"),
        "category": doc.get("category"),
        "level": doc.get("level"),
        "ects": doc.get("ects"),
        "ects_values": doc.get("ects_values") or [],
        "page_url": doc.get("page_url"),
        "file_url": doc.get("file_url"),
        "path": doc.get("path"),
        "checksum": doc.get("checksum"),
        "file_status": doc.get("file_status"),
        "source_file": doc.get("source_file"),
        "source_index": doc.get("source_index"),
        "match_score": 99,
        "match_reasons": [reason],
    }


def patch(programmes: list[dict[str, Any]], unmatched_docs: list[dict[str, Any]]):
    already = []
    for p in programmes:
        already.append({d.get("url") for d in p.get("documents", []) if d.get("url")})

    audit = []
    remaining = []
    patched_count = 0

    for doc_idx, doc in enumerate(unmatched_docs):
        targets, reason = patch_targets(programmes, doc)

        if not targets:
            remaining.append(doc)
            continue

        url = doc.get("document_url")
        if not url:
            remaining.append(doc)
            continue

        attached_any = False
        for p_idx in targets:
            if url in already[p_idx]:
                attached_any = True
                continue

            programmes[p_idx].setdefault("documents", [])
            programmes[p_idx]["documents"].append(document_payload(doc, reason or "patch"))
            already[p_idx].add(url)
            attached_any = True
            patched_count += 1

            audit.append({
                "programme_index": p_idx,
                "programme": programmes[p_idx].get("programme"),
                "programme_level": programmes[p_idx].get("level"),
                "programme_ects": programmes[p_idx].get("ects_points"),
                "document_index": doc_idx,
                "document_program_name": doc.get("program_name"),
                "document_label": doc.get("document_label"),
                "document_url": url,
                "document_ects_values": doc.get("ects_values") or [],
                "reason": reason,
            })

        if not attached_any:
            remaining.append(doc)

    for p in programmes:
        docs = p.get("documents") or []
        docs.sort(key=lambda d: (str(d.get("source_type") or ""), str(d.get("label") or ""), str(d.get("url") or "")))
        p["document_match_count"] = len(docs)
        p["matched_sources"] = ["faculty_documents_normalized"] if docs else []
        if len(docs) > 15:
            p["document_match_warning"] = "many_documents_attached_check_manually"
        else:
            p.pop("document_match_warning", None)

    return programmes, audit, remaining, patched_count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--programmes", required=True, help="programmes_with_faculty_documents.json from the old matcher")
    parser.add_argument("--unmatched-docs", required=True, help="unmatched_faculty_documents.json from the old matcher")
    parser.add_argument("--out", required=True)
    parser.add_argument("--patch-audit-out")
    parser.add_argument("--remaining-unmatched-out")
    args = parser.parse_args()

    programmes = load_json(Path(args.programmes))
    unmatched_docs = load_json(Path(args.unmatched_docs))

    if not isinstance(programmes, list):
        raise ValueError("--programmes must be a JSON list")
    if not isinstance(unmatched_docs, list):
        raise ValueError("--unmatched-docs must be a JSON list")

    patched, audit, remaining, patched_count = patch(programmes, unmatched_docs)

    save_json(Path(args.out), patched)
    if args.patch_audit_out:
        save_json(Path(args.patch_audit_out), audit)
    if args.remaining_unmatched_out:
        save_json(Path(args.remaining_unmatched_out), remaining)

    print(f"Input unmatched docs: {len(unmatched_docs)}")
    print(f"Patch attachments added: {patched_count}")
    print(f"Patch audit rows: {len(audit)}")
    print(f"Remaining unmatched docs: {len(remaining)}")
    print(f"Wrote patched programmes to {args.out}")


if __name__ == "__main__":
    main()
