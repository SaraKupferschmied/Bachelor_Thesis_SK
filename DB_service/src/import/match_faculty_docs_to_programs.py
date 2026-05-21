#!/usr/bin/env python3
"""
match_faculty_documents_to_programs.py

Attach normalized faculty documents to programs from
programs_with_curricula_enriched.json.

Inputs
------
1) programs_with_curricula_enriched.json
   Source of truth for programs.

2) faculty_documents_normalized.json
   One row per document from the new normalizer.

Output
------
A copy of the program list, each program enriched with:

  "documents": [
    {
      "url": "...",
      "label": "...",
      "file_url": "...",
      "path": "...",
      "checksum": "...",
      "faculty": "...",
      "language": "...",
      "category": "...",
      "level": "...",
      "ects": 90,
      "ects_values": [90, 30],
      "match_score": 12,
      "match_reasons": [...]
    }
  ],
  "document_match_count": 3

Matching policy
---------------
- Programs are the source of truth.
- Documents come ONLY from faculty_documents_normalized.json.
- If a document has ects_values, it can attach to every clearly matching
  programme whose ects_points is in ects_values.
- If a document has no ects_values, attach it only to the best program(s)
  by name/faculty/level, preferring the highest ects_points among otherwise
  comparable candidates. This handles generic docs, flyers, and web study-plan
  links that usually describe the main programme.
- A document may be attached to multiple programs only when the evidence is
  clear, e.g. same name + same level + ECTS variant match.

Usage
-----
python DB_service/src/import/match_faculty_documents_to_programs.py ^
  --programs scrapy_crawler/scrapy_crawler/spider_outputs/programs_with_curricula_enriched.json ^
  --docs scrapy_crawler/scrapy_crawler/spider_outputs/faculty_documents_normalized.json ^
  --out scrapy_crawler/scrapy_crawler/spider_outputs/programs_with_faculty_documents.json ^
  --audit-out scrapy_crawler/scrapy_crawler/spider_outputs/document_program_match_audit.json
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Optional


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
# Normalization
# ---------------------------------------------------------------------------

FACULTY_MAP = {
    "faculty of science and medicine": "SCIMED",
    "science and medicine": "SCIMED",
    "faculty of management economics and social sciences": "SES",
    "faculty of management, economics and social sciences": "SES",
    "management economics and social sciences": "SES",
    "faculty of law": "LAW",
    "law": "LAW",
    "faculty of humanities": "PHILO",
    "faculty of arts and humanities": "PHILO",
    "humanities": "PHILO",
    "arts and humanities": "PHILO",
    "faculty of theology": "THEO",
    "theology": "THEO",
    "faculty of education": "EDUFORM",
    "faculty of education and training sciences": "EDUFORM",
    "fakultat fur erziehungs und bildungswissenschaften": "EDUFORM",
    "fakultaet fuer erziehungs und bildungswissenschaften": "EDUFORM",
    "swiss centre for islam and society": "INTERFACULTY",
    "institute for family research and counseling": "INTERFACULTY",
    "environmental sciences and humanities institute": "INTERFACULTY",
}

SYNONYM_PHRASES = {
    # DE/FR/EN programme equivalents
    "informatik": "computer science",
    "informatique": "computer science",
    "wirtschaftsinformatik": "business informatics",
    "informatique de gestion": "business informatics",
    "betriebswirtschaftslehre": "business administration",
    "gestion dentreprise": "business administration",
    "volkswirtschaftslehre": "economics",
    "sciences economiques": "economics",
    "humanmedizin": "human medicine",
    "medecine humaine": "human medicine",
    "médecine humaine": "human medicine",
    "medizin": "medicine",
    "medecine": "medicine",
    "médecine": "medicine",
    "mathematik": "mathematics",
    "mathematiques": "mathematics",
    "mathématiques": "mathematics",
    "physik": "physics",
    "physique": "physics",
    "chemie": "chemistry",
    "chimie": "chemistry",
    "geographie": "geography",
    "géographie": "geography",
    "biologie": "biology",
    "biochimie": "biochemistry",
    "sportwissenschaften": "sport sciences",
    "sciences du sport": "sport sciences",
    "digitale neurowissenschaft": "digital neuroscience",
    "neurosciences digitales": "digital neuroscience",
    "experimentelle biomedizinische forschung": "experimental biomedical research",
    "recherche biomedicale experimentale": "experimental biomedical research",
    "recherche biomédicale expérimentale": "experimental biomedical research",
    "islam und gesellschaft": "islam and society",
    "islam et societe": "islam and society",
    "islam et société": "islam and society",
    "familien kinder und jugendstudien": "family children youth studies",
    "etudes sur la famille lenfance et la jeunesse": "family children youth studies",
    "études sur la famille lenfance et la jeunesse": "family children youth studies",
    "umweltgeisteswissenschaften und nachhaltigkeitsforschung": "environmental humanities sustainability studies",
    "humanites environnementales et durabilite": "environmental humanities sustainability studies",
    "humanités environnementales et durabilité": "environmental humanities sustainability studies",
    "unterricht auf der primarstufe": "teacher education primary level",
    "formation a lenseignement pour le degre primaire": "teacher education primary level",
    "unterricht auf der sekundarstufe i": "teacher education secondary level i",
    "formation a lenseignement pour le degre secondaire i": "teacher education secondary level i",
    "unterricht an maturitatsschulen": "teacher education baccalaureate schools",
    "maturitätsschulen": "baccalaureate schools",
    "erziehungswissenschaften": "education sciences",
    "sciences de leducation": "education sciences",
}

STOPWORDS = {
    "study", "studies", "plan", "plans", "studienplan", "etudes", "études",
    "programme", "program", "programm", "curriculum", "curricula",
    "bachelor", "master", "doctorat", "doktorat", "doctorate", "phd",
    "major", "minor", "hauptfach", "nebenfach", "zusatzfach", "fach",
    "ects", "etcs", "credits", "kreditpunkte", "punkte",
    "of", "in", "and", "the", "for", "to", "de", "du", "des", "der", "die",
    "das", "und", "en", "pour", "fur", "fuer", "für", "a", "le", "la", "les",
    "version", "current", "valid", "gültig", "gueltig", "seit", "from",
    "webseite", "website", "flyer", "brochure", "broschure", "broschüre",
    "presentation", "prasentation", "présentation",
}


def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def norm_text(value: Any) -> str:
    s = strip_accents(str(value or "").lower())
    s = s.replace("&", " and ")
    s = re.sub(r"[’'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Phrase-level synonyms after punctuation normalization.
    for src, dst in SYNONYM_PHRASES.items():
        src_n = strip_accents(src.lower())
        src_n = re.sub(r"[^a-z0-9]+", " ", src_n).strip()
        if src_n:
            s = re.sub(rf"\b{re.escape(src_n)}\b", dst, s)

    return re.sub(r"\s+", " ", s).strip()


def token_set(value: Any) -> set[str]:
    return {
        t for t in norm_text(value).split()
        if t and t not in STOPWORDS and not t.isdigit()
    }


def faculty_canonical(value: Any) -> Optional[str]:
    if not value:
        return None

    s = norm_text(value)
    upper = str(value).upper()
    if upper in {"SCIMED", "SES", "LAW", "PHILO", "THEO", "EDUFORM", "INTERFACULTY"}:
        return upper

    for key, val in FACULTY_MAP.items():
        if norm_text(key) in s:
            return val

    if "scimed" in s:
        return "SCIMED"
    if "ses" in s:
        return "SES"
    if "ius" in s:
        return "LAW"
    if "theo" in s:
        return "THEO"
    if "philo" in s or "lettres" in s:
        return "PHILO"
    if "edu" in s or "education" in s:
        return "EDUFORM"

    return None


def level_norm(value: Any) -> Optional[str]:
    if value in {"B", "M", "D"}:
        return str(value)

    s = norm_text(value)
    if any(x in s for x in ["doctorat", "doktorat", "doctorate", "phd"]):
        return "D"
    if any(x in s.split() for x in ["master", "msc", "ma"]):
        return "M"
    if any(x in s.split() for x in ["bachelor", "bsc", "ba"]):
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


# ---------------------------------------------------------------------------
# Programme/document feature extraction
# ---------------------------------------------------------------------------

def programme_names(p: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in [
        "programme",
        "programme_name_en",
        "programme_name_de",
        "programme_name_fr",
    ]:
        v = p.get(key)
        if v and str(v) not in out:
            out.append(str(v))
    return out


def programme_text(p: dict[str, Any]) -> str:
    parts = programme_names(p)
    parts.extend([
        p.get("department"),
        p.get("faculty"),
        p.get("programme_url"),
        p.get("programme_url_en"),
        p.get("programme_url_de"),
        p.get("programme_url_fr"),
    ])
    return " ".join(str(x) for x in parts if x)


def doc_text(doc: dict[str, Any]) -> str:
    parts = [
        doc.get("program_name"),
        " ".join(doc.get("program_name_variants") or []),
        doc.get("document_label"),
        doc.get("document_url"),
        doc.get("page_url"),
        doc.get("category"),
    ]
    return " ".join(str(x) for x in parts if x)


def best_name_overlap(p: dict[str, Any], doc: dict[str, Any]) -> tuple[int, float, set[str], set[str]]:
    d_tokens = token_set(doc_text(doc))

    best_overlap = 0
    best_ratio = 0.0
    best_p_tokens: set[str] = set()

    for name in programme_names(p):
        pts = token_set(name)
        if not pts:
            continue
        overlap = len(pts & d_tokens)
        ratio = overlap / max(1, len(pts))
        if ratio > best_ratio or (ratio == best_ratio and overlap > best_overlap):
            best_overlap = overlap
            best_ratio = ratio
            best_p_tokens = pts

    return best_overlap, best_ratio, best_p_tokens, d_tokens


def abbreviation_candidates(names: list[str]) -> set[str]:
    out = set()
    for name in names:
        words = [w for w in re.findall(r"[A-Za-zÀ-ÿ]+", name) if len(w) > 2]
        if len(words) >= 2:
            abbr = "".join(w[0] for w in words).lower()
            if 2 <= len(abbr) <= 8:
                out.add(abbr)

    # Common explicit aliases used in filenames/labels.
    aliases = {
        "computer science": "cs",
        "business informatics": "bi",
        "digital neuroscience": "dn",
        "experimental biomedical research": "ebr",
        "environmental humanities sustainability studies": "ehss",
        "sport sciences": "sp",
    }

    for name in names:
        n = norm_text(name)
        for phrase, alias in aliases.items():
            if phrase in n:
                out.add(alias)

    return out


def url_stem_tokens(url: Any) -> set[str]:
    stem = Path(str(url or "").split("?")[0]).stem
    return token_set(stem)


def has_abbreviation_match(p: dict[str, Any], doc: dict[str, Any]) -> Optional[str]:
    names = programme_names(p)
    abbrs = abbreviation_candidates(names)
    if not abbrs:
        return None

    doc_blob = norm_text(doc_text(doc))
    file_tokens = url_stem_tokens(doc.get("document_url"))

    for abbr in abbrs:
        if abbr in file_tokens or re.search(rf"\b{re.escape(abbr)}\b", doc_blob):
            return abbr

    return None


def ects_values(doc: dict[str, Any]) -> list[int]:
    values = doc.get("ects_values")
    if isinstance(values, list):
        return [int(v) for v in values if isinstance(v, int) or (isinstance(v, str) and v.isdigit())]

    v = parse_int(doc.get("ects"))
    return [v] if v is not None else []


def programme_ects(p: dict[str, Any]) -> Optional[int]:
    return parse_int(p.get("ects_points"))


def is_generic_document(doc: dict[str, Any]) -> bool:
    label = norm_text(doc.get("document_label"))
    url = norm_text(doc.get("document_url"))

    generic_words = [
        "flyer", "brochure", "broschure", "broschüre", "presentation",
        "prasentation", "praesentation", "reglement", "regulation",
        "webseite", "website"
    ]

    if any(w in label for w in generic_words):
        return True

    # Web study-plan shortcuts often have no ECTS and are generic for all variants.
    if "studies unifr ch go" in url:
        return True

    return False


def is_main_ects_for_level(level: Optional[str], ects: Optional[int]) -> bool:
    if level == "B":
        return ects in {120, 150, 180, 240}
    if level == "M":
        return ects in {90, 105, 120, 180}
    if level == "D":
        return ects is None or ects == 30
    return False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def base_score(p: dict[str, Any], doc: dict[str, Any]) -> tuple[int, list[str], dict[str, Any]]:
    score = 0
    reasons: list[str] = []
    features: dict[str, Any] = {}

    p_fac = faculty_canonical(p.get("faculty"))
    d_fac = faculty_canonical(doc.get("faculty"))

    if p_fac and d_fac:
        if p_fac == d_fac:
            score += 3
            reasons.append("faculty")
        else:
            # Some interfaculty programs are hosted under a faculty in studies.
            # Allow PHILO/INTERFACULTY soft crossing when names are strong later.
            if {p_fac, d_fac} <= {"PHILO", "INTERFACULTY"}:
                score -= 1
                reasons.append("soft_faculty_cross")
            else:
                return -999, ["faculty_conflict"], features

    p_level = level_norm(p.get("level"))
    d_level = level_norm(doc.get("level") or doc.get("category"))

    if p_level and d_level:
        if p_level == d_level:
            score += 2
            reasons.append("level")
        else:
            return -999, ["level_conflict"], features

    p_ects = programme_ects(p)
    d_ects_values = ects_values(doc)

    if p_ects is not None and d_ects_values:
        if p_ects in d_ects_values:
            score += 5
            reasons.append("ects")
        else:
            # Strong conflict. Allow only if the document is generic and name is strong later.
            score -= 4
            reasons.append("ects_conflict")

    overlap, ratio, p_tokens, d_tokens = best_name_overlap(p, doc)
    features["name_overlap"] = overlap
    features["name_ratio"] = ratio

    if ratio >= 0.95 and overlap >= 1:
        score += 8
        reasons.append("exact_or_near_name")
    elif ratio >= 0.60 and overlap >= 2:
        score += 6
        reasons.append("strong_name_overlap")
    elif overlap >= 2:
        score += 4
        reasons.append("medium_name_overlap")
    elif overlap >= 1:
        score += 2
        reasons.append("weak_name_overlap")

    abbr = has_abbreviation_match(p, doc)
    if abbr:
        score += 4
        reasons.append(f"abbrev:{abbr}")

    # Curriculum URL equality/shortcut equality is very strong.
    doc_urls = {str(doc.get("document_url") or ""), str(doc.get("page_url") or ""), str(doc.get("file_url") or "")}
    prog_curricula = {
        str(p.get("curriculum_de_url") or ""),
        str(p.get("curriculum_fr_url") or ""),
        str(p.get("curriculum_en_url") or ""),
        str(p.get("curriculum_unspecified_url") or ""),
    }
    if any(u and u in prog_curricula for u in doc_urls):
        score += 6
        reasons.append("curriculum_url")

    # Generic docs should not attach on weak name evidence.
    if is_generic_document(doc) and overlap == 0 and not abbr:
        score -= 3
        reasons.append("generic_without_name")

    features["p_ects"] = p_ects
    features["d_ects_values"] = d_ects_values
    features["p_level"] = p_level
    features["d_level"] = d_level

    return score, reasons, features


def candidate_matches(programmes: list[dict[str, Any]], doc: dict[str, Any], threshold: int) -> list[tuple[int, list[str], dict[str, Any], int]]:
    out = []
    for idx, p in enumerate(programmes):
        score, reasons, features = base_score(p, doc)

        if score < threshold:
            continue

        # If ECTS exists, require exact ECTS match unless this is a generic doc
        # with very strong name/curriculum evidence.
        p_ects = features.get("p_ects")
        dvals = features.get("d_ects_values") or []
        if dvals and p_ects not in dvals:
            if not ("curriculum_url" in reasons or features.get("name_ratio", 0) >= 0.95):
                continue

        # Require some name/abbr/curriculum evidence, never faculty+level alone.
        if not any(
            r.startswith("exact_or_near_name")
            or r.startswith("strong_name_overlap")
            or r.startswith("medium_name_overlap")
            or r.startswith("abbrev:")
            or r == "curriculum_url"
            for r in reasons
        ):
            continue

        out.append((score, reasons, features, idx))

    return out


def select_matches_for_doc(programmes: list[dict[str, Any]], doc: dict[str, Any], threshold: int) -> list[tuple[int, list[str], int]]:
    matches = candidate_matches(programmes, doc, threshold)

    if not matches:
        return []

    dvals = ects_values(doc)

    if dvals:
        # Clear ECTS documents can attach to all matching programme variants with
        # explicit listed ECTS values.
        selected = [
            (score, reasons, idx)
            for score, reasons, features, idx in matches
            if features.get("p_ects") in dvals
        ]

        # If no explicit ECTS programme matched, fall back to best name evidence.
        if selected:
            max_score = max(s for s, _, _ in selected)
            # Keep close ties to allow bilingual/same-document variants.
            return [(s, r, i) for s, r, i in selected if s >= max_score - 2]

    # No document ECTS: attach only to the best matching programme variant(s).
    # Prefer highest ECTS among comparable same-name/level candidates because
    # generic docs usually describe the main programme.
    max_score = max(s for s, _, _, _ in matches)
    close = [(s, r, f, i) for s, r, f, i in matches if s >= max_score - 1]

    # Prefer main ECTS, then highest ECTS.
    def rank(item: tuple[int, list[str], dict[str, Any], int]) -> tuple[int, int, int]:
        score, reasons, features, idx = item
        p = programmes[idx]
        level = level_norm(p.get("level"))
        ects = programme_ects(p)
        main_bonus = 1 if is_main_ects_for_level(level, ects) else 0
        return (main_bonus, ects or -1, score)

    best_rank = max(rank(x) for x in close)
    selected4 = [x for x in close if rank(x) == best_rank]

    # Usually only one. If exact same programme appears in multiple languages/duplicates,
    # keep all exact ties.
    return [(s, r + ["selected_no_doc_ects_highest_main_ects"], i) for s, r, f, i in selected4]


def make_document_payload(doc: dict[str, Any], score: int, reasons: list[str]) -> dict[str, Any]:
    return {
        "url": doc.get("document_url"),
        "label": doc.get("document_label"),
        "source_type": "faculty_crawler",
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
        "match_score": score,
        "match_reasons": reasons,
    }


def attach_documents(
    programmes: list[dict[str, Any]],
    docs: list[dict[str, Any]],
    threshold: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    out = [dict(p) for p in programmes]
    attached_urls_by_programme = [set() for _ in out]
    audit: list[dict[str, Any]] = []
    unmatched_docs: list[dict[str, Any]] = []

    for d_idx, doc in enumerate(docs):
        selected = select_matches_for_doc(programmes, doc, threshold)

        if not selected:
            unmatched_docs.append(doc)
            continue

        for score, reasons, p_idx in selected:
            url = doc.get("document_url")
            if not url:
                continue

            if url in attached_urls_by_programme[p_idx]:
                continue

            attached_urls_by_programme[p_idx].add(url)
            out[p_idx].setdefault("documents", [])
            out[p_idx]["documents"].append(make_document_payload(doc, score, reasons))

            audit.append({
                "programme_index": p_idx,
                "programme": programmes[p_idx].get("programme"),
                "programme_name_en": programmes[p_idx].get("programme_name_en"),
                "programme_name_de": programmes[p_idx].get("programme_name_de"),
                "programme_name_fr": programmes[p_idx].get("programme_name_fr"),
                "programme_level": programmes[p_idx].get("level"),
                "programme_ects": programmes[p_idx].get("ects_points"),
                "programme_faculty": programmes[p_idx].get("faculty"),
                "document_index": d_idx,
                "document_program_name": doc.get("program_name"),
                "document_label": doc.get("document_label"),
                "document_url": url,
                "document_ects_values": doc.get("ects_values") or [],
                "score": score,
                "reasons": reasons,
            })

    for p in out:
        docs_attached = p.get("documents") or []
        docs_attached.sort(key=lambda d: (-(d.get("match_score") or 0), str(d.get("label") or ""), str(d.get("url") or "")))
        p["document_match_count"] = len(docs_attached)
        p["matched_sources"] = ["faculty_documents_normalized"] if docs_attached else []

        if len(docs_attached) > 15:
            p["document_match_warning"] = "many_documents_attached_check_manually"

    return out, audit, unmatched_docs


def print_stats(output: list[dict[str, Any]], docs: list[dict[str, Any]], audit: list[dict[str, Any]], unmatched_docs: list[dict[str, Any]]) -> None:
    programmes_with_docs = sum(1 for p in output if p.get("documents"))
    zero_docs = len(output) - programmes_with_docs
    many_docs = sum(1 for p in output if len(p.get("documents") or []) > 15)

    print(f"Programmes: {len(output)}")
    print(f"Input documents: {len(docs)}")
    print(f"Document-programme attachments: {len(audit)}")
    print(f"Programmes with docs: {programmes_with_docs}")
    print(f"Programmes with zero docs: {zero_docs}")
    print(f"Programmes with >15 docs: {many_docs}")
    print(f"Unmatched document rows: {len(unmatched_docs)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--programmes", required=True)
    parser.add_argument("--docs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--audit-out")
    parser.add_argument("--unmatched-docs-out")
    parser.add_argument("--threshold", type=int, default=9)
    args = parser.parse_args()

    programmes = load_json(Path(args.programmes))
    docs = load_json(Path(args.docs))

    if not isinstance(programmes, list):
        raise ValueError("--programmes must be a JSON list")
    if not isinstance(docs, list):
        raise ValueError("--docs must be a JSON list")

    output, audit, unmatched_docs = attach_documents(programmes, docs, args.threshold)

    save_json(Path(args.out), output)
    if args.audit_out:
        save_json(Path(args.audit_out), audit)
    if args.unmatched_docs_out:
        save_json(Path(args.unmatched_docs_out), unmatched_docs)

    print(f"Wrote {len(output)} programmes to {args.out}")
    if args.audit_out:
        print(f"Wrote audit to {args.audit_out}")
    if args.unmatched_docs_out:
        print(f"Wrote unmatched docs to {args.unmatched_docs_out}")
    print_stats(output, docs, audit, unmatched_docs)


if __name__ == "__main__":
    main()
