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


def norm_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("&", " and ")
    s = strip_accents(s)

    # normalize apostrophes
    s = re.sub(r"[’'`]", "", s)

    # small typo normalization that helps "Umwelgeistes..." vs "Umweltgeistes..."
    s = s.replace("umwelgeistes", "umweltgeistes")

    # keep only alnum as tokens
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


# ----------------------------
# Category/level normalization
# ----------------------------
def norm_level(v: Any) -> Optional[str]:
    """
    Normalize level/category to one of: 'B', 'M', 'D'
    Accepts: 'b','m','d','bachelor','master','doctorat','doctorate', etc.
    """
    if v is None:
        return None
    s = norm_text(str(v))
    if not s:
        return None
    if s in {"b", "ba", "bachelor", "bachelors"}:
        return "B"
    if s in {"m", "ma", "master", "masters"}:
        return "M"
    if s in {"d", "dr", "doctorate", "doctorat", "doktorat", "phd"}:
        return "D"
    if s.upper() in {"B", "M", "D"}:
        return s.upper()
    return None


# ----------------------------
# Programme name canonicalization (prefix tolerant)
# ----------------------------
NAME_PREFIXES = {
    "hauptfach",
    "nebenfach",
    "zusatzfach",
    "zusatzfacher",
    "zusatzfaecher",
    "zusatzfacher",
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
    "d",
    "etudes",
    "études",
    "study",
    "ects",
    "kreditpunkte",
    "kreditpunkten",
}


def canonical_name_keys(name: str) -> List[str]:
    """
    Produce multiple normalized keys for matching.
      'Hauptfach Informatik' -> ['hauptfach informatik', 'informatik']
    """
    full = norm_text(name)
    if not full:
        return []
    toks = full.split()

    # Strip leading prefixes repeatedly
    i = 0
    while i < len(toks) and toks[i] in NAME_PREFIXES:
        i += 1

    stripped = " ".join(toks[i:]).strip()

    keys = [full]
    if stripped and stripped != full:
        keys.append(stripped)

    # de-dup preserve order
    seen = set()
    out = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


# ----------------------------
# ECTS helpers
# ----------------------------
ECTS_RE = re.compile(r"\b(\d{2,3})\s*(ects|kreditpunkte|credits?)\b", re.IGNORECASE)


def ects_bucket(name: str, ects_value: Optional[int]) -> Optional[int]:
    # NOTE: if you no longer want to force 120 for "hauptfach" here, remove this.
    if "hauptfach" in norm_text(name):
        return 120
    return ects_value


def extract_ects_from_text(text: str) -> Optional[int]:
    if not text:
        return None
    m = ECTS_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


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


# ----------------------------
# Variant A: less strict fuzzy (token-overlap fallback)
# ----------------------------
def best_fuzzy_key(target_key: str, all_keys: List[str], *, limit: int = 8000) -> Tuple[Optional[str], float]:
    """
    Find best fuzzy match for a normalized key.

    Strategy:
      1) Prefer candidates that share at least one token with target_key
      2) If none share tokens (abbrev/typo cases), fall back to scanning a bounded slice
    """
    toks = set(target_key.split())
    if not toks:
        return None, 0.0

    candidates = [k for k in all_keys if toks.intersection(k.split())]

    # Fallback: if token overlap yields no candidates, scan a bounded subset
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
# Doc filtering (kept)
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
    "d'etudes",
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


