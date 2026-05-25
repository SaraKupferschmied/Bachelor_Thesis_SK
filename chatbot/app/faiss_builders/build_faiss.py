from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings

from app.config import settings


def _chunk_dir_for(target: str) -> Path:
    """
    Adjust these paths to match your project structure.
    """
    root = Path(__file__).resolve().parents[2] / "scrapy_crawler" / "outputs"

    if target == "studyplans":
        return root / "parsed_chunks"
    if target in {"regulations", "reglementations"}:
        return root / "parsed_chunks_regulations"

    raise ValueError(f"Unknown target: {target}")


def _index_dir_for(target: str) -> Path:
    if target == "studyplans":
        return settings.studyplans_index
    if target in {"regulations", "reglementations"}:
        return settings.reglementations_index

    raise ValueError(f"Unknown target: {target}")


def _iter_chunk_files(chunks_dir: Path) -> Iterable[Path]:
    if not chunks_dir.exists():
        raise FileNotFoundError(f"Chunks directory not found: {chunks_dir}")

    for path in sorted(chunks_dir.glob("*.jsonl")):
        if path.name.startswith("_"):
            continue
        yield path


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _is_useful_chunk(obj: dict) -> bool:
    text = _truncate_text(_normalize_text(obj["text"]), max_chars=4000)
    if not text:
        return False

    chunk_type = obj.get("chunk_type")
    if chunk_type not in {"page", "section", "course_row"}:
        return False

    # Filter very tiny junk chunks
    if len(text) < 20:
        return False

    return True

def _split_large_doc(doc: Document, max_chars: int = 1200, overlap: int = 150) -> list[Document]:
    text = (doc.page_content or "").strip()
    if len(text) <= max_chars:
        return [doc]

    out: list[Document] = []
    start = 0

    while start < len(text):
        end = min(len(text), start + max_chars)
        chunk_text = text[start:end].strip()

        meta = dict(doc.metadata)
        meta["subchunk_start"] = start
        meta["subchunk_end"] = end

        out.append(Document(page_content=chunk_text, metadata=meta))

        if end >= len(text):
            break

        start = max(0, end - overlap)

    return out

def _load_documents_from_jsonl(chunks_dir: Path) -> list[Document]:
    docs: list[Document] = []
    seen_ids: set[str] = set()

    for file_path in _iter_chunk_files(chunks_dir):
        with file_path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"[warn] invalid json in {file_path.name}:{line_no}: {e}")
                    continue

                if not _is_useful_chunk(obj):
                    continue

                chunk_id = str(obj.get("chunk_id") or "")
                if chunk_id and chunk_id in seen_ids:
                    continue
                if chunk_id:
                    seen_ids.add(chunk_id)

                text = _truncate_text(_normalize_text(obj["text"]), max_chars=4000)

                metadata = {
                    "chunk_id": obj.get("chunk_id"),
                    "doc_key": obj.get("doc_key"),
                    "program_key": obj.get("program_key"),
                    "source_url": obj.get("source_url"),
                    "local_path": obj.get("local_path"),
                    "sha256": obj.get("sha256"),
                    "title": obj.get("title"),
                    "faculty": obj.get("faculty"),
                    "degree_level": obj.get("degree_level"),
                    "total_ects": obj.get("total_ects"),
                    "program_name": obj.get("program_name"),
                    "doc_label": obj.get("doc_label"),
                    "source_type": obj.get("source_type"),
                    "page": obj.get("page"),
                    "chunk_type": obj.get("chunk_type"),
                    "section": obj.get("section"),
                    "subsection": obj.get("subsection"),
                    "prev_chunk_id": obj.get("prev_chunk_id"),
                    "next_chunk_id": obj.get("next_chunk_id"),
                    "parent_section_id": obj.get("parent_section_id"),
                    "source_file": file_path.name,
                }

                base_doc = Document(
                    page_content=text,
                    metadata=metadata,
                )
                docs.extend(_split_large_doc(base_doc))
                

    return docs

def _truncate_text(text: str, max_chars: int = 4000) -> str:
    text = " ".join((text or "").split()).strip()
    return text[:max_chars]

def _build_faiss_index(documents: list[Document], index_dir: Path, batch_size: int = 128) -> FAISS:
    if not documents:
        raise RuntimeError("No documents found to index.")

    index_dir.mkdir(parents=True, exist_ok=True)

    embeddings = OllamaEmbeddings(
        model=settings.ollama_embedding_model,
        base_url=settings.ollama_host,
    )

    db: FAISS | None = None

    total = len(documents)
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = documents[start:end]
        print(f"[embed] batch {start}-{end} / {total}")

        if db is None:
            db = FAISS.from_documents(batch, embeddings)
        else:
            db.add_documents(batch)

    assert db is not None
    db.save_local(str(index_dir))
    return db


def build_index_for(target: str, parser: str = "default", force_rebuild: bool = False) -> FAISS:
    chunks_dir = _chunk_dir_for(target)
    index_dir = _index_dir_for(target)

    if index_dir.exists() and not force_rebuild:
        print(f"[info] index already exists: {index_dir}")
        print("[info] use --force to rebuild")
        embeddings = OllamaEmbeddings(
            model=settings.ollama_embedding_model,
            base_url=settings.ollama_host,
        )
        return FAISS.load_local(
            str(index_dir),
            embeddings,
            allow_dangerous_deserialization=True,
        )

    documents = _load_documents_from_jsonl(chunks_dir)
    print(f"[info] loaded {len(documents)} chunks from {chunks_dir}")

    # Small debug stats
    type_counts: dict[str, int] = {}
    for d in documents:
        t = str(d.metadata.get("chunk_type"))
        type_counts[t] = type_counts.get(t, 0) + 1

    print("[info] chunk types:")
    for k, v in sorted(type_counts.items()):
        print(f"  - {k}: {v}")

    db = _build_faiss_index(documents, index_dir)
    return db


def main() -> None:
    parser = argparse.ArgumentParser(description="Build FAISS indexes from parsed JSONL chunks.")
    parser.add_argument(
        "--target",
        choices=["all", "studyplans", "regulations", "reglementations"],
        default="all",
        help="Which index to build.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rebuild even if index already exists.",
    )
    args = parser.parse_args()

    targets: list[str] = []
    if args.target in {"all", "studyplans"}:
        targets.append("studyplans")
    if args.target in {"all", "regulations", "reglementations"}:
        targets.append("regulations")

    for target in targets:
        print(f"\n=== Building {target} index ===")
        db = build_index_for(target, force_rebuild=args.force)
        print(f"[done] {target} -> {_index_dir_for(target)} ({len(db.index_to_docstore_id)} vectors)")


if __name__ == "__main__":
    main()