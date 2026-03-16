"""
Ollama-based RAG for regulations + study plans.

- Loads parsed PDFs from ./parsed/<subfolder>
- Splits into chunks
- Creates/loads persisted FAISS indexes
- Answers questions via ChatOllama using ONLY retrieved context
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Tuple

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


def _language_name(language: str | None) -> str:
    if language == "de":
        return "German"
    if language == "fr":
        return "French"
    return "English"


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
                "The retrieved documents may be in German, French, English, or Italian; use them all if relevant. "
                "Even if the documents are written in another language, the final answer must be in the requested language. "
                "Always cite sources as [filename p.X].",
            ),
            ("human", "Question: {question}\n\nContext:\n{context}\n\nAnswer with citations:"),
        ]
    )


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
                    "page": int(page_num),
                    "category": category,
                    "source_type": "pdf",
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
    retriever = db.as_retriever(search_kwargs={"k": k or settings.k})
    docs = retriever.invoke(question)

    context_parts = []
    for d in docs:
        src = d.metadata.get("source", "document")
        page = d.metadata.get("page")
        cite = f"[{src} p.{page if isinstance(page, int) else '?'}]"
        context_parts.append(f"{cite}\n{d.page_content}")

    context = "\n\n".join(context_parts)

    llm = ChatOllama(model=settings.ollama_model, temperature=0)
    prompt = _build_prompt(language)
    msg = prompt.format_messages(question=question, context=context)
    resp = llm.invoke(msg)

    sources = []
    for d in docs:
        page = d.metadata.get("page")
        sources.append(
            {
                "source": d.metadata.get("source", "document"),
                "page": page if isinstance(page, int) else None,
                "snippet": (d.page_content[:350] + "…") if len(d.page_content) > 350 else d.page_content,
                "metadata": d.metadata,
                "source_type": d.metadata.get("source_type", "pdf"),
            }
        )

    return resp.content, sources


def debug_find_chunks_for_doc(db: FAISS, doc_key: str, contains: str | None = None, limit: int = 20):
    matches = []
    docs = db.docstore._dict.values()

    for d in docs:
        if d.metadata.get("doc_key") == doc_key:
            if contains is None or contains.lower() in d.page_content.lower():
                matches.append({
                    "source": d.metadata.get("source"),
                    "page": d.metadata.get("page"),
                    "snippet": d.page_content[:800],
                })
            if len(matches) >= limit:
                break

    return matches