# ----------------------------
# Parsing sources
# ----------------------------
def extract_source_entries(source_name: str, data: Any) -> List[Dict[str, Any]]:
    """
    Standardize faculty input into entries:
      { source, faculty, level, names[], ects_candidates[], documents[] }
    """
    out: List[Dict[str, Any]] = []
    if not isinstance(data, list):
        return out

    for item in data:
        if not isinstance(item, dict):
            continue

        entry: Dict[str, Any] = {
            "source": source_name,
            "faculty": item.get("faculty") or item.get("faculty_canonical"),
            "level": norm_level(item.get("level") or item.get("category") or item.get("programme_level")),
            "names": [],
            "ects_candidates": [],
            "documents": [],
        }

        # names
        if isinstance(item.get("title"), str) and item["title"]:
            entry["names"].append(item["title"])

        if isinstance(item.get("name_variants"), list):
            for v in item["name_variants"]:
                if isinstance(v, str) and v:
                    entry["names"].append(v)

        if isinstance(item.get("program"), str) and item["program"]:
            entry["names"].append(item["program"])

        prog = item.get("program")
        if isinstance(prog, dict):
            for k in ("name_de", "name_fr", "name_en", "name_it", "name"):
                if isinstance(prog.get(k), str) and prog.get(k):
                    entry["names"].append(prog[k])
            entry["level"] = entry["level"] or norm_level(prog.get("level") or prog.get("category"))

        # documents
        if isinstance(item.get("documents"), list):
            entry["documents"].extend(
                [
                    d
                    for d in item["documents"]
                    if isinstance(d, dict) and (d.get("url") or d.get("label"))
                ]
            )

        # de-dup names
        entry["names"] = [n for n in entry["names"] if isinstance(n, str) and n.strip()]
        seen = set()
        deduped = []
        for n in entry["names"]:
            if n not in seen:
                seen.add(n)
                deduped.append(n)
        entry["names"] = deduped

        # infer ects candidates
        ects_set = set()
        for n in entry["names"]:
            if "hauptfach" in norm_text(n):
                ects_set.add(120)
            ev = extract_ects_from_text(n)
            if isinstance(ev, int):
                ects_set.add(ev)

        src_ects = item.get("ects")
        if isinstance(src_ects, int):
            ects_set.add(src_ects)
        elif isinstance(src_ects, str) and src_ects.strip().isdigit():
            ects_set.add(int(src_ects.strip()))

        # also consider ects_candidates if present (bucket pages)
        if isinstance(item.get("ects_candidates"), list):
            for ev in item["ects_candidates"]:
                if isinstance(ev, int):
                    ects_set.add(ev)
                elif isinstance(ev, str) and ev.strip().isdigit():
                    ects_set.add(int(ev.strip()))

        entry["ects_candidates"] = sorted(ects_set)
        out.append(entry)

    return out


# ----------------------------
# Build indices (LEVEL + NAME + ECTS)
# ----------------------------
IndexKey = Tuple[Optional[str], str, Optional[int]]
# (level, name_key, ects_bucket)


def build_indices(source_entries: List[Dict[str, Any]]) -> Dict[IndexKey, List[Dict[str, Any]]]:
    """
    Index keys:
      (level, name_key, ects_bucket)

    Notes:
      - level can be None (wildcard)
      - name_key includes both full and prefix-stripped versions
      - ects_bucket can be None (unknown)
    """
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
    fuzzy_threshold: float = 0.86,
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
        # keep your previous behavior: ignore D records
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

        # bucket: force 120 if any variant contains 'hauptfach'
        bucket: Optional[int] = None
        for v in variants:
            b = ects_bucket(v, base_ects_raw)
            if b is not None:
                bucket = b
                if b == 120:
                    break

        candidates: List[Dict[str, Any]] = []
        match_type: Optional[str] = None

        # Build all candidate name keys for this record (full + prefix-stripped)
        rec_name_keys: List[str] = []
        for v in variants:
            rec_name_keys.extend(canonical_name_keys(v))

        # de-dup
        seen = set()
        rec_name_keys = [k for k in rec_name_keys if not (k in seen or seen.add(k))]

        # -------- EXACT MATCHES --------
        # 1) (level, name, ects)
        if bucket is not None:
            for nk in rec_name_keys:
                key = (level, nk, bucket)
                if key in index:
                    candidates = index[key]
                    match_type = "exact_level_name_ects"
                    stats["matched_exact_level_name_ects"] += 1
                    chosen_name_for_docs = nk
                    break

        # 2) (level, name, None)
        if not candidates:
            for nk in rec_name_keys:
                key = (level, nk, None)
                if key in index:
                    candidates = index[key]
                    match_type = "exact_level_name"
                    stats["matched_exact_level_name"] += 1
                    chosen_name_for_docs = nk
                    break

        # 3) (None, name, ects)  (level-agnostic)
        if not candidates and bucket is not None:
            for nk in rec_name_keys:
                key = (None, nk, bucket)
                if key in index:
                    candidates = index[key]
                    match_type = "exact_name_ects"
                    stats["matched_exact_name_ects"] += 1
                    chosen_name_for_docs = nk
                    break

        # 4) (None, name, None)
        if not candidates:
            for nk in rec_name_keys:
                key = (None, nk, None)
                if key in index:
                    candidates = index[key]
                    match_type = "exact_name"
                    stats["matched_exact_name"] += 1
                    chosen_name_for_docs = nk
                    break

        # -------- FUZZY MATCHES (Variant A) --------
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
    ap.add_argument("--fuzzy-threshold", type=float, default=0.86)
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
