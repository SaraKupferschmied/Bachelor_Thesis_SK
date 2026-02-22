#!/usr/bin/env python3
"""
Normalize all faculty JSON files into ONE combined interfaculty-like file.

Adds (without deleting existing fields):
  - title
  - page_url
  - name_variants
  - doc_urls
  - file_urls
  - lang

Also adds/normalizes for better matching:
  - program_clean: canonical programme name for matching (e.g. "Biologie", "Law", "Theology")
  - program_base_clean: same as program_clean but WITHOUT track prefixes like "Hauptfach"/"Zusatzfächer"
  - faculty_canonical: stable faculty bucket (e.g. "Law", "Theology", "Science and Medicine")
  - level: 'B'/'M'/'D' if we can infer it
  - ects: numeric ects when possible
  - ects_candidates: optional list for bucket pages (e.g. "Nebenfächer" pages)
  - track: optional ("nebenfach", "plus30", etc.) for debugging/matching

Faculty-specific logic:
  - SCIMED:
      * "+30" pages are ignored (left as-is; no forced ects/prefix logic)
      * "Zusatzfächer " is ADDED for programmes with ects below thresholds:
          - Bachelor: ects < 120  => "Zusatzfächer <name>"
          - Master:   ects < 90   => "Zusatzfächer <name>"
      * "Hauptfach " is ADDED ONLY to the biggest ECTS variant per (level, base name),
        if that biggest ects meets thresholds:
          - Bachelor: ects >= 120
          - Master:   ects >= 90
        (This is done in a second pass across all SCIMED items.)

  - Theology: map to
      "Theology", "Theology (canonical License)", "Interreligious studies"
  - Law: normalize "Recht / Droit / Law / Jus" to "Law" (unless special programmes like MALS)

Generic logic:
  - If the item looks like a "Nebenfach" bucket page (any faculty):
      * If a single ects is explicit => keep ects
      * If ects is NOT explicit or multiple values appear in docs => set ects=None and
        set ects_candidates based on what is found, or defaults (B/M => [30,60])

Output: one big JSON list containing all normalized entries.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


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
# Basic helpers
# ----------------------------
def clean_text(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return re.sub(r"\s+", " ", str(s)).strip()


def uniq(xs: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in xs:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def norm(s: str) -> str:
    return (s or "").strip().lower()


def pick_lang_from_url(u: Optional[str]) -> Optional[str]:
    if not u:
        return None
    m = re.search(r"/(de|fr|en|it)/", u)
    return m.group(1) if m else None


def first_int(v: Any) -> Optional[int]:
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


# ----------------------------
# ECTS parsing
# ----------------------------
ECTS_RE = re.compile(r"\b(\d{1,3})\s*(ects|kreditpunkte|credits?)\b", re.IGNORECASE)

def parse_ects_from_text(s: str) -> Optional[int]:
    if not s:
        return None
    m = ECTS_RE.search(s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


# ----------------------------
# Level inference
# ----------------------------
def infer_level_from_urls(*urls: Optional[str]) -> Optional[str]:
    blob = " ".join([u for u in urls if isinstance(u, str) and u]).lower()
    if any(x in blob for x in ["/doctorat/", "/doktorat/", "doctorat", "doktorat", "phd"]):
        return "D"
    if "/master/" in blob or "/ma/" in blob or "master" in blob or "msc" in blob or "spmsc" in blob:
        return "M"
    if "/bachelor/" in blob or "/ba/" in blob or "bachelor" in blob or "bsc" in blob:
        return "B"
    return None


def infer_level_generic(item: Dict[str, Any]) -> Optional[str]:
    cand = [
        item.get("level"),
        item.get("category"),
        item.get("program_group"),
        item.get("page_url"),
        item.get("page_url_de"),
        item.get("page_url_fr"),
        item.get("page_url_en"),
        item.get("page_url_it"),
    ]
    txt = " ".join([str(c) for c in cand if c])
    t = norm(txt)

    if any(x in t for x in ["doctorat", "doktor", "phd", "doctorate"]):
        return "D"
    if any(x in t for x in ["master", "msc", "ma ", "m a", "/master/", "spmsc"]):
        return "M"
    if any(
        x in t
        for x in [
            "bachelor",
            "bsc",
            "ba ",
            "b a",
            "/bachelor/",
            "für bachelors",
            "fuer bachelors",
            "nebenfach",
            "branche secondaire",
            "minors",
        ]
    ):
        return "B"
    return None


# ----------------------------
# Faculty canonicalization
# ----------------------------
def faculty_canonical_from(item: Dict[str, Any], source_name: str) -> Optional[str]:
    f = item.get("faculty")
    if isinstance(f, str) and f.strip():
        fs = f.strip()
        if fs.upper() == "SCIMED":
            return "Science and Medicine"
        if fs.upper() == "EDUFORM":
            return "EDUFORM"
        if fs.lower() in {"theology", "theologische fakultaet", "theologische fakultät"}:
            return "Theology"
        if fs.lower() in {"law", "ius", "rechtswissenschaft", "droit"}:
            return "Law"
        return fs

    s = (source_name or "").lower()
    if "scimed" in s:
        return "Science and Medicine"
    if "eduform" in s:
        return "EDUFORM"
    if "theo" in s:
        return "Theology"
    if "ius" in s or "law" in s:
        return "Law"
    return None


# ----------------------------
# Documents & name variants extraction (generic)
# ----------------------------
def extract_documents(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    raw = item.get("documents")
    if isinstance(raw, list):
        for d in raw:
            if isinstance(d, dict) and d.get("url"):
                docs.append(d)
            elif isinstance(d, str):
                docs.append({"url": d})
    return docs


def extract_name_variants(item: Dict[str, Any]) -> List[str]:
    variants: List[str] = []

    if isinstance(item.get("title"), str):
        variants.append(item["title"])

    prog = item.get("program")
    if isinstance(prog, dict):
        for k in ["name", "name_de", "name_fr", "name_en", "name_it"]:
            if isinstance(prog.get(k), str):
                variants.append(prog[k])

    if isinstance(item.get("program"), str):
        variants.append(item["program"])

    for k in ["programme", "program_name", "name", "program_clean", "program_base_clean"]:
        if isinstance(item.get(k), str):
            variants.append(item[k])

    docs = item.get("documents")
    if isinstance(docs, list):
        for d in docs:
            if isinstance(d, dict) and isinstance(d.get("label"), str):
                variants.append(d["label"])

    return uniq([clean_text(v) for v in variants if clean_text(v)])


def choose_best_title(name_variants: List[str], track: Optional[str] = None) -> Optional[str]:
    if not name_variants:
        return None

    # If this is a bucket page, prefer the bucket label if present.
    if track in {"nebenfach", "plus30"}:
        for v in name_variants:
            if v and len(v) <= 40:
                # often the bucket itself is short and early in variants
                return v

    bad_patterns = [
        r"^studienplan\b",
        r"^plan d['’]études\b",
        r"^study plan\b",
        r"^nebenfächer\b",
        r"^nebenfaecher\b",
        r"^doppelabschlüsse\b",
        r"^kompetenzrahmen\b",
        r"^sprachen öffnen\b",
        r"^sprachen offnen\b",
    ]

    def is_bad(s: str) -> bool:
        t = s.lower()
        return any(re.search(p, t) for p in bad_patterns)

    good = [v for v in name_variants if not is_bad(v)]
    if good:
        return sorted(good, key=len)[0]
    return name_variants[0]


def choose_page_url(item: Dict[str, Any]) -> Optional[str]:
    if isinstance(item.get("page_url"), str):
        return item["page_url"]

    prog = item.get("program")
    if isinstance(prog, dict):
        for k in ["page_url", "page_url_de", "page_url_fr", "page_url_en", "page_url_it", "studienplan_url"]:
            if isinstance(prog.get(k), str):
                return prog[k]

    for k in ["page_url_de", "page_url_fr", "page_url_en", "page_url_it", "studienplan_url"]:
        if isinstance(item.get(k), str):
            return item[k]

    return None


# ----------------------------
# Generic "Nebenfach bucket" logic
# ----------------------------
_RE_NEBENFACH = re.compile(r"\b(nebenfach|nebenfächer|branche\s*secondaire|minors?)\b", re.IGNORECASE)

def is_nebenfach_bucket(item: Dict[str, Any], page_url: Optional[str], title: Optional[str], program: Any) -> bool:
    blobs: List[str] = []
    for k in ("category", "program_group"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            blobs.append(v)
    if isinstance(title, str) and title.strip():
        blobs.append(title)
    if isinstance(page_url, str) and page_url.strip():
        blobs.append(page_url)
    if isinstance(program, str) and program.strip():
        blobs.append(program)
    if isinstance(program, dict):
        for k in ("name_de", "name_fr", "name_en", "name_it", "name"):
            v = program.get(k)
            if isinstance(v, str) and v.strip():
                blobs.append(v)

    text = " ".join(blobs)
    return bool(_RE_NEBENFACH.search(text))


def nebenfach_default_ects_candidates(level: Optional[str]) -> List[int]:
    # practical defaults; you can extend if you see more
    if level == "M":
        return [30, 60]  # < 90
    if level == "B":
        return [30, 60]  # < 120
    return [30, 60]


# ----------------------------
# SCIMED helpers
# ----------------------------
PLUS30_CAT = {"plus30", "plus 30", "bcp30", "bc p30", "bc+30", "bc+ 30"}

_RE_PLUS30 = re.compile(r"\+\s*30\b", re.IGNORECASE)
_RE_ZUSATZ_ANY = re.compile(r"\bzusatzf(ae|ä)cher\b|\bzusatzfach\b", re.IGNORECASE)
_RE_HAUPTFACH_ANY = re.compile(r"\bhauptfach\b", re.IGNORECASE)

_RE_STRIP_PREFIXES = re.compile(r"^\s*(hauptfach|zusatzf(ae|ä)cher|zusatzfach)\b[:\-\s]*", re.IGNORECASE)

def scimed_is_plus30(item: Dict[str, Any], name: str) -> bool:
    cat = norm(str(item.get("category") or ""))
    pg = norm(str(item.get("program_group") or ""))
    pu = norm(str(item.get("page_url") or ""))
    t = norm(name)

    if cat in PLUS30_CAT:
        return True
    if "plus30" in cat or "plus30" in pg or "plus30" in pu:
        return True
    if "bcp30" in pg or "bcp30" in pu:
        return True
    if _RE_PLUS30.search(t):
        return True
    return False


def scimed_strip_track_prefixes(name: str) -> str:
    s = clean_text(name) or ""
    s = _RE_STRIP_PREFIXES.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"^[\-\:\s]+|[\-\:\s]+$", "", s).strip()
    return s or (clean_text(name) or "")


def scimed_thresholds(level: Optional[str]) -> Tuple[int, int]:
    """
    Returns (hauptfach_min, zusatzfaecher_max_exclusive) as thresholds.
      - Master: hauptfach_min=90,  zusatzfaecher if ects < 90
      - Bachelor: hauptfach_min=120, zusatzfaecher if ects < 120
    """
    if level == "M":
        return 90, 90
    return 120, 120


# ----------------------------
# Theology normalization
# ----------------------------
_RE_THEO_CANON = re.compile(
    r"\b(kanonisch|canonique|licence canonique|kanonisches lizenziat|lizenziat)\b",
    re.IGNORECASE,
)
_RE_THEO_INTERREL = re.compile(r"\b(interreligi|interrelig)\b", re.IGNORECASE)
_RE_THEO_THEOLOGY = re.compile(r"\b(theolog|théolog|theologie|théologie)\b", re.IGNORECASE)

_RE_THEO_JUNK_WORDS = re.compile(
    r"\b(master|bachelor|of|arts|science|in|en|de|studien|etudes|"
    r"hauptprogramm|vollprogramm|spezialisierung|mit|programme|programm|ects)\b",
    re.IGNORECASE,
)

def theo_program_clean(name: str, page_urls: List[str]) -> str:
    t = (name or "").strip()

    if _RE_THEO_CANON.search(t):
        return "Theology (canonical License)"
    if _RE_THEO_INTERREL.search(t):
        return "Interreligious studies"
    if _RE_THEO_THEOLOGY.search(t) or any("theology" in (u or "").lower() for u in page_urls):
        return "Theology"

    s = re.sub(r"\b\d{2,3}\b", " ", t)
    s = _RE_THEO_JUNK_WORDS.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or t


# ----------------------------
# Law normalization
# ----------------------------
_RE_LAW_CORE = re.compile(r"\b(recht|droit|law|jus)\b", re.IGNORECASE)
_RE_LAW_MALS = re.compile(r"\bmals\b", re.IGNORECASE)

def law_program_clean(name: str) -> str:
    t = (name or "").strip()
    if _RE_LAW_MALS.search(t):
        return "MALS"
    if _RE_LAW_CORE.search(t):
        return "Law"
    s = re.sub(r"\b(master|bachelor|of|arts|science|in|ects)\b", " ", t, flags=re.IGNORECASE)
    s = re.sub(r"\b\d{1,3}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or t


# ----------------------------
# Main normalize per item
# ----------------------------
def normalize_item(item: Dict[str, Any], source_name: str) -> Dict[str, Any]:
    out = dict(item)

    docs = extract_documents(item)
    doc_urls = uniq([d.get("url") for d in docs if isinstance(d, dict) and d.get("url")])

    page_url = choose_page_url(out)
    lang = out.get("lang") or pick_lang_from_url(page_url)

    out["source_file"] = source_name
    out["page_url"] = page_url
    out["lang"] = lang
    out["documents"] = docs
    out["doc_urls"] = doc_urls
    out["file_urls"] = uniq(out.get("file_urls", []) + doc_urls if isinstance(out.get("file_urls"), list) else doc_urls)

    out["faculty_canonical"] = out.get("faculty_canonical") or faculty_canonical_from(out, source_name)

    # infer level
    prog = out.get("program")
    prog_urls: List[str] = []
    if isinstance(prog, dict):
        for k in ["page_url_de", "page_url_fr", "page_url_en", "page_url_it", "page_url"]:
            if isinstance(prog.get(k), str) and prog.get(k):
                prog_urls.append(prog[k])

    out["level"] = out.get("level") or infer_level_from_urls(page_url, *prog_urls) or infer_level_generic(out)

    # ECTS: prefer explicit program dict ects, then item ects, then parse from strings
    ects: Optional[int] = None
    if isinstance(prog, dict):
        ects = first_int(prog.get("ects"))
    ects = ects if ects is not None else first_int(out.get("ects"))

    # Parse from program names and doc labels if still unknown
    if ects is None:
        if isinstance(prog, dict):
            for k in ["name_de", "name_fr", "name_en", "name_it", "name"]:
                if isinstance(prog.get(k), str):
                    ects = parse_ects_from_text(prog[k])
                    if ects is not None:
                        break
        if ects is None:
            for d in docs:
                if isinstance(d, dict) and isinstance(d.get("label"), str):
                    ects = parse_ects_from_text(d["label"])
                    if ects is not None:
                        break

    # ----------------------------
    # Generic Nebenfach bucket handling (ANY faculty)
    # ----------------------------
    title0 = clean_text(out.get("title")) or ""
    program0 = out.get("program")

    if is_nebenfach_bucket(out, page_url, title0, program0):
        out["track"] = out.get("track") or "nebenfach"

        found: Set[int] = set()

        # program dict explicit ects is authoritative if set
        prog_ects = first_int(prog.get("ects")) if isinstance(prog, dict) else None
        if prog_ects is not None:
            ects = prog_ects
        else:
            # parse ects from program names
            if isinstance(prog, dict):
                for k in ["name_de", "name_fr", "name_en", "name_it", "name"]:
                    if isinstance(prog.get(k), str):
                        ev = parse_ects_from_text(prog[k])
                        if isinstance(ev, int):
                            found.add(ev)
            elif isinstance(prog, str):
                ev = parse_ects_from_text(prog)
                if isinstance(ev, int):
                    found.add(ev)

            # parse ects from docs
            for d in docs:
                if not isinstance(d, dict):
                    continue
                for field in ("label", "url"):
                    v = d.get(field)
                    if isinstance(v, str) and v:
                        ev = parse_ects_from_text(v)
                        if isinstance(ev, int):
                            found.add(ev)

            # If multiple or none -> keep ects None and store candidates
            if len(found) == 1:
                ects = next(iter(found))
            else:
                out["ects_candidates"] = sorted(found) if found else nebenfach_default_ects_candidates(out.get("level"))
                ects = None

    # ----------------------------
    # Faculty-specific program_clean
    # ----------------------------
    fac = out.get("faculty_canonical") or ""

    # SCIMED: use first doc label as program name when available
    if (source_name or "").lower() == "scimed.json" or out.get("faculty") == "SCIMED" or fac == "Science and Medicine":
        first_label = None
        if docs and isinstance(docs[0], dict):
            first_label = clean_text(docs[0].get("label"))
        if first_label:
            if "program_group" not in out and isinstance(out.get("program"), str):
                out["program_group"] = out.get("program")
            out["program"] = first_label
            out["program_name"] = first_label

        raw_program = clean_text(out.get("program")) or ""

        if scimed_is_plus30(out, raw_program):
            # ignore +30: leave as is; no forced ects/prefix logic
            out["track"] = out.get("track") or "plus30"
            out["program_base_clean"] = raw_program
            out["program_clean"] = raw_program
        else:
            base_clean = scimed_strip_track_prefixes(raw_program)
            out["program_base_clean"] = base_clean
            # program_clean will be set in second pass (prefix rules) but keep a sane default now
            out["program_clean"] = base_clean

    # Theology
    elif fac == "Theology":
        name_candidates: List[str] = []
        if isinstance(prog, dict):
            for k in ["name_de", "name_fr", "name_en", "name_it", "name"]:
                if isinstance(prog.get(k), str) and prog.get(k).strip():
                    name_candidates.append(prog[k].strip())
        if isinstance(out.get("program"), str) and out["program"].strip():
            name_candidates.append(out["program"].strip())
        if docs:
            for d in docs:
                if isinstance(d, dict) and isinstance(d.get("label"), str):
                    name_candidates.append(d["label"])

        best_name = name_candidates[0] if name_candidates else (out.get("program") if isinstance(out.get("program"), str) else "")
        out["program_base_clean"] = theo_program_clean(best_name, prog_urls + ([page_url] if page_url else []))
        out["program_clean"] = out["program_base_clean"]

    # Law
    elif fac == "Law":
        name_candidates = []
        if isinstance(prog, dict):
            for k in ["name_de", "name_fr", "name_en", "name_it", "name"]:
                if isinstance(prog.get(k), str) and prog.get(k).strip():
                    name_candidates.append(prog[k].strip())
        if isinstance(out.get("program"), str) and out["program"].strip():
            name_candidates.append(out["program"].strip())

        best_name = name_candidates[0] if name_candidates else (out.get("program") if isinstance(out.get("program"), str) else "")
        out["program_base_clean"] = law_program_clean(best_name)
        out["program_clean"] = out["program_base_clean"]

        cat = norm(str(out.get("category") or ""))
        if "nebenfach" in cat or "branche secondaire" in cat:
            out["track"] = out.get("track") or "minor"

    else:
        # generic fallback
        if isinstance(out.get("program_clean"), str) and out["program_clean"].strip():
            out["program_base_clean"] = out.get("program_base_clean") or out["program_clean"].strip()
        elif isinstance(out.get("program"), str) and out["program"].strip():
            out["program_clean"] = out["program"].strip()
            out["program_base_clean"] = out["program_clean"]
        elif isinstance(prog, dict):
            for k in ["name_de", "name_fr", "name_en", "name_it", "name"]:
                if isinstance(prog.get(k), str) and prog.get(k).strip():
                    out["program_clean"] = prog[k].strip()
                    out["program_base_clean"] = out["program_clean"]
                    break

    # normalize ects field if we found it (and we didn't intentionally clear it)
    if ects is not None:
        out["ects"] = ects
    else:
        # leave as None if unknown / bucket
        out["ects"] = out.get("ects") if first_int(out.get("ects")) is not None else None

    # Build name variants AFTER program_clean is set
    name_variants = extract_name_variants(out)
    if isinstance(out.get("program_clean"), str) and out["program_clean"].strip():
        name_variants = uniq([out["program_clean"]] + name_variants)
    if isinstance(out.get("program_base_clean"), str) and out["program_base_clean"].strip():
        name_variants = uniq([out["program_base_clean"]] + name_variants)

    out["name_variants"] = name_variants
    out["title"] = choose_best_title(name_variants, out.get("track"))

    return out


# ----------------------------
# Second pass: apply SCIMED prefix rules across variants
# ----------------------------
def apply_scimed_prefix_rules(all_items: List[Dict[str, Any]]) -> None:
    """
    For SCIMED items (not plus30):
      - Add "Zusatzfächer " to programmes whose ects is below threshold (B<120, M<90)
      - Add "Hauptfach " ONLY to the biggest ects per (level, program_base_clean) if meets threshold
    """
    # group scimed items by (level, base_clean)
    groups: Dict[Tuple[Optional[str], str], List[Dict[str, Any]]] = {}

    for it in all_items:
        if it.get("faculty_canonical") != "Science and Medicine":
            continue
        if it.get("track") == "plus30":
            continue
        base_clean = it.get("program_base_clean")
        if not isinstance(base_clean, str) or not base_clean.strip():
            continue
        level = it.get("level") if isinstance(it.get("level"), str) else None
        groups.setdefault((level, base_clean.strip()), []).append(it)

    for (level, base_clean), items in groups.items():
        # find max ects among items that have ects
        max_ects = None
        for it in items:
            ev = first_int(it.get("ects"))
            if ev is None:
                continue
            if max_ects is None or ev > max_ects:
                max_ects = ev

        hauptfach_min, zusatz_cutoff = scimed_thresholds(level)

        # decide which item(s) get Hauptfach
        for it in items:
            ev = first_int(it.get("ects"))
            if ev is None:
                # if ects missing, leave plain base name
                it["program_clean"] = base_clean
                continue

            # Hauptfach only for max ects variant (and if meets threshold)
            if max_ects is not None and ev == max_ects and ev >= hauptfach_min:
                it["program_clean"] = f"Hauptfach {base_clean}"
                it["track"] = it.get("track") or "hauptfach"
            else:
                # Zusatzfächer if below cutoff
                if ev < zusatz_cutoff:
                    it["program_clean"] = f"Zusatzfächer {base_clean}"
                    it["track"] = it.get("track") or "minor"
                else:
                    it["program_clean"] = base_clean

    # After changing program_clean, refresh name_variants + title for affected items
    for it in all_items:
        if it.get("faculty_canonical") != "Science and Medicine":
            continue
        if it.get("track") == "plus30":
            continue

        # rebuild variants with updated program_clean
        nv = extract_name_variants(it)
        if isinstance(it.get("program_clean"), str) and it["program_clean"].strip():
            nv = uniq([it["program_clean"]] + nv)
        if isinstance(it.get("program_base_clean"), str) and it["program_base_clean"].strip():
            nv = uniq([it["program_base_clean"]] + nv)
        it["name_variants"] = nv
        it["title"] = choose_best_title(nv, it.get("track"))


# ----------------------------
# Main
# ----------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True, help="Folder containing faculty JSON files")
    ap.add_argument("--out", required=True, help="Output combined normalized file")
    args = ap.parse_args()

    input_dir = Path(args.input_dir)
    all_items: List[Dict[str, Any]] = []

    for fp in sorted(input_dir.glob("*.json")):
        data = load_json(fp)
        if not isinstance(data, list):
            continue

        for item in data:
            if isinstance(item, dict):
                all_items.append(normalize_item(item, fp.name))

    # second pass rules for SCIMED variants
    apply_scimed_prefix_rules(all_items)

    save_json(Path(args.out), all_items)

    print("Done.")
    print(f"Combined normalized file written to: {args.out}")
    print(f"Total normalized entries: {len(all_items)}")

    # small sanity counts
    theo = sum(1 for x in all_items if x.get("faculty_canonical") == "Theology")
    law = sum(1 for x in all_items if x.get("faculty_canonical") == "Law")
    scimed = sum(1 for x in all_items if x.get("faculty_canonical") == "Science and Medicine")
    neben = sum(1 for x in all_items if x.get("track") == "nebenfach")
    plus30 = sum(1 for x in all_items if x.get("track") == "plus30")
    print(f"Theology normalized entries: {theo}")
    print(f"Law normalized entries: {law}")
    print(f"SCIMED normalized entries: {scimed}")
    print(f"Nebenfach bucket entries: {neben}")
    print(f"SCIMED +30 ignored entries: {plus30}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
