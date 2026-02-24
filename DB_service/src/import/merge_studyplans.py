#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ----------------------------
# IO helpers
# ----------------------------
def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ----------------------------
# Normalization helpers
# ----------------------------
def strip_accents(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(ch for ch in s if not unicodedata.combining(ch))


# Small conservative synonym mapping (token-level)
SYNONYM_MAP = {
    "humanmedizin": "medizin",
    "médecinehumaine": "médecine",
    "medecinehumaine": "medecine",
    "humanmedicine": "medicine",
    # Optional:
    # "bewegungswissenschaften": "sportwissenschaften",
}


def apply_synonyms(normed: str) -> str:
    toks = normed.split()
    toks2 = [SYNONYM_MAP.get(t, t) for t in toks]
    return " ".join(toks2).strip()


def norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("&", " and ")
    s = strip_accents(s)
    s = re.sub(r"[’'`]", "", s)

    # small typo normalization you had
    s = s.replace("umwelgeistes", "umweltgeistes")

    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    s = apply_synonyms(s)
    return s


# ----------------------------
# Category/level normalization
# ----------------------------
def norm_level(v: Any) -> Optional[str]:
    """
    Normalize to one of: 'B', 'M', 'D'
    """
    if v is None:
        return None
    s = norm_text(str(v))
    if not s:
        return None
    if s in {"b", "ba", "bachelor", "bachelors", "bsc"}:
        return "B"
    if s in {"m", "ma", "master", "masters", "msc", "spmsc", "commsc", "premsc"}:
        return "M"
    if s in {"d", "dr", "doctorate", "doctorat", "doktorat", "phd"}:
        return "D"
    if str(v).upper() in {"B", "M", "D"}:
        return str(v).upper()
    return None


def infer_level_from_label(text: str) -> Optional[str]:
    t = norm_text(text or "")
    if not t:
        return None
    if any(x in t for x in ["doctorat", "doktorat", "doctorate", "phd"]):
        return "D"
    if any(x in t for x in ["master", "msc", "spmsc", "commsc", "premsc"]):
        return "M"
    if any(x in t for x in ["bachelor", "bsc"]):
        return "B"
    return None


# ----------------------------
# Programme name keys (less strict + EDU phrases)
# ----------------------------
NAME_PREFIXES = {
    "hauptfach",
    "nebenfach",
    "zusatzfach",
    "zusatzfacher",
    "zusatzfaecher",
    "zusatzfächer",
    "major",
    "minor",
    "fach",
    "bachelor",
    "master",
    "doktorat",
    "doctorat",
    "doctorate",
    "studienplan",
    "plan",
    "etudes",
    "études",
    "study",
    "ects",
    "kreditpunkte",
    "kreditpunkten",
    "of",
    "in",
}

SOFT_STOP_TOKENS = {
    # DE
    "ausbildung",
    "fur",
    "fuer",
    "für",
    "den",
    "die",
    "das",
    "unterricht",
    "an",
    "auf",
    "maturitatsschulen",
    "maturitätsschulen",
    "sekundarstufe",
    "primarstufe",
    # FR
    "formation",
    "a",
    "à",
    "lenseignement",
    "enseignement",
    "pour",
    "les",
    "ecoles",
    "écoles",
    "de",
    "du",
    "des",
    "maturite",
    "maturité",
    "degre",
    "degré",
    "secondaire",
    "primaire",
    # EN
    "teacher",
    "education",
    "for",
    "schools",
    "secondary",
    "primary",
    "level",
    "baccalaureate",
    # generic
    "sciences",
    "science",
    "arts",
    "and",
}

DOC_LABEL_PREFIX_RE = re.compile(
    r"^(studienplan|study plan|plan d['’]etudes|plan d’etudes|plan d'etudes|"
    r"brosch(ü|u)re|brochure|reglement|règlement|ordnung|regulations?)\b",
    re.IGNORECASE,
)


def split_variants_on_separators(s: str) -> list[str]:
    s = (s or "").strip()
    if not s:
        return []
    parts = re.split(r"\s*/\s*|\s*-\s*|\s*:\s*", s)
    parts = [p.strip() for p in parts if p.strip()]
    out = [s] + parts
    seen = set()
    uniq_out = []
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq_out.append(p)
    return uniq_out


def reduce_soft_tokens(key: str) -> str:
    toks = key.split()
    toks2 = [t for t in toks if t not in SOFT_STOP_TOKENS]
    return " ".join(toks2).strip()


def canonical_name_keys(name: str) -> List[str]:
    full0 = norm_text(name)
    if not full0:
        return []

    keys: List[str] = []

    for variant in split_variants_on_separators(full0):
        full = norm_text(variant)
        if not full:
            continue

        toks = full.split()
        keys.append(full)

        # strip leading prefixes
        i = 0
        while i < len(toks) and toks[i] in NAME_PREFIXES:
            i += 1
        stripped = " ".join(toks[i:]).strip()
        if stripped and stripped != full:
            keys.append(stripped)

        # soft-token reduced
        rf = reduce_soft_tokens(full)
        if rf and rf != full:
            keys.append(rf)
        if stripped:
            rs = reduce_soft_tokens(stripped)
            if rs and rs != stripped:
                keys.append(rs)

        if len(toks) >= 2:
            keys.append(" ".join(toks[:2]))
        keys.append(toks[0])

    seen = set()
    out: List[str] = []
    for k in keys:
        k = (k or "").strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ----------------------------
# ECTS helpers
# ----------------------------
ECTS_ANY_RE = re.compile(
    r"\b(\d{2,3})(?:\s*\+\s*(\d{2,3}))?\s*(ects|kreditpunkte|credits?)\b",
    re.IGNORECASE,
)


def extract_ects_list_from_text(text: str) -> List[int]:
    if not text:
        return []
    m = ECTS_ANY_RE.search(text)
    if not m:
        return []
    out: List[int] = []
    try:
        out.append(int(m.group(1)))
    except Exception:
        pass
    if m.group(2):
        try:
            out.append(int(m.group(2)))
        except Exception:
            pass
    seen = set()
    uniq_out = []
    for v in out:
        if v not in seen:
            seen.add(v)
            uniq_out.append(v)
    return uniq_out


def get_rec_ects(rec: Dict[str, Any]) -> Optional[int]:
    v = rec.get("ects_points")
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    for k in ("ects", "ECTS", "credits", "credit_points", "kreditpunkte"):
        v = rec.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.strip().isdigit():
            return int(v.strip())
    return None


def programme_name_variants(rec: Dict[str, Any]) -> List[str]:
    keys = [
        "title",
        "programme_name_de",
        "programme_name_fr",
        "programme_name_en",
        "programme",
        "program_clean",
        "program_base_clean",
        "program_short_clean",
    ]
    names: List[str] = []
    for k in keys:
        v = rec.get(k)
        if isinstance(v, str) and v.strip():
            names.append(v.strip())

    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def ects_bucket_for_name(name: str, *, level: Optional[str], ects_value: Optional[int]) -> Optional[int]:
    if "hauptfach" not in norm_text(name):
        return ects_value

    if level == "M":
        if ects_value is not None and ects_value >= 90:
            return ects_value
        return 90

    if level == "B":
        if ects_value is not None and ects_value >= 120:
            return ects_value
        return 120

    return ects_value


# ----------------------------
# Fuzzy helper
# ----------------------------
def best_fuzzy_key(target_key: str, all_keys: List[str], *, limit: int = 8000) -> Tuple[Optional[str], float]:
    toks = set(target_key.split())
    if not toks:
        return None, 0.0

    candidates = [k for k in all_keys if toks.intersection(k.split())]
    if not candidates:
        candidates = all_keys[:limit]

    best_k, best_score = None, 0.0
    for ck in candidates[:limit]:
        score = difflib.SequenceMatcher(None, target_key, ck).ratio()
        if score > best_score:
            best_k, best_score = ck, score
    return best_k, best_score


# ----------------------------
# Input file collection
# ----------------------------
def iter_input_files(inputs: List[str], input_dir: Optional[str]) -> List[Path]:
    files: List[Path] = [Path(p) for p in inputs]
    if input_dir:
        d = Path(input_dir)
        if d.exists():
            files.extend(sorted([p for p in d.glob("*.json") if p.is_file()]))

    seen = set()
    uniq_files: List[Path] = []
    for p in files:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq_files.append(p)
    return uniq_files


# ----------------------------
# Doc filtering (used AFTER matching)
# ----------------------------
STOPWORDS = {
    "hauptfach",
    "minor",
    "major",
    "master",
    "bachelor",
    "mono",
    "studienplan",
    "plan",
    "detudes",
    "d etudes",
    "etudes",
    "études",
    "study",
    "reglement",
    "regulations",
    "ordnung",
    "ects",
    "kreditpunkte",
    "kreditpunkten",
    "fach",
    "zusatzfach",
    "zusatzfächer",
    "einleitung",
    "introduction",
    "intro",
    "uebergang",
    "übergang",
    "uebergangsregelung",
    "übergangsregelung",
}


def tokens_for_match(s: str) -> set[str]:
    s = norm_text(s)
    return {t for t in s.split() if t and t not in STOPWORDS and len(t) > 2}


def filter_docs_for_programme(
    docs: List[Dict[str, Any]],
    programme_name: str,
    *,
    min_token_overlap: int = 1,
    fuzzy_label_threshold: float = 0.65,
) -> List[Dict[str, Any]]:
    pname = (programme_name or "").strip()
    if not pname:
        return docs

    ptoks = tokens_for_match(pname)
    if not ptoks:
        return docs

    kept: List[Dict[str, Any]] = []
    for d in docs:
        if not isinstance(d, dict):
            continue
        label = d.get("label") or ""
        u = d.get("url") or ""
        text = f"{label} {u}"
        dtoks = tokens_for_match(text)

        overlap = len(ptoks.intersection(dtoks))
        fuzzy_ok = False
        if label:
            score = difflib.SequenceMatcher(None, norm_text(pname), norm_text(label)).ratio()
            fuzzy_ok = score >= fuzzy_label_threshold

        if overlap >= min_token_overlap or fuzzy_ok:
            kept.append(d)

    return kept if kept else docs


_MINOR_HINT_RE = re.compile(
    r"\b(minor|nebenfach|branche\s*secondaire|extension|zusatz|plus\s*30|\+\s*30)\b",
    re.IGNORECASE,
)
_MAJOR_HINT_RE = re.compile(r"\b(major|hauptfach)\b", re.IGNORECASE)


def _doc_text(doc: Dict[str, Any]) -> str:
    return f"{doc.get('label','')} {doc.get('url','')}"


def doc_seems_minor(doc: Dict[str, Any]) -> bool:
    text = _doc_text(doc)
    if _MINOR_HINT_RE.search(text):
        return True
    # Calameo "read" pages are often minors in SES; label may not contain "Nebenfach"
    if "calameo.com/read/" in (text or "").lower():
        return True
    return False


def doc_seems_major(doc: Dict[str, Any]) -> bool:
    text = _doc_text(doc)
    return bool(_MAJOR_HINT_RE.search(text))


def candidate_is_minor_context(c: Dict[str, Any]) -> bool:
    """
    Decide if a matched candidate entry represents a minor bucket even if doc labels are generic.
    We rely on explicit 'track' or category-like fields we carry over from the normalized sources.
    """
    blob = " ".join(
        [
            str(c.get("track") or ""),
            str(c.get("category") or ""),
            str(c.get("program_group") or ""),
        ]
    ).lower()
    return any(x in blob for x in ["nebenfach", "minor", "branche secondaire", "zusatz", "plus30", "+30", "plus 30"])


# ----------------------------
# Parsing sources (IDENTITY names separate from doc labels)
# ----------------------------
def _dedup_strs(xs: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in xs:
        if not isinstance(x, str):
            continue
        x = x.strip()
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def extract_source_entries(source_name: str, data: Any) -> List[Dict[str, Any]]:
    """
    Standardize input into entries:
      { source, faculty, level, names[], doc_label_names[], ects_candidates[], documents[], track?, category? }

    Key safety change:
      - "names" are IDENTITY only (title + programme names + cleaned programme variants)
      - doc labels NEVER go into "names" (they go into doc_label_names and are only used as fallback)
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(data, list):
        return out

    for item in data:
        if not isinstance(item, dict):
            continue

        faculty = item.get("faculty") or item.get("faculty_canonical")
        base_level = norm_level(item.get("level") or item.get("category") or item.get("programme_level"))

        # carry minor context through
        track = item.get("track")
        category = item.get("category")
        program_group = item.get("program_group")

        # ---- identity names ----
        id_names: List[str] = []

        if isinstance(item.get("title"), str) and item["title"].strip():
            id_names.append(item["title"].strip())

        for k in (
            "programme",
            "programme_name_de",
            "programme_name_fr",
            "programme_name_en",
            "program_clean",
            "program_base_clean",
            "program_short_clean",
        ):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                id_names.append(v.strip())

        prog = item.get("program")
        if isinstance(prog, dict):
            for k in ("name_de", "name_fr", "name_en", "name_it", "name"):
                v = prog.get(k)
                if isinstance(v, str) and v.strip():
                    id_names.append(v.strip())
            base_level = base_level or norm_level(prog.get("level") or prog.get("category"))

        if isinstance(item.get("name_variants"), list):
            for v in item["name_variants"]:
                if not (isinstance(v, str) and v.strip()):
                    continue
                vv = v.strip()
                if DOC_LABEL_PREFIX_RE.search(vv):
                    continue
                id_names.append(vv)

        id_names = _dedup_strs(id_names)

        # ---- documents + doc label names ----
        docs: List[Dict[str, Any]] = []
        doc_label_names: List[str] = []
        if isinstance(item.get("documents"), list):
            for d in item["documents"]:
                if isinstance(d, dict) and (d.get("url") or d.get("label")):
                    docs.append(d)
                    if isinstance(d.get("label"), str) and d["label"].strip():
                        doc_label_names.append(d["label"].strip())

        doc_label_names = _dedup_strs(doc_label_names)

        # ---- Split into per-doc entries when doc provides ects/level ----
        doc_based_entries: List[Dict[str, Any]] = []
        for d in docs:
            label = d.get("label") or ""
            url = d.get("url") or ""

            d_level = infer_level_from_label(label) or infer_level_from_label(url) or base_level
            ects_list = extract_ects_list_from_text(label) or extract_ects_list_from_text(url)

            if ects_list:
                for ev in ects_list:
                    doc_based_entries.append(
                        {
                            "source": source_name,
                            "faculty": faculty,
                            "level": d_level,
                            "names": id_names,
                            "doc_label_names": doc_label_names,
                            "ects_candidates": [ev],
                            "documents": [d],
                            "track": track,
                            "category": category,
                            "program_group": program_group,
                        }
                    )

        if doc_based_entries:
            out.extend(doc_based_entries)

            ects_set = set()
            for e in doc_based_entries:
                for ev in e.get("ects_candidates", []) or []:
                    ects_set.add(ev)

            out.append(
                {
                    "source": source_name,
                    "faculty": faculty,
                    "level": base_level,
                    "names": id_names,
                    "doc_label_names": doc_label_names,
                    "ects_candidates": sorted(ects_set),
                    "documents": docs,
                    "track": track,
                    "category": category,
                    "program_group": program_group,
                }
            )
            continue

        # ---- Non-splittable ----
        ects_set = set()

        for n in id_names:
            for ev in extract_ects_list_from_text(n):
                ects_set.add(ev)

        src_ects = item.get("ects")
        if isinstance(src_ects, int):
            ects_set.add(src_ects)
        elif isinstance(src_ects, str) and src_ects.strip().isdigit():
            ects_set.add(int(src_ects.strip()))

        if isinstance(item.get("ects_candidates"), list):
            for ev in item["ects_candidates"]:
                if isinstance(ev, int):
                    ects_set.add(ev)
                elif isinstance(ev, str) and ev.strip().isdigit():
                    ects_set.add(int(ev.strip()))

        out.append(
            {
                "source": source_name,
                "faculty": faculty,
                "level": base_level,
                "names": id_names,
                "doc_label_names": doc_label_names,
                "ects_candidates": sorted(ects_set),
                "documents": docs,
                "track": track,
                "category": category,
                "program_group": program_group,
            }
        )

    return out


# ----------------------------
# Build indices
# ----------------------------
IndexKey = Tuple[Optional[str], str, Optional[int]]  # (level, name_key, ects)


def build_indices(
    source_entries: List[Dict[str, Any]],
) -> Tuple[Dict[IndexKey, List[Dict[str, Any]]], Dict[IndexKey, List[Dict[str, Any]]]]:
    idx_id: Dict[IndexKey, List[Dict[str, Any]]] = {}
    idx_doc: Dict[IndexKey, List[Dict[str, Any]]] = {}

    def add_to_index(idx: Dict[IndexKey, List[Dict[str, Any]]], e: Dict[str, Any], names: List[str]) -> None:
        level = e.get("level")
        ects_cands: List[int] = e.get("ects_candidates", []) or []

        for n in names:
            for nk in canonical_name_keys(n):
                if not nk:
                    continue

                if ects_cands:
                    for ev in ects_cands:
                        idx.setdefault((level, nk, ev), []).append(e)
                        idx.setdefault((None, nk, ev), []).append(e)
                else:
                    idx.setdefault((level, nk, None), []).append(e)
                    idx.setdefault((None, nk, None), []).append(e)

    for e in source_entries:
        add_to_index(idx_id, e, e.get("names", []) or [])
        add_to_index(idx_doc, e, e.get("doc_label_names", []) or [])

    return idx_id, idx_doc


# ----------------------------
# Matching helper
# ----------------------------
def find_candidates_for_record(
    rec_name_keys: List[str],
    *,
    level: Optional[str],
    bucket: Optional[int],
    index: Dict[IndexKey, List[Dict[str, Any]]],
    all_name_keys: List[str],
    fuzzy_threshold: float,
) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
    candidates: List[Dict[str, Any]] = []
    match_type: Optional[str] = None
    chosen_name_for_docs: Optional[str] = rec_name_keys[0] if rec_name_keys else None

    # -------- EXACT MATCHES --------
    if bucket is not None:
        for nk in rec_name_keys:
            key = (level, nk, bucket)
            if key in index:
                candidates = index[key]
                match_type = "exact_level_name_ects"
                chosen_name_for_docs = nk
                return candidates, match_type, chosen_name_for_docs

    for nk in rec_name_keys:
        key = (level, nk, None)
        if key in index:
            candidates = index[key]
            match_type = "exact_level_name"
            chosen_name_for_docs = nk
            return candidates, match_type, chosen_name_for_docs

    if bucket is not None:
        for nk in rec_name_keys:
            key = (None, nk, bucket)
            if key in index:
                candidates = index[key]
                match_type = "exact_name_ects"
                chosen_name_for_docs = nk
                return candidates, match_type, chosen_name_for_docs

    for nk in rec_name_keys:
        key = (None, nk, None)
        if key in index:
            candidates = index[key]
            match_type = "exact_name"
            chosen_name_for_docs = nk
            return candidates, match_type, chosen_name_for_docs

    # -------- FUZZY MATCHES --------
    best_bk: Optional[str] = None
    best_score: float = 0.0
    best_rec_key: Optional[str] = None

    for nk in rec_name_keys:
        bk, score = best_fuzzy_key(nk, all_name_keys)
        if bk and score > best_score:
            best_bk, best_score, best_rec_key = bk, score, nk

    if best_bk and best_score >= fuzzy_threshold:
        if bucket is not None and (level, best_bk, bucket) in index:
            candidates = index[(level, best_bk, bucket)]
            match_type = "fuzzy_level_name_ects"
        elif (level, best_bk, None) in index:
            candidates = index[(level, best_bk, None)]
            match_type = "fuzzy_level_name"
        elif bucket is not None and (None, best_bk, bucket) in index:
            candidates = index[(None, best_bk, bucket)]
            match_type = "fuzzy_name_ects"
        elif (None, best_bk, None) in index:
            candidates = index[(None, best_bk, None)]
            match_type = "fuzzy_name"

        if best_rec_key:
            chosen_name_for_docs = best_rec_key

    return candidates, match_type, chosen_name_for_docs


def token_overlap_ok(base_variants: List[str], candidate_entry: Dict[str, Any], *, min_overlap: int = 1) -> bool:
    base_text = " ".join([v for v in base_variants if isinstance(v, str)])
    btoks = tokens_for_match(base_text)

    cand_names = candidate_entry.get("names", []) or []
    cand_text = " ".join([v for v in cand_names if isinstance(v, str)])
    ctoks = tokens_for_match(cand_text)

    return len(btoks.intersection(ctoks)) >= min_overlap


# ----------------------------
# Merge
# ----------------------------
def merge(
    base: List[Dict[str, Any]],
    idx_id: Dict[IndexKey, List[Dict[str, Any]]],
    idx_doc: Dict[IndexKey, List[Dict[str, Any]]],
    *,
    fuzzy_threshold: float = 0.84,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    stats = {
        "ignored_doctorates": 0,
        # identity matches
        "matched_id_exact_level_name_ects": 0,
        "matched_id_exact_level_name": 0,
        "matched_id_exact_name_ects": 0,
        "matched_id_exact_name": 0,
        "matched_id_fuzzy_level_name_ects": 0,
        "matched_id_fuzzy_level_name": 0,
        "matched_id_fuzzy_name_ects": 0,
        "matched_id_fuzzy_name": 0,
        # doc fallback matches
        "matched_doc_exact_level_name_ects": 0,
        "matched_doc_exact_level_name": 0,
        "matched_doc_exact_name_ects": 0,
        "matched_doc_exact_name": 0,
        "matched_doc_fuzzy_level_name_ects": 0,
        "matched_doc_fuzzy_level_name": 0,
        "matched_doc_fuzzy_name_ects": 0,
        "matched_doc_fuzzy_name": 0,
        # other
        "unmatched": 0,
        "docs_filtered": 0,
        "doc_candidates_rejected_by_gate": 0,
    }

    all_name_keys_id = sorted({k[1] for k in idx_id.keys()})
    all_name_keys_doc = sorted({k[1] for k in idx_doc.keys()})

    merged: List[Dict[str, Any]] = []

    def bump(match_type: str, prefix: str) -> None:
        key = f"matched_{prefix}_{match_type}"
        if key in stats:
            stats[key] += 1

    for rec in base:
        if norm_level(rec.get("level")) == "D" or rec.get("level") == "D":
            stats["ignored_doctorates"] += 1
            merged.append(rec)
            continue

        new_rec = dict(rec)

        level = norm_level(rec.get("level"))
        variants = programme_name_variants(rec)
        programme_fallback = rec.get("programme") or ""
        chosen_name_for_docs = variants[0] if variants else programme_fallback

        base_ects_raw = get_rec_ects(rec)

        bucket: Optional[int] = None
        for v in variants:
            b = ects_bucket_for_name(v, level=level, ects_value=base_ects_raw)
            if b is not None:
                bucket = b
                break

        rec_name_keys: List[str] = []
        for v in variants:
            rec_name_keys.extend(canonical_name_keys(v))

        seen = set()
        deduped = []
        for k in rec_name_keys:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        rec_name_keys = deduped

        candidates: List[Dict[str, Any]] = []
        match_type: Optional[str] = None

        # -------- 1) IDENTITY-FIRST matching --------
        cand1, mt1, chosen1 = find_candidates_for_record(
            rec_name_keys,
            level=level,
            bucket=bucket,
            index=idx_id,
            all_name_keys=all_name_keys_id,
            fuzzy_threshold=fuzzy_threshold,
        )
        if cand1:
            candidates, match_type, chosen_name_for_docs = cand1, mt1, (chosen1 or chosen_name_for_docs)
            if match_type:
                bump(match_type, "id")
        else:
            # -------- 2) DOC-LABEL fallback matching + confirmation gate --------
            cand2, mt2, chosen2 = find_candidates_for_record(
                rec_name_keys,
                level=level,
                bucket=bucket,
                index=idx_doc,
                all_name_keys=all_name_keys_doc,
                fuzzy_threshold=fuzzy_threshold,
            )
            if cand2:
                gated = [c for c in cand2 if token_overlap_ok(variants, c, min_overlap=1)]
                if not gated:
                    stats["doc_candidates_rejected_by_gate"] += 1
                else:
                    candidates, match_type, chosen_name_for_docs = gated, mt2, (chosen2 or chosen_name_for_docs)
                    if match_type:
                        bump(match_type, "doc")

        if candidates:
            faculties = sorted({c.get("faculty") for c in candidates if c.get("faculty")})
            sources = sorted({c.get("source") for c in candidates if c.get("source")})

            docs: List[Dict[str, Any]] = []
            for c in candidates:
                docs.extend(c.get("documents", []))

            before_n = len(docs)
            docs = filter_docs_for_programme(docs, chosen_name_for_docs, min_token_overlap=1)
            after_n = len(docs)
            if after_n < before_n:
                stats["docs_filtered"] += 1

            # de-dup docs by url else label
            seen = set()
            docs_dedup: List[Dict[str, Any]] = []
            for d in docs:
                if not isinstance(d, dict):
                    continue
                ident = d.get("url") or d.get("label") or repr(d)
                if ident not in seen:
                    seen.add(ident)
                    docs_dedup.append(d)

            # ---- Master 30/60 safeguard (refined) ----
            # Previous behavior dropped everything unless doc label explicitly said "minor/nebenfach".
            # For SES minors and Calameo HTML pages, labels are often just the programme name.
            base_ects = get_rec_ects(rec)
            if level == "M" and base_ects in {30, 60}:
                # If candidates are minor context, keep docs unless they look like MAJOR docs
                if any(candidate_is_minor_context(c) for c in candidates):
                    docs_dedup = [d for d in docs_dedup if not doc_seems_major(d)]
                else:
                    # Legacy behavior: keep only clearly minor-marked docs
                    minor_docs = [d for d in docs_dedup if doc_seems_minor(d)]
                    docs_dedup = minor_docs if minor_docs else []

            new_rec["faculty"] = faculties[0] if faculties else None
            new_rec["faculties"] = faculties
            new_rec["documents"] = docs_dedup
            new_rec["matched_sources"] = sources
            new_rec["match_type"] = match_type or "matched"
        else:
            stats["unmatched"] += 1
            new_rec["faculty"] = None
            new_rec["faculties"] = []
            new_rec["documents"] = []
            new_rec["matched_sources"] = []
            new_rec["match_type"] = "unmatched"

        merged.append(new_rec)

    return merged, stats


# ----------------------------
# Main
# ----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--inputs", nargs="*", default=[])
    ap.add_argument("--input-dir", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fuzzy-threshold", type=float, default=0.84)
    args = ap.parse_args()

    base = load_json(Path(args.base))
    if not isinstance(base, list):
        raise ValueError("Base JSON must be a list")

    input_files = iter_input_files(args.inputs, args.input_dir)
    if not input_files:
        raise ValueError("No input files found")

    source_entries: List[Dict[str, Any]] = []
    for fp in input_files:
        data = load_json(fp)
        source_entries.extend(extract_source_entries(fp.stem, data))

    idx_id, idx_doc = build_indices(source_entries)

    merged, stats = merge(
        base,
        idx_id,
        idx_doc,
        fuzzy_threshold=args.fuzzy_threshold,
    )

    save_json(Path(args.out), merged)

    print("Done.")
    print("Output:", args.out)
    print("Stats:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())