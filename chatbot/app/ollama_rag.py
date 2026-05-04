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
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document

from .config import settings


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
                "You are a careful assistant for university regulations and study plans. "
                "Answer ONLY using the provided context. "
                "If the answer is not in the context, say you cannot find it in the documents. "
                f"Always answer in {target_language}. "
                f"Use natural, clear {target_language}. "
                "The retrieved documents may be in German, French, English, Italian, or Spanish; use them all if relevant. "
                "Even if the documents are written in another language, the final answer must be in the requested language. "
                "For study-plan questions, treat metadata as authoritative. "
                "Use only context chunks whose metadata matches the requested programme, degree level, ECTS amount, year, "
                "semester, or section. Ignore chunks from other programmes or degree levels, even if their wording is similar. "
                "When course rows are present, extract the course code, course title, semester, language, assessment, ECTS, "
                "and teacher if available. Do not invent missing course data. "
                "Always cite sources as [filename p.X].",
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

    name = metadata.get("program_name")
    degree = metadata.get("degree_level")
    ects = metadata.get("total_ects")
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

        entry = grouped.setdefault(
            identity,
            {
                "identity": identity,
                "program_name": md.get("program_name"),
                "program_key": md.get("program_key"),
                "degree_level": md.get("degree_level"),
                "total_ects": md.get("total_ects"),
                "metadata_texts": [],
                "content_samples": [],
            },
        )

        for field in (
            "program_name",
            "program_key",
            "title",
            "doc_label",
            "faculty",
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
    q_tokens = _tokens(question)
    alias_text = entry.get("alias_text", "")
    alias_tokens = entry.get("alias_tokens", set())

    score = 0.0

    program_name_norm = _normalize_text(entry.get("program_name"))
    program_key_norm = _normalize_text(entry.get("program_key"))

    if program_name_norm and program_name_norm in q_norm:
        score += 12.0

    if program_key_norm and program_key_norm in q_norm:
        score += 12.0

    important_query_tokens = q_tokens - {"bachelor", "master", "ects"}
    overlap = important_query_tokens & alias_tokens
    score += float(len(overlap) * 3)

    for token in overlap:
        if len(token) >= 8:
            score += 2.0

    if degree and entry.get("degree_level") == degree:
        score += 3.0
    elif degree and entry.get("degree_level") and entry.get("degree_level") != degree:
        score -= 2.0

    if ects is not None and entry.get("total_ects") == ects:
        score += 2.0

    return score


def _detect_program(
    question: str,
    db: FAISS,
    degree: str | None = None,
    ects: int | None = None,
) -> Dict[str, Any] | None:
    catalog = _build_program_catalog(db)
    if not catalog:
        return None

    scored = [
        (_score_program_match(question, entry, degree, ects), entry)
        for entry in catalog
    ]
    scored.sort(key=lambda item: item[0], reverse=True)

    best_score, best_entry = scored[0]

    if best_score < 8.0:
        return None

    return best_entry


def _metadata_matches_program(metadata: Dict[str, Any], program: Dict[str, Any]) -> bool:
    if not program:
        return True

    wanted_key = program.get("program_key")
    wanted_name = program.get("program_name")
    wanted_degree = program.get("degree_level")
    wanted_ects = program.get("total_ects")

    if wanted_key and metadata.get("program_key") == wanted_key:
        return True

    if wanted_name and metadata.get("program_name") == wanted_name:
        if wanted_degree and metadata.get("degree_level") not in (None, wanted_degree):
            return False
        if wanted_ects and metadata.get("total_ects") not in (None, wanted_ects):
            return False
        return True

    return False


def _metadata_matches_degree(metadata: Dict[str, Any], degree: str | None) -> bool:
    if not degree:
        return True
    return metadata.get("degree_level") in (None, degree)


def _metadata_matches_ects(metadata: Dict[str, Any], ects: int | None) -> bool:
    if ects is None:
        return True
    return metadata.get("total_ects") in (None, ects)


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
        if program.get("program_key"):
            hints.append(f"program_key: {program.get('program_key')}")

    if degree:
        hints.append(f"degree_level: {degree}")

    if ects is not None:
        hints.append(f"total_ects: {ects}")

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

        if degree and md.get("degree_level") == degree:
            score += 20.0

        if ects is not None and md.get("total_ects") == ects:
            score += 10.0

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
        str((d.metadata or {}).get("program_name"))
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
                    "program_name": meta.get("program_name"),
                    "program_key": meta.get("program_key"),
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
    final_k = k or settings.k

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
            f"program_name={md.get('program_name')}; "
            f"program_key={md.get('program_key')}; "
            f"degree_level={md.get('degree_level')}; "
            f"total_ects={md.get('total_ects')}; "
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

    return resp.content, sources


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