"""
Ollama-based RAG for regulations + study plans.

- Loads parsed PDFs from ./parsed/<subfolder>
- Splits into chunks
- Creates/loads persisted FAISS indexes
- Answers questions via ChatOllama using ONLY retrieved context

Retrieval strategy:
- Metadata-aware retrieval detects programme, degree level, ECTS amount, and study year.
- Language-aware retrieval translates the question into indexed document languages.
- The combined retriever runs metadata-aware retrieval for the original and translated queries.
"""

from __future__ import annotations

import json
import re
import requests
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document

from .config import settings
from .performance import timed_step


_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=900,
    chunk_overlap=150,
    separators=[
        "\nArt. ",
        "\nArtikel ",
        "\n§",
        "\n## ",
        "\n### ",
        "\n\n",
        "\n",
        " ",
        "",
    ],
)

_EMBEDDINGS = HuggingFaceEmbeddings(
    model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)

META_RE = re.compile(r"---METADATA_JSON---\s*(\{.*?\})\s*---/METADATA_JSON---", re.S)
PAGE_RE = re.compile(r"---PAGE\s+(\d+)---\s*(.*?)(?=---PAGE\s+\d+---|\Z)", re.S)


LANGUAGE_NAMES = {
    "de": "German",
    "fr": "French",
    "en": "English",
    "it": "Italian",
    "es": "Spanish",
}


def detect_request_language(text: str | None, explicit_language: str | None = None) -> str:
    """Return the answer language for a request.

    The API's explicit language parameter wins.  If it is missing, use a
    small deterministic detector for the languages relevant to this thesis
    project.  This avoids depending on the LLM for routing and keeps German
    and French questions from silently falling back to English.
    """
    explicit = _normalize_language_code(explicit_language)
    if explicit in {"de", "fr", "en", "it", "es"}:
        return explicit

    normalized = _normalize_text(text or "")
    tokens = set(normalized.split())

    german_markers = {
        "bitte", "gib", "mir", "liste", "aller", "alle", "welche", "welcher", "welches",
        "kurs", "kurse", "modul", "module", "studiengang", "studienplan", "wirtschaftsinformatik",
        "bachelor", "master", "deutsch", "deutsche", "auf", "und", "oder", "im", "im", "der",
        "die", "das", "des", "für", "fuer", "semester", "jahr", "studienjahr", "angeboten",
        "unterrichtet", "prüfungen", "pruefungen", "ects", "zeige", "nenne", "erkläre", "erklaere",
    }
    french_markers = {
        "donne", "moi", "liste", "tous", "toutes", "quels", "quelles", "quel", "quelle",
        "cours", "module", "modules", "programme", "bachelor", "master", "français", "francais",
        "en", "et", "ou", "du", "de", "des", "la", "le", "les", "pour", "semestre", "annee",
        "année", "enseigné", "enseignes", "enseignés", "examen", "examens", "montre", "explique",
        "informatique", "gestion",
    }
    english_markers = {
        "please", "give", "show", "list", "all", "which", "what", "course", "courses",
        "module", "modules", "program", "programme", "study", "plan", "semester", "year",
        "bachelor", "master", "english", "taught", "offered", "explain",
    }

    scores = {
        "de": len(tokens & german_markers),
        "fr": len(tokens & french_markers),
        "en": len(tokens & english_markers),
    }

    # Umlauts and common French accents are strong signals.
    raw = text or ""
    if re.search(r"[äöüßÄÖÜ]", raw):
        scores["de"] += 2
    if re.search(r"[àâçéèêëîïôùûüÿœÀÂÇÉÈÊËÎÏÔÙÛÜŸŒ]", raw):
        scores["fr"] += 2

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "en"


def _looks_english(text: str | None) -> bool:
    if not text:
        return False
    normalized = _normalize_text(text)
    tokens = set(normalized.split())
    english_markers = {
        "based", "provided", "appears", "there", "are", "several", "students", "pursuing",
        "degree", "here", "summary", "information", "references", "note", "answer", "question",
        "found", "matching", "results", "course", "courses", "program", "programme", "section",
    }
    return len(tokens & english_markers) >= 3


def ensure_answer_language(answer: str, language: str | None) -> str:
    """Final safety guard: translate accidental English answers back to the requested UI language.

    The RAG prompt already asks the model to answer in the requested language,
    but local models sometimes ignore that instruction when the context is
    multilingual.  This guard only performs a second LLM call when it detects
    the common broken case: target German/French but answer is visibly English.
    Citations, course codes, ECTS values, and bullet structure are preserved.
    """
    code = _normalize_language_code(language)
    if code not in {"de", "fr"}:
        return answer
    if not _looks_english(answer):
        return answer

    target_language = _language_name(code)
    llm = ChatOllama(
        model=settings.ollama_model,
        temperature=0,
        base_url=settings.ollama_host,
    )
    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            f"Translate the assistant answer into natural {target_language}. "
            "Preserve markdown formatting, bullets, course codes, ECTS values, semesters, proper names, and citations exactly. "
            "Do not add new facts and do not remove any factual information.",
        ),
        ("human", "Answer to translate:\n{answer}"),
    ])
    try:
        with timed_step("answer.language_guard"):
            resp = llm.invoke(prompt.format_messages(answer=answer))
        translated = str(resp.content or "").strip()
        return translated or answer
    except Exception as exc:
        print(f"[warn] answer language guard failed for {code}: {exc}")
        return answer


_QUERY_STOPWORDS = {
    "what", "which", "who", "when", "where", "how", "are", "is", "the", "a", "an", "of", "in", "for", "to",
    "and", "or", "with", "without", "offered", "taught", "thaught", "courses", "course", "modules", "module",
    "programme", "program", "study", "studies", "plan", "year", "semester", "bachelor", "master",
    "welche", "welcher", "welches", "werden", "wird", "sind", "ist", "im", "des", "der", "die",
    "das", "ein", "eine", "einer", "einem", "einen", "von", "zu", "zum", "zur", "und", "oder", "mit", "ohne",
    "angeboten", "unterrichtet", "gelehrt", "kurse", "kurs", "module", "modul", "studiengang", "studienplan",
    "jahr", "semester", "ersten", "erstes", "erste", "zweiten", "zweites", "zweite", "dritten", "drittes", "dritte",
    "quels", "quelles", "cours", "sont", "dans", "du", "de", "la", "le", "les", "pour", "programme", "annee",
}

_DEGREE_ALIASES = {
    "Bachelor": ["bachelor", "bsc", "ba", "bachelorstudium"],
    "Master": ["master", "msc", "ma", "masterstudium"],
}

_YEAR_ALIASES = {
    "1": [
        "1. jahr", "1 jahr", "1st year", "first year", "erstes jahr", "ersten jahr", "erste jahr",
        "premiere annee", "première année", "1ere annee", "1ère année",
    ],
    "2": [
        "2. jahr", "2 jahr", "2nd year", "second year", "zweites jahr", "zweiten jahr", "zweite jahr",
        "deuxieme annee", "deuxième année", "2eme annee", "2ème année",
    ],
    "3": [
        "3. jahr", "3 jahr", "3rd year", "third year", "drittes jahr", "dritten jahr", "dritte jahr",
        "troisieme annee", "troisième année", "3eme annee", "3ème année",
    ],
}


def _normalize_language_code(language: str | None) -> str | None:
    if not language:
        return None

    value = language.lower().strip()
    aliases = {
        "german": "de",
        "deutsch": "de",
        "french": "fr",
        "francais": "fr",
        "français": "fr",
        "english": "en",
        "italian": "it",
        "italiano": "it",
        "spanish": "es",
        "espanol": "es",
        "español": "es",
    }
    return aliases.get(value, value[:2])


def _language_name(language: str | None) -> str:
    code = _normalize_language_code(language)
    return LANGUAGE_NAMES.get(code or "", "English")


def _build_prompt(language: str | None) -> ChatPromptTemplate:
    target_language = _language_name(language)

    return ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You are a careful assistant for university regulations and study plans from the University of Friburg (CH). "
                "Answer ONLY using the provided context, do not invent or speculate anything that is not in the context. "
                "If the answer is not in the context, say you cannot find it in the documents. "
                f"CRITICAL: The final answer must be written in {target_language}, because this is the user interface/request language. "
                f"Do not answer in English unless the requested language is English. Use natural, clear {target_language}. "
                "The retrieved documents may be in German, French, English, Italian, or Spanish; use them all if relevant. "
                "Even if the documents are written in another language, the final answer must be in the requested language. "
                "For study-plan questions, treat metadata as authoritative. "
                "Use only context chunks whose metadata matches the requested programme, degree level, semester or if applicable ECTS or headings like year or section"
                "Ignore chunks from other programmes or degree levels, even if their wording is similar. But please note that context in other languages is still relevant, only metadata are english, headers can be german, french or italian. "
                "When course rows are present, extract the course code, course title, semester, language, assessment, ECTS, "
                "and teacher if available. Do not invent missing course data. "
                "If the question is about courses for a certain studyprogram and the backend api does not help search the rag for the tables containing the courses and answer with the course names you find."
                "Always cite sources if there are any but dont invent exemplary sources.",
            ),
            ("human", "Question: {question}\n\nContext:\n{context}\n\nAnswer with citations:"),
        ]
    )


def _normalize_text(value: Any) -> str:
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = text.replace("_", " ").replace("-", " ").replace("/", " ")
    text = re.sub(r"[^\w\sÀ-ÿ]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokens(value: Any) -> set[str]:
    text = _normalize_text(value)
    return {
        token
        for token in text.split()
        if len(token) >= 3 and token not in _QUERY_STOPWORDS
    }



def _program_key_parts(program_key: Any) -> Dict[str, Any]:
    """Parse new parser key: faculty|level|ects|program name."""
    raw = str(program_key or "").strip()
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 4:
        return {}
    ects: int | None = None
    try:
        ects = int(float(parts[2]))
    except Exception:
        ects = None
    degree = parts[1].strip().title() if parts[1].strip() else None
    return {
        "faculty": parts[0] or None,
        "degree_level": degree,
        "total_ects": ects,
        "program_name": parts[3] or None,
    }





def _metadata_program_names(metadata: Dict[str, Any]) -> list[str]:
    """Return all programme-name variants stored in metadata, including legacy names."""
    names: list[str] = []
    for key in ("programme_name_en", "programme_name_de", "programme_name_fr", "program_name"):
        value = metadata.get(key)
        if value and str(value).strip() and str(value) not in names:
            names.append(str(value))

    key_parts = _program_key_parts(metadata.get("program_key"))
    key_name = key_parts.get("program_name")
    if key_name and str(key_name) not in names:
        names.append(str(key_name))

    return names


def _metadata_program_name(metadata: Dict[str, Any]) -> str | None:
    names = _metadata_program_names(metadata)
    return names[0] if names else None


def _metadata_degree(metadata: Dict[str, Any]) -> str | None:
    key_parts = _program_key_parts(metadata.get("program_key"))
    return first_non_empty_string(metadata.get("level"), metadata.get("degree_level"), key_parts.get("degree_level"))


def _metadata_ects(metadata: Dict[str, Any]) -> int | float | str | None:
    key_parts = _program_key_parts(metadata.get("program_key"))
    return metadata.get("ects_points") if metadata.get("ects_points") is not None else (
        metadata.get("total_ects") if metadata.get("total_ects") is not None else key_parts.get("total_ects")
    )


def first_non_empty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, str):
            text = str(value).strip()
            if text:
                return text
    return None


def _canonical_program_key(faculty: Any, degree: Any, ects: Any, program_name: Any) -> str | None:
    if not program_name:
        return None
    degree_norm = _normalize_text(degree)
    program_norm = _normalize_text(program_name)
    faculty_norm = _normalize_text(faculty)
    try:
        ects_norm = str(int(float(ects))) if ects is not None else ""
    except Exception:
        ects_norm = ""
    if not degree_norm or not ects_norm or not program_norm:
        return None
    return f"{faculty_norm}|{degree_norm}|{ects_norm}|{program_norm}"


def _program_query_text(question: str, degree: str | None = None, ects: int | None = None) -> str:
    """Remove common question scaffolding so fuzzy matching sees the programme name."""
    text = _normalize_text(question)
    # Remove degree/ECTS and common question/action words in supported languages.
    removable = set(_QUERY_STOPWORDS) | {
        "bachelors", "masters", "bachelorstudiengang", "masterstudiengang",
        "unterrichten", "unterricht", "belegen", "belegt", "lernen", "enthält", "enthaelt",
        "taught", "teach", "teaches", "included", "contain", "contains", "available",
        "etudier", "enseigne", "enseignes", "enseignes", "proposes", "propose",
    }
    tokens = [t for t in text.split() if t not in removable]
    if degree:
        tokens = [t for t in tokens if t != degree.lower()]
    if ects is not None:
        tokens = [t for t in tokens if t != str(ects) and t != "ects"]
    return " ".join(tokens).strip()


def _program_aliases_from_api(program: Dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for key in ("name", "name_en", "name_de", "name_fr"):
        value = program.get(key)
        if value and str(value) not in aliases:
            aliases.append(str(value))
    return aliases


def _fetch_programs_from_backend() -> list[Dict[str, Any]]:
    try:
        response = requests.get(f"{settings.backend_api_base}/programs/", timeout=5)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []
    except Exception as exc:
        print(f"[warn] backend programme resolver unavailable: {exc}")
        return []


def _score_api_program(question: str, program_query: str, degree: str | None, ects: int | None, program: Dict[str, Any]) -> float:
    q_norm = _normalize_text(program_query or question)
    q_tokens = _tokens(program_query or question)
    aliases = _program_aliases_from_api(program)
    alias_norms = [_normalize_text(a) for a in aliases if a]
    alias_tokens = set().union(*(_tokens(a) for a in aliases)) if aliases else set()

    score = 0.0
    if any(a and a == q_norm for a in alias_norms):
        score += 100.0
    if any(a and (a in q_norm or q_norm in a) for a in alias_norms):
        score += 55.0

    overlap = q_tokens & alias_tokens
    score += len(overlap) * 12.0
    for token in overlap:
        if len(token) >= 8:
            score += 5.0

    if degree and str(program.get("degree_level", "")).lower() == degree.lower():
        score += 20.0
    elif degree and program.get("degree_level"):
        score -= 25.0

    if ects is not None:
        try:
            if int(float(program.get("total_ects"))) == ects:
                score += 25.0
            else:
                score -= 8.0
        except Exception:
            pass
    else:
        # If the user says simply "Bachelor in X" or "Master in X", prefer the main programme.
        ptype = str(program.get("program_type") or "").lower()
        total = program.get("total_ects")
        if degree == "Bachelor" and (ptype == "mono" or total == 180):
            score += 8.0
        if degree == "Master" and ptype in {"major", "mono"}:
            score += 6.0

    return score


def _resolve_program_via_backend(question: str, degree: str | None, ects: int | None) -> Dict[str, Any] | None:
    programs = _fetch_programs_from_backend()
    if not programs:
        return None

    program_query = _program_query_text(question, degree=degree, ects=ects)
    scored = [(_score_api_program(question, program_query, degree, ects, p), p) for p in programs]
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]

    if best_score < 40.0:
        return None

    faculty = best.get("faculty_name")
    canonical_key = _canonical_program_key(faculty, best.get("degree_level"), best.get("total_ects"), best.get("name_en") or best.get("name"))
    return {
        "program_id": best.get("program_id"),
        "program_name": best.get("name_en") or best.get("name"),
        "program_aliases": _program_aliases_from_api(best),
        "program_key": canonical_key,
        "degree_level": best.get("degree_level"),
        "total_ects": best.get("total_ects"),
        "faculty": faculty,
        "resolver": "backend_api",
        "resolver_score": best_score,
        "program_query": program_query,
    }

def _metadata_value_as_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(v) for v in value)
    return str(value or "")


def _iter_docstore_docs(db: FAISS) -> Iterable[Document]:
    docstore_dict = getattr(getattr(db, "docstore", None), "_dict", {}) or {}
    return docstore_dict.values()


def _program_identity(metadata: Dict[str, Any]) -> str | None:
    program_key = metadata.get("program_key")
    if program_key:
        return f"key::{program_key}"

    name = _metadata_program_name(metadata)
    degree = _metadata_degree(metadata)
    ects = _metadata_ects(metadata)
    if name:
        return f"name::{name}::{degree}::{ects}"

    return None


def _build_program_catalog(db: FAISS) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}

    for doc in _iter_docstore_docs(db):
        md = doc.metadata or {}
        identity = _program_identity(md)
        if not identity:
            continue

        key_parts = _program_key_parts(md.get("program_key"))
        derived_program_names = _metadata_program_names(md)
        derived_program_name = derived_program_names[0] if derived_program_names else None
        derived_degree = _metadata_degree(md)
        derived_ects = _metadata_ects(md)
        derived_faculty = md.get("faculty") or key_parts.get("faculty")

        entry = grouped.setdefault(
            identity,
            {
                "identity": identity,
                "program_name": derived_program_name,
                "program_aliases": derived_program_names,
                "program_key": md.get("program_key"),
                "degree_level": derived_degree,
                "total_ects": derived_ects,
                "faculty": derived_faculty,
                "metadata_texts": [],
                "content_samples": [],
            },
        )

        for field in (
            "programme_name_en",
            "programme_name_de",
            "programme_name_fr",
            "program_name",
            "program_key",
            "title",
            "doc_label",
            "faculty",
            "level",
            "ects_points",
            "degree_level",
            "total_ects",
            "source_file",
            "source_url",
            "curriculum_url",
            "language",
            "language_name",
        ):
            if md.get(field) is not None:
                entry["metadata_texts"].append(_metadata_value_as_text(md.get(field)))

        for derived in (*derived_program_names, derived_degree, derived_ects, derived_faculty):
            if derived is not None:
                entry["metadata_texts"].append(_metadata_value_as_text(derived))

        if len(entry["content_samples"]) < 8:
            entry["content_samples"].append(doc.page_content[:1800])

    catalog: List[Dict[str, Any]] = []

    for entry in grouped.values():
        alias_text = " ".join(entry["metadata_texts"] + entry["content_samples"])
        entry["alias_text"] = _normalize_text(alias_text)
        entry["alias_tokens"] = _tokens(alias_text)
        catalog.append(entry)

    return catalog


def _detect_degree(question: str) -> str | None:
    q = _normalize_text(question)
    for degree, aliases in _DEGREE_ALIASES.items():
        if any(alias in q for alias in aliases):
            return degree
    return None


def _detect_total_ects(question: str) -> int | None:
    q = _normalize_text(question)
    match = re.search(r"\b(30|60|90|120|180)\s*ects\b", q)
    if match:
        return int(match.group(1))
    return None


def _detect_year(question: str) -> str | None:
    q = _normalize_text(question)
    for year, aliases in _YEAR_ALIASES.items():
        if any(_normalize_text(alias) in q for alias in aliases):
            return year
    return None


def _score_program_match(
    question: str,
    entry: Dict[str, Any],
    degree: str | None,
    ects: int | None,
) -> float:
    q_norm = _normalize_text(question)
    q_program = _program_query_text(question, degree=degree, ects=ects)
    q_program_norm = _normalize_text(q_program)
    q_tokens = _tokens(q_program or question)
    alias_text = entry.get("alias_text", "")
    alias_tokens = entry.get("alias_tokens", set())

    score = 0.0

    program_key_norm = _normalize_text(entry.get("program_key"))
    key_parts = _program_key_parts(entry.get("program_key"))
    candidate_names = {
        *(_normalize_text(name) for name in entry.get("program_aliases", []) if name),
        _normalize_text(entry.get("program_name")),
        _normalize_text(key_parts.get("program_name")),
    }

    # Treat structured multilingual programme-name metadata as authoritative.
    for candidate_name in candidate_names:
        if not candidate_name:
            continue
        if candidate_name == q_program_norm:
            score += 120.0
        elif candidate_name in q_program_norm or q_program_norm in candidate_name:
            score += 70.0
        else:
            name_tokens = set(candidate_name.split()) - _QUERY_STOPWORDS
            overlap = q_tokens & name_tokens
            if overlap:
                score += len(overlap) * 18.0
                if name_tokens and len(overlap) / max(len(name_tokens), 1) >= 0.7:
                    score += 35.0

    if program_key_norm and program_key_norm in q_norm:
        score += 40.0

    # Low-weight fallback: content/title overlap. This should not beat exact key-name matches.
    overlap = q_tokens & alias_tokens
    score += float(len(overlap) * 2)

    if degree and str(entry.get("degree_level") or "").lower() == degree.lower():
        score += 20.0
    elif degree and entry.get("degree_level"):
        score -= 25.0

    if ects is not None:
        try:
            if int(float(entry.get("total_ects"))) == ects:
                score += 25.0
            else:
                score -= 6.0
        except Exception:
            pass
    else:
        # When no ECTS are given, prefer the main programme over minors.
        try:
            total = int(float(entry.get("total_ects")))
            if degree == "Bachelor" and total == 180:
                score += 8.0
            if degree == "Master" and total in {90, 120}:
                score += 6.0
        except Exception:
            pass

    return score

def _find_catalog_entry_for_resolved_program(
    catalog: List[Dict[str, Any]],
    resolved: Dict[str, Any],
) -> Dict[str, Any] | None:
    wanted_name = _normalize_text(resolved.get("program_name"))
    wanted_degree = str(resolved.get("degree_level") or "").lower()
    wanted_ects = resolved.get("total_ects")
    wanted_key = _normalize_text(resolved.get("program_key"))

    scored: list[tuple[float, Dict[str, Any]]] = []
    for entry in catalog:
        key_parts = _program_key_parts(entry.get("program_key"))
        entry_names = [_normalize_text(n) for n in entry.get("program_aliases", []) if n]
        key_name = _normalize_text(key_parts.get("program_name"))
        if key_name:
            entry_names.append(key_name)
        entry_name = entry_names[0] if entry_names else ""
        entry_degree = str(entry.get("degree_level") or key_parts.get("degree_level") or "").lower()
        entry_ects = entry.get("total_ects") if entry.get("total_ects") is not None else key_parts.get("total_ects")
        entry_key = _normalize_text(entry.get("program_key"))

        score = 0.0
        if wanted_key and entry_key == wanted_key:
            score += 120.0
        if wanted_name and any(name == wanted_name for name in entry_names):
            score += 80.0
        elif wanted_name and any(wanted_name in name or name in wanted_name for name in entry_names):
            score += 45.0
        if wanted_degree and entry_degree == wanted_degree:
            score += 25.0
        elif wanted_degree and entry_degree:
            score -= 40.0
        try:
            if wanted_ects is not None and entry_ects is not None and int(float(wanted_ects)) == int(float(entry_ects)):
                score += 30.0
            elif wanted_ects is not None and entry_ects is not None:
                score -= 8.0
        except Exception:
            pass
        scored.append((score, entry))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1] if scored[0][0] >= 70.0 else None


def _detect_program(
    question: str,
    db: FAISS,
    degree: str | None = None,
    ects: int | None = None,
) -> Dict[str, Any] | None:
    catalog = _build_program_catalog(db)
    if not catalog:
        return None

    # Preferred path: resolve multilingual programme names through the structured backend API,
    # then map the resolved programme to the programme_key in FAISS.
    resolved = _resolve_program_via_backend(question, degree=degree, ects=ects)
    if resolved:
        matched_entry = _find_catalog_entry_for_resolved_program(catalog, resolved)
        if matched_entry:
            merged = dict(matched_entry)
            merged.update({
                "resolver": resolved.get("resolver"),
                "resolver_score": resolved.get("resolver_score"),
                "program_id": resolved.get("program_id"),
                "program_aliases": resolved.get("program_aliases"),
                "program_query": resolved.get("program_query"),
            })
            return merged

    # Fallback: use FAISS metadata only. The programme-name part of program_key is authoritative.
    scored = [
        (_score_program_match(question, entry, degree, ects), entry)
        for entry in catalog
    ]
    scored.sort(key=lambda item: item[0], reverse=True)

    best_score, best_entry = scored[0]

    if best_score < 40.0:
        return None

    result = dict(best_entry)
    result["resolver"] = "faiss_metadata"
    result["resolver_score"] = best_score
    result["program_query"] = _program_query_text(question, degree=degree, ects=ects)
    return result

def _metadata_matches_program(metadata: Dict[str, Any], program: Dict[str, Any]) -> bool:
    if not program:
        return True

    md_parts = _program_key_parts(metadata.get("program_key"))
    wanted_parts = _program_key_parts(program.get("program_key"))

    wanted_key = _normalize_text(program.get("program_key"))
    md_key = _normalize_text(metadata.get("program_key"))

    if wanted_key and md_key and md_key == wanted_key:
        return True

    wanted_names = [_normalize_text(n) for n in program.get("program_aliases", []) if n]
    wanted_primary = _normalize_text(program.get("program_name") or wanted_parts.get("program_name"))
    if wanted_primary:
        wanted_names.append(wanted_primary)
    md_names = [_normalize_text(n) for n in _metadata_program_names(metadata)]

    wanted_degree = str(program.get("degree_level") or wanted_parts.get("degree_level") or "").lower()
    md_degree = str(_metadata_degree(metadata) or "").lower()

    wanted_ects = program.get("total_ects") if program.get("total_ects") is not None else wanted_parts.get("total_ects")
    md_ects = _metadata_ects(metadata)

    if wanted_names and md_names and set(wanted_names) & set(md_names):
        if wanted_degree and md_degree and wanted_degree != md_degree:
            return False
        try:
            if wanted_ects is not None and md_ects is not None and int(float(wanted_ects)) != int(float(md_ects)):
                return False
        except Exception:
            pass
        return True

    return False

def _metadata_matches_degree(metadata: Dict[str, Any], degree: str | None) -> bool:
    if not degree:
        return True
    md_degree = _metadata_degree(metadata)
    return md_degree in (None, degree)


def _metadata_matches_ects(metadata: Dict[str, Any], ects: int | None) -> bool:
    if ects is None:
        return True
    md_ects = _metadata_ects(metadata)
    try:
        return md_ects is None or int(float(md_ects)) == int(float(ects))
    except Exception:
        return True


def _doc_matches_year(doc: Document, year: str | None) -> bool:
    if not year:
        return True

    section = _normalize_text(doc.metadata.get("section"))
    content = _normalize_text(doc.page_content)

    patterns = [
        f"{year} jahr",
        f"{year} studienjahr",
    ]

    if year == "1":
        patterns += ["erstes studienjahr", "ersten studienjahr", "erstes jahr", "ersten jahr", "first year"]
    elif year == "2":
        patterns += ["zweites studienjahr", "zweiten studienjahr", "zweites jahr", "zweiten jahr", "second year"]
    elif year == "3":
        patterns += ["drittes studienjahr", "dritten studienjahr", "drittes jahr", "dritten jahr", "third year"]

    return any(pattern in section or pattern in content for pattern in patterns)


def _enrich_query(
    question: str,
    program: Dict[str, Any] | None,
    degree: str | None,
    ects: int | None,
    year: str | None,
) -> str:
    hints = [question]

    if program:
        hints.append(f"program_name: {program.get('program_name')}")
        aliases = program.get("program_aliases") or []
        if aliases:
            hints.append("program_aliases: " + " | ".join(str(a) for a in aliases))
        if program.get("program_key"):
            hints.append(f"program_key: {program.get('program_key')}")

    if degree:
        hints.append(f"level: {degree}")

    if ects is not None:
        hints.append(f"ects_points: {ects}")

    if year:
        hints.append(f"section: {year}. Jahr {year}. Studienjahr first year second year third year")

    return "\n".join(hints)


def _retrieve_metadata_aware(db: FAISS, question: str, k: int) -> Tuple[List[Document], Dict[str, Any]]:
    degree = _detect_degree(question)
    ects = _detect_total_ects(question)
    year = _detect_year(question)
    program = _detect_program(question, db, degree=degree, ects=ects)

    debug_info = {
        "detected_program_name": program.get("program_name") if program else None,
        "detected_program_key": program.get("program_key") if program else None,
        "detected_program_resolver": program.get("resolver") if program else None,
        "detected_program_score": program.get("resolver_score") if program else None,
        "detected_program_query": program.get("program_query") if program else None,
        "detected_degree": degree,
        "detected_ects": ects,
        "detected_year": year,
        "retrieval_mode": "metadata_first",
    }

    all_docs = list(_iter_docstore_docs(db))

    candidates = [
        d for d in all_docs
        if (not program or _metadata_matches_program(d.metadata or {}, program))
        and _metadata_matches_degree(d.metadata or {}, degree)
        and _metadata_matches_ects(d.metadata or {}, ects)
    ]

    debug_info["candidate_count_after_program_degree_ects"] = len(candidates)

    if program and not candidates:
        debug_info["error"] = "Programme was detected, but no FAISS documents matched its metadata."
        return [], debug_info

    if not program:
        enriched_query = _enrich_query(question, program, degree, ects, year)
        fetch_k = max(k * 8, 40)
        candidates = db.as_retriever(search_kwargs={"k": fetch_k}).invoke(enriched_query)
        debug_info["candidate_count_global_fallback"] = len(candidates)

    year_candidates = candidates

    if year:
        filtered_by_year = [d for d in candidates if _doc_matches_year(d, year)]
        debug_info["candidate_count_after_year_filter"] = len(filtered_by_year)

        if filtered_by_year:
            year_candidates = filtered_by_year
        else:
            year_candidates = candidates
            debug_info["year_filter_warning"] = (
                "No candidate matched the detected year; kept programme-matching candidates only."
            )

    q_tokens = _tokens(question)

    def score_doc(doc: Document) -> float:
        md = doc.metadata or {}
        text = _normalize_text(doc.page_content + " " + json.dumps(md, ensure_ascii=False))

        score = 0.0

        if program and _metadata_matches_program(md, program):
            score += 100.0

        md_degree = _metadata_degree(md)
        md_ects = _metadata_ects(md)

        if degree and md_degree == degree:
            score += 20.0

        try:
            if ects is not None and md_ects is not None and int(float(md_ects)) == int(float(ects)):
                score += 10.0
        except Exception:
            pass

        if year and _doc_matches_year(doc, year):
            score += 40.0

        if md.get("chunk_type") == "table":
            score += 20.0

        if md.get("contains_table"):
            score += 20.0

        if re.search(r"\b[A-ZÄÖÜ]{2,4}\.\d{5}\b", doc.page_content):
            score += 15.0

        doc_tokens = set(text.split())
        score += len(q_tokens & doc_tokens) * 2.0

        doc_label = _normalize_text(md.get("doc_label"))
        if "studienplan" in doc_label:
            score += 10.0

        return score

    docs = sorted(year_candidates, key=score_doc, reverse=True)[:k]

    debug_info["returned_count"] = len(docs)
    debug_info["returned_programs"] = sorted({
        str(_metadata_program_name(d.metadata or {}) or _program_key_parts((d.metadata or {}).get("program_key")).get("program_name"))
        for d in docs
    })

    for d in docs:
        d.metadata["retrieval_debug"] = debug_info

    return docs, debug_info


def _available_language_codes(db: FAISS) -> list[str]:
    codes: set[str] = set()

    for doc in _iter_docstore_docs(db):
        code = _normalize_language_code(doc.metadata.get("language"))
        if code:
            codes.add(code)

    return sorted(codes)


def _translate_query(question: str, target_language_code: str, llm: ChatOllama) -> str:
    target_language = _language_name(target_language_code)

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "Translate the user question for document retrieval. "
                "Preserve course codes, abbreviations, names, numbers, dates, ECTS values, and URLs exactly. "
                "Return only the translated question, with no explanation.",
            ),
            ("human", f"Target language: {target_language}\nQuestion: {{question}}"),
        ]
    )

    try:
        response = llm.invoke(prompt.format_messages(question=question))
        translated = str(response.content).strip().strip('"')
        return translated or question
    except Exception as exc:
        print(f"[warn] query translation failed for {target_language_code}: {exc}")
        return question


def _retrieve_metadata_and_language_aware(
    db: FAISS,
    question: str,
    k: int,
    language: str | None = None,
) -> Tuple[List[Document], Dict[str, Any]]:
    llm = ChatOllama(
        model=settings.ollama_model,
        temperature=0,
        base_url=settings.ollama_host,
    )

    requested_language = _normalize_language_code(language)

    queries: list[tuple[str, str | None]] = [(question, requested_language)]

    for code in _available_language_codes(db):
        translated = _translate_query(question, code, llm)
        if translated and translated.lower().strip() != question.lower().strip():
            queries.append((translated, code))

    merged: list[Document] = []
    seen: set[str] = set()
    debug_runs = []

    for query, query_language in queries:
        docs, debug_info = _retrieve_metadata_aware(db, query, k)

        debug_info = dict(debug_info)
        debug_info["query"] = query
        debug_info["query_language"] = query_language
        debug_runs.append(debug_info)

        for doc in docs:
            doc_language = _normalize_language_code(doc.metadata.get("language"))

            if query_language and doc_language and doc_language != query_language:
                continue

            key = (
                doc.metadata.get("chunk_id")
                or f"{doc.metadata.get('source')}:{doc.metadata.get('page')}:{doc.page_content[:80]}"
            )

            if key in seen:
                continue

            seen.add(key)
            merged.append(doc)

            if len(merged) >= k:
                return merged, {
                    "retrieval_mode": "metadata_and_language_aware",
                    "requested_language": requested_language,
                    "runs": debug_runs,
                    "returned_count": len(merged),
                }

    return merged, {
        "retrieval_mode": "metadata_and_language_aware",
        "requested_language": requested_language,
        "runs": debug_runs,
        "returned_count": len(merged),
    }


def _load_parsed_file(path: str, category: str) -> List[Document]:
    raw = Path(path).read_text(encoding="utf-8")

    meta_match = META_RE.search(raw)
    if not meta_match:
        raise ValueError(f"Missing metadata block in {path}")

    meta = json.loads(meta_match.group(1))
    docs: List[Document] = []

    for page_num, page_text in PAGE_RE.findall(raw):
        text = page_text.strip()
        if not text:
            continue

        source_name = Path(
            meta.get("local_path")
            or meta.get("pdf_url")
            or meta.get("document_page_url")
            or path
        ).name

        docs.append(
            Document(
                page_content=text,
                metadata={
                    "sha256": meta.get("sha256"),
                    "doc_key": meta.get("doc_key") or meta.get("reg_doc_key"),
                    "source_url": meta.get("source_url") or meta.get("pdf_url") or meta.get("document_page_url"),
                    "local_path": meta.get("local_path"),
                    "source": source_name,
                    "source_file": source_name,
                    "page": int(page_num),
                    "category": category,
                    "source_type": "pdf",

                    # Preserve richer metadata if your parser generated it.
                    "language": meta.get("language"),
                    "language_name": meta.get("language_name"),
                    "programme_name_en": meta.get("programme_name_en"),
                    "programme_name_de": meta.get("programme_name_de"),
                    "programme_name_fr": meta.get("programme_name_fr"),
                    "program_name": meta.get("program_name"),
                    "program_key": meta.get("program_key"),
                    "level": meta.get("level"),
                    "ects_points": meta.get("ects_points"),
                    "degree_level": meta.get("degree_level"),
                    "total_ects": meta.get("total_ects"),
                    "doc_label": meta.get("doc_label"),
                    "faculty": meta.get("faculty"),
                    "curriculum_url": meta.get("curriculum_url"),
                    "title": meta.get("title"),
                },
            )
        )

    if not docs:
        raise ValueError(f"No page content found in {path}")

    return docs


def _load_parsed_documents(parsed_paths: List[str], category: str) -> List[Document]:
    docs: List[Document] = []
    failed = []

    for path in parsed_paths:
        try:
            docs.extend(_load_parsed_file(path, category))
        except Exception as e:
            failed.append((path, str(e)))
            print(f"[warn] Failed to load parsed file: {path} -> {type(e).__name__}: {e!r}")

    print(
        f"[index] category={category} pages_loaded={len(docs)} "
        f"failed_files={len(failed)} total_files={len(parsed_paths)}"
    )

    if not docs:
        raise RuntimeError(f"No readable parsed files found for category '{category}'")

    return docs


def build_or_load_index_for(subfolder: str, index_dir: Path, force_rebuild: bool = False) -> FAISS:
    index_path = Path(index_dir)
    index_path.mkdir(parents=True, exist_ok=True)

    faiss_file = index_path / "index.faiss"
    pkl_file = index_path / "index.pkl"

    if not force_rebuild and faiss_file.exists() and pkl_file.exists():
        print(f"[index] Loading existing index for {subfolder} from {index_path}")
        return FAISS.load_local(
            str(index_path),
            _EMBEDDINGS,
            allow_dangerous_deserialization=True,
        )

    if subfolder == "studyplans":
        parsed_folder = settings.studyplans_parsed
    elif subfolder == "reglementations":
        parsed_folder = settings.reglementations_parsed
    else:
        raise ValueError(f"Unknown subfolder: {subfolder}")

    parsed_paths = [str(p) for p in parsed_folder.rglob("*.txt")]

    if not parsed_paths:
        raise FileNotFoundError(f"No parsed files found in {parsed_folder}")

    documents = _load_parsed_documents(parsed_paths, category=subfolder)
    chunks = _SPLITTER.split_documents(documents)

    print(f"[index] Building FAISS for {subfolder}: docs={len(documents)} chunks={len(chunks)}")
    db = FAISS.from_documents(chunks, _EMBEDDINGS)
    db.save_local(str(index_path))
    return db


def debug_chunk_file(path: str, category: str = "studyplans", limit: int = 10):
    docs = _load_parsed_file(path, category)
    chunks = _SPLITTER.split_documents(docs)

    print(f"pages={len(docs)} chunks={len(chunks)}")
    for i, ch in enumerate(chunks[:limit]):
        print("=" * 80)
        print(f"chunk {i}")
        print(ch.metadata)
        print(ch.page_content[:1200])


def answer_question(
    db: FAISS,
    question: str,
    k: int | None = None,
    language: str | None = None,
) -> Tuple[str, List[dict]]:
    language = detect_request_language(question, language)
    final_k = k or settings.k

    with timed_step("rag.retrieve", k=final_k):
        docs, retrieval_debug = _retrieve_metadata_and_language_aware(
            db=db,
            question=question,
            k=final_k,
            language=language,
        )

    if not docs:
        fallback_by_language = {
            "de": "Ich habe in den Dokumenten keine passenden Quellen zur Frage gefunden.",
            "fr": "Je n'ai trouvé aucune source pertinente dans les documents pour répondre à la question.",
            "en": "I could not find matching sources in the documents for this question.",
            "it": "Non ho trovato fonti pertinenti nei documenti per rispondere alla domanda.",
            "es": "No encontré fuentes relevantes en los documentos para responder a la pregunta.",
        }
        code = _normalize_language_code(language) or "en"
        return fallback_by_language.get(code, fallback_by_language["en"]), []

    context_parts = []

    for d in docs:
        src = d.metadata.get("source") or d.metadata.get("source_file") or "document"
        page = d.metadata.get("page") or d.metadata.get("page_start")
        lang = d.metadata.get("language_name") or d.metadata.get("language")
        cite = f"[{src} p.{page if isinstance(page, int) else '?'}" + (f", {lang}" if lang else "") + "]"

        md = d.metadata or {}
        metadata_header = (
            "Metadata: "
            f"programme_name_en={md.get('programme_name_en')}; "
            f"programme_name_de={md.get('programme_name_de')}; "
            f"programme_name_fr={md.get('programme_name_fr')}; "
            f"program_name={md.get('program_name')}; "
            f"program_key={md.get('program_key')}; "
            f"level={md.get('level') or md.get('degree_level')}; "
            f"ects_points={md.get('ects_points') if md.get('ects_points') is not None else md.get('total_ects')}; "
            f"section={md.get('section')}; "
            f"chunk_type={md.get('chunk_type')}; "
            f"language={md.get('language')}; "
            f"language_name={md.get('language_name')}"
        )

        language_note = f"Document language: {lang}" if lang else "Document language: unknown"
        context_parts.append(f"{cite}\n{language_note}\n{metadata_header}\n{d.page_content}")

    context = "\n\n".join(context_parts)

    llm = ChatOllama(
        model=settings.ollama_model,
        temperature=0,
        base_url=settings.ollama_host,
    )

    prompt = _build_prompt(language)
    msg = prompt.format_messages(question=question, context=context)
    with timed_step("rag.llm_answer"):
        resp = llm.invoke(msg)

    sources = []

    for d in docs:
        page = d.metadata.get("page") or d.metadata.get("page_start")
        metadata = dict(d.metadata)
        metadata["retrieval_debug"] = retrieval_debug

        sources.append(
            {
                "source": d.metadata.get("source") or d.metadata.get("source_file") or "document",
                "page": page if isinstance(page, int) else None,
                "language": d.metadata.get("language"),
                "snippet": (d.page_content[:350] + "…") if len(d.page_content) > 350 else d.page_content,
                "metadata": metadata,
                "source_type": d.metadata.get("source_type", "pdf"),
            }
        )

    answer = ensure_answer_language(str(resp.content or ""), language)
    return answer, sources


def debug_find_chunks_for_doc(
    db: FAISS,
    doc_key: str,
    contains: str | None = None,
    limit: int = 20,
):
    matches = []

    for d in _iter_docstore_docs(db):
        if d.metadata.get("doc_key") == doc_key:
            if contains is None or contains.lower() in d.page_content.lower():
                matches.append(
                    {
                        "source": d.metadata.get("source"),
                        "page": d.metadata.get("page"),
                        "snippet": d.page_content[:800],
                    }
                )

            if len(matches) >= limit:
                break

    return matches