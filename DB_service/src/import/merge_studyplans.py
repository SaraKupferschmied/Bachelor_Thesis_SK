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
    # Optional (uncomment if it helps your dataset)
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
    # edu labels: "Master Major", "MSc ..."
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

# tokens that appear in programme names but often not in doc titles (teacher education, etc.)
SOFT_STOP_TOKENS = {
    # DE
    "ausbildung", "fur", "fuer", "für",
    "den", "die", "das",
    "unterricht",
    "an", "auf",
    "maturitatsschulen", "maturitätsschulen",
    "sekundarstufe",
    "primarstufe",
    # FR
    "formation", "a", "à", "lenseignement", "enseignement",
    "pour", "les", "ecoles", "écoles", "de", "du", "des",
    "maturite", "maturité",
    "degre", "degré",
    "secondaire", "primaire",
    # EN
    "teacher", "education", "for", "schools", "secondary", "primary", "level", "baccalaureate",
    # generic
    "sciences", "science", "arts", "and",
}


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
    """
    Generate multiple keys:
      - full
      - prefix-stripped
      - soft-token-reduced versions
      - first token / first 2 tokens
      - separator-split variants
    """
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

        # very short heads help SCIMED long slash names + Medizin/Humanmedizin
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
# more permissive: capture "90+30 ECTS" too
ECTS_ANY_RE = re.compile(r"\b(\d{2,3})(?:\s*\+\s*(\d{2,3}))?\s*(ects|kreditpunkte|credits?)\b", re.IGNORECASE)

def extract_ects_list_from_text(text: str) -> List[int]:
    """
    Returns list of ects values found in e.g.
      "Master Major + Minor (90+30 ECTS-Kreditpunkte)" -> [90, 30]
      "Bachelor Minor (60 ECTS-Kreditpunkte)" -> [60]
    """
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
    # de-dup preserve
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
    """
    Fix your earlier bug:
      - do NOT force 120 always for 'Hauptfach'
      - For Bachelor: 'Hauptfach' implies >=120 (prefer record ects if >=120 else 120)
      - For Master:   'Hauptfach' implies >=90  (prefer record ects if >=90  else 90)
    If name doesn't contain hauptfach, return ects_value as-is.
    """
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

    # unknown level: don't force hard, but prefer existing ects, else None
    return ects_value


# ----------------------------
# Fuzzy helper (unchanged conceptually)
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
# Doc filtering (kept, but now less critical because docs are split by ects+level)
# ----------------------------
STOPWORDS = {
    "hauptfach", "minor", "major", "master", "bachelor", "mono",
    "studienplan", "plan", "d'etudes", "etudes", "études", "study",
    "reglement", "regulations", "ordnung",
    "ects", "kreditpunkte", "kreditpunkten",
    "fach", "zusatzfach", "zusatzfächer",
    "einleitung", "introduction", "intro",
    "uebergang", "übergang", "uebergangsregelung", "übergangsregelung",
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


# ----------------------------
# Parsing sources (IMPORTANT: split entries by doc-level+ects)
# ----------------------------
def extract_source_entries(source_name: str, data: Any) -> List[Dict[str, Any]]:
    """
    Standardize faculty input into entries:
      { source, faculty, level, names[], ects_candidates[], documents[] }

    NEW:
      - If an item contains multiple docs with different ECTS/levels, we split into per-doc entries:
          level inferred from label, ects inferred from label (supports 90+30)
      - This is crucial for EDUFORM buckets (Erziehungswissenschaften, Pädagogik/Psychologie, LDS/LDM, etc.)
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(data, list):
        return out

    for item in data:
        if not isinstance(item, dict):
            continue

        faculty = item.get("faculty") or item.get("faculty_canonical")
        base_level = norm_level(item.get("level") or item.get("category") or item.get("programme_level"))

        # Collect base name variants (programme identity)
        names: List[str] = []
        if isinstance(item.get("title"), str) and item["title"]:
            names.append(item["title"])

        if isinstance(item.get("name_variants"), list):
            for v in item["name_variants"]:
                if isinstance(v, str) and v:
                    names.append(v)

        if isinstance(item.get("program"), str) and item["program"]:
            names.append(item["program"])

        prog = item.get("program")
        if isinstance(prog, dict):
            for k in ("name_de", "name_fr", "name_en", "name_it", "name"):
                if isinstance(prog.get(k), str) and prog.get(k):
                    names.append(prog[k])
            base_level = base_level or norm_level(prog.get("level") or prog.get("category"))

        # Collect docs
        docs: List[Dict[str, Any]] = []
        if isinstance(item.get("documents"), list):
            docs.extend([d for d in item["documents"] if isinstance(d, dict) and (d.get("url") or d.get("label"))])

        # De-dup names (raw)
        seen = set()
        deduped = []
        for n in names:
            n = n.strip()
            if n and n not in seen:
                seen.add(n)
                deduped.append(n)
        names = deduped

        # --- NEW: split into per-doc entries when doc label provides ects/level ---
        doc_based_entries: List[Dict[str, Any]] = []

        for d in docs:
            label = d.get("label") or ""
            url = d.get("url") or ""

            d_level = infer_level_from_label(label) or infer_level_from_label(url) or base_level
            ects_list = extract_ects_list_from_text(label) or extract_ects_list_from_text(url)

            # If we got at least one ects, create entries keyed by each ects (important for 90+30)
            if ects_list:
                for ev in ects_list:
                    doc_based_entries.append(
                        {
                            "source": source_name,
                            "faculty": faculty,
                            "level": d_level,
                            "names": names,
                            "ects_candidates": [ev],
                            "documents": [d],
                        }
                    )

        # If we created doc-based entries, use them and ALSO add a fallback bucket entry
        # (bucket entry keeps all docs; helpful when base programme has no ects)
        if doc_based_entries:
            out.extend(doc_based_entries)

            # fallback bucket entry (ects_candidates aggregated)
            ects_set = set()
            for e in doc_based_entries:
                for ev in e["ects_candidates"]:
                    ects_set.add(ev)
            out.append(
                {
                    "source": source_name,
                    "faculty": faculty,
                    "level": base_level,
                    "names": names,
                    "ects_candidates": sorted(ects_set),
                    "documents": docs,
                }
            )
            continue

        # --- OLD behavior for non-splittable items ---
        ects_set = set()

        # from names
        for n in names:
            for ev in extract_ects_list_from_text(n):
                ects_set.add(ev)

        # from item ects
        src_ects = item.get("ects")
        if isinstance(src_ects, int):
            ects_set.add(src_ects)
        elif isinstance(src_ects, str) and src_ects.strip().isdigit():
            ects_set.add(int(src_ects.strip()))

        # from ects_candidates
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
                "names": names,
                "ects_candidates": sorted(ects_set),
                "documents": docs,
            }
        )

    return out


# ----------------------------
# Build indices (LEVEL + NAME + ECTS)
# ----------------------------
IndexKey = Tuple[Optional[str], str, Optional[int]]  # (level, name_key, ects)

def build_indices(source_entries: List[Dict[str, Any]]) -> Dict[IndexKey, List[Dict[str, Any]]]:
    idx: Dict[IndexKey, List[Dict[str, Any]]] = {}

    for e in source_entries:
        level = e.get("level")  # 'B'/'M'/'D'/None
        ects_cands: List[int] = e.get("ects_candidates", []) or []
        names: List[str] = e.get("names", []) or []

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

    return idx


# ----------------------------
# Merge
# ----------------------------
def merge(
    base: List[Dict[str, Any]],
    index: Dict[IndexKey, List[Dict[str, Any]]],
    *,
    fuzzy_threshold: float = 0.84,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    stats = {
        "ignored_doctorates": 0,
        "matched_exact_level_name_ects": 0,
        "matched_exact_level_name": 0,
        "matched_exact_name_ects": 0,
        "matched_exact_name": 0,
        "matched_fuzzy_level_name_ects": 0,
        "matched_fuzzy_level_name": 0,
        "matched_fuzzy_name_ects": 0,
        "matched_fuzzy_name": 0,
        "unmatched": 0,
        "docs_filtered": 0,
    }

    all_name_keys = sorted({k[1] for k in index.keys()})
    merged: List[Dict[str, Any]] = []

    for rec in base:
        # keep your behavior: ignore D records
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

        # bucket: respect level thresholds for 'Hauptfach'
        bucket: Optional[int] = None
        for v in variants:
            b = ects_bucket_for_name(v, level=level, ects_value=base_ects_raw)
            if b is not None:
                bucket = b
                break

        candidates: List[Dict[str, Any]] = []
        match_type: Optional[str] = None

        # Candidate name keys for base record
        rec_name_keys: List[str] = []
        for v in variants:
            rec_name_keys.extend(canonical_name_keys(v))

        # de-dup
        seen = set()
        deduped = []
        for k in rec_name_keys:
            if k not in seen:
                seen.add(k)
                deduped.append(k)
        rec_name_keys = deduped

        # -------- EXACT MATCHES --------
        if bucket is not None:
            for nk in rec_name_keys:
                key = (level, nk, bucket)
                if key in index:
                    candidates = index[key]
                    match_type = "exact_level_name_ects"
                    stats["matched_exact_level_name_ects"] += 1
                    chosen_name_for_docs = nk
                    break

        if not candidates:
            for nk in rec_name_keys:
                key = (level, nk, None)
                if key in index:
                    candidates = index[key]
                    match_type = "exact_level_name"
                    stats["matched_exact_level_name"] += 1
                    chosen_name_for_docs = nk
                    break

        if not candidates and bucket is not None:
            for nk in rec_name_keys:
                key = (None, nk, bucket)
                if key in index:
                    candidates = index[key]
                    match_type = "exact_name_ects"
                    stats["matched_exact_name_ects"] += 1
                    chosen_name_for_docs = nk
                    break

        if not candidates:
            for nk in rec_name_keys:
                key = (None, nk, None)
                if key in index:
                    candidates = index[key]
                    match_type = "exact_name"
                    stats["matched_exact_name"] += 1
                    chosen_name_for_docs = nk
                    break

        # -------- FUZZY MATCHES --------
        if not candidates:
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
                    stats["matched_fuzzy_level_name_ects"] += 1
                elif (level, best_bk, None) in index:
                    candidates = index[(level, best_bk, None)]
                    match_type = "fuzzy_level_name"
                    stats["matched_fuzzy_level_name"] += 1
                elif bucket is not None and (None, best_bk, bucket) in index:
                    candidates = index[(None, best_bk, bucket)]
                    match_type = "fuzzy_name_ects"
                    stats["matched_fuzzy_name_ects"] += 1
                elif (None, best_bk, None) in index:
                    candidates = index[(None, best_bk, None)]
                    match_type = "fuzzy_name"
                    stats["matched_fuzzy_name"] += 1

                if best_rec_key:
                    chosen_name_for_docs = best_rec_key

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

            new_rec["faculty"] = faculties[0] if faculties else None
            new_rec["faculties"] = faculties
            new_rec["documents"] = docs_dedup
            new_rec["matched_sources"] = sources
            new_rec["match_type"] = match_type
        else:
            stats["unmatched"] += 1
            new_rec["faculty"] = None
            new_rec["faculties"] = []
            new_rec["documents"] = []
            new_rec["matched_sources"] = []
            new_rec["match_type"] = "unmatched"

        merged.append(new_rec)

    return merged, stats


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

    index = build_indices(source_entries)

    merged, stats = merge(
        base,
        index,
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