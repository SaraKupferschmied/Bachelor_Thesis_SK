from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Iterable

# Allow running this file directly with:
# python chatbot/app/create_base_faiss_vectorstore.py
CHATBOT_DIR = Path(__file__).resolve().parents[1]
if str(CHATBOT_DIR) not in sys.path:
    sys.path.insert(0, str(CHATBOT_DIR))

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings

from app.config import settings


BASE_INFO_INPUT_DIR = (
    Path(__file__).resolve().parents[2]
    / "scrapy_crawler"
    / "scrapy_crawler"
    / "spider_outputs"
    / "base_info"
)

BASE_INFO_INDEX_DIR = settings.vectorstore_dir / "faiss_BaseData"


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_metadata_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return str(value)


def _iter_json_files(input_dir: Path) -> Iterable[Path]:
    if not input_dir.exists():
        raise FileNotFoundError(f"Base-info input directory not found: {input_dir}")

    for path in sorted(input_dir.glob("*.json")):
        if not path.name.startswith("_"):
            yield path


def _load_json_file(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _base_metadata(item: dict[str, Any], file_path: Path) -> dict[str, Any]:
    keys = [
        "doc_key",
        "source_type",
        "source_url",
        "source_reference",
        "canonical_url",
        "url",
        "title",
        "page_title",
        "page_heading",
        "language",
        "category",
        "type",
        "faculty",
        "parent_url",
        "depth",
        "crawled_at",
        "retrieved_at",
        "crawler_version",
    ]

    metadata = {
        key: _safe_metadata_value(item.get(key))
        for key in keys
        if key in item and item.get(key) is not None
    }

    metadata["source_file"] = file_path.name
    metadata["parser"] = "scrapy_base_info_json"
    metadata["index_variant"] = "base_info_semantic_v1"

    if not metadata.get("source_url"):
        metadata["source_url"] = metadata.get("url") or metadata.get("canonical_url")

    return metadata


def _split_text(
    text: str,
    *,
    max_chars: int = 1400,
    overlap_chars: int = 180,
) -> list[str]:
    text = _normalize_text(text)
    if not text:
        return []

    if len(text) <= max_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        chunk = "\n\n".join(current).strip()
        if chunk:
            chunks.append(chunk)
        current = []
        current_len = 0

    for para in paragraphs:
        if len(para) > max_chars:
            flush()
            start = 0
            while start < len(para):
                end = min(start + max_chars, len(para))
                piece = para[start:end].strip()
                if piece:
                    chunks.append(piece)
                if end >= len(para):
                    break
                start = max(0, end - overlap_chars)
            continue

        projected = current_len + (2 if current else 0) + len(para)
        if current and projected > max_chars:
            flush()

        current.append(para)
        current_len += (2 if current_len else 0) + len(para)

    flush()

    if overlap_chars > 0 and len(chunks) > 1:
        with_overlap: list[str] = [chunks[0]]
        for previous, chunk in zip(chunks, chunks[1:]):
            tail = previous[-overlap_chars:].strip()
            with_overlap.append(f"Previous context: {tail}\n\n{chunk}" if tail else chunk)
        chunks = with_overlap

    return chunks


def _section_title(section: dict[str, Any]) -> str | None:
    for key in ("section_title", "title", "heading", "name"):
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _section_text(section: dict[str, Any]) -> str:
    parts: list[str] = []

    title = _section_title(section)
    if title:
        parts.append(title)

    for key in ("content", "text", "body", "full_text"):
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())

    for key in ("items", "links"):
        value = section.get(key)
        if isinstance(value, list):
            lines = []
            for entry in value:
                if isinstance(entry, str):
                    lines.append(entry)
                elif isinstance(entry, dict):
                    label = entry.get("text") or entry.get("title") or entry.get("label")
                    href = entry.get("href") or entry.get("url")
                    if label and href:
                        lines.append(f"{label}: {href}")
                    elif label:
                        lines.append(str(label))
                    elif href:
                        lines.append(str(href))
            if lines:
                parts.append("\n".join(lines))

    return _normalize_text("\n\n".join(parts))


def _documents_from_item(
    item: dict[str, Any],
    file_path: Path,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[Document]:
    docs: list[Document] = []
    base_meta = _base_metadata(item, file_path)

    doc_key = str(
        item.get("doc_key")
        or item.get("source_url")
        or item.get("url")
        or file_path.stem
    )

    previous_chunk_id: str | None = None

    sections = item.get("sections")
    if isinstance(sections, list) and sections:
        for section_index, section in enumerate(sections, start=1):
            if not isinstance(section, dict):
                continue

            section_text = _section_text(section)
            if not section_text:
                continue

            section_meta = {
                **base_meta,
                "section_index": section_index,
                "section": _section_title(section),
                "source_reference": section.get("source_reference")
                or section.get("source_url")
                or base_meta.get("source_reference")
                or base_meta.get("source_url"),
            }

            for part_index, chunk_text in enumerate(
                _split_text(section_text, max_chars=max_chars, overlap_chars=overlap_chars),
                start=1,
            ):
                chunk_id = f"{doc_key}::section::{section_index:03d}::chunk::{part_index:03d}"
                metadata = {
                    **section_meta,
                    "chunk_id": chunk_id,
                    "chunk_type": "section",
                    "prev_chunk_id": previous_chunk_id,
                    "next_chunk_id": None,
                }

                docs.append(Document(page_content=chunk_text, metadata=metadata))

                if previous_chunk_id is not None and len(docs) >= 2:
                    docs[-2].metadata["next_chunk_id"] = chunk_id

                previous_chunk_id = chunk_id

    if not docs:
        fallback_text = ""
        for key in ("full_text", "content", "text", "body"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                fallback_text = value
                break

        for part_index, chunk_text in enumerate(
            _split_text(fallback_text, max_chars=max_chars, overlap_chars=overlap_chars),
            start=1,
        ):
            chunk_id = f"{doc_key}::full_text::chunk::{part_index:03d}"
            metadata = {
                **base_meta,
                "chunk_id": chunk_id,
                "chunk_type": "full_text",
                "section": item.get("page_heading") or item.get("title"),
                "prev_chunk_id": previous_chunk_id,
                "next_chunk_id": None,
            }

            docs.append(Document(page_content=chunk_text, metadata=metadata))

            if previous_chunk_id is not None and len(docs) >= 2:
                docs[-2].metadata["next_chunk_id"] = chunk_id

            previous_chunk_id = chunk_id

    return docs


def load_documents_from_base_info(
    input_dir: Path,
    *,
    max_chars: int,
    overlap_chars: int,
) -> list[Document]:
    documents: list[Document] = []
    seen_chunk_ids: set[str] = set()

    for file_path in _iter_json_files(input_dir):
        items = _load_json_file(file_path)
        for item in items:
            for doc in _documents_from_item(
                item,
                file_path,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            ):
                chunk_id = str(doc.metadata.get("chunk_id") or "")
                if chunk_id and chunk_id in seen_chunk_ids:
                    continue
                if chunk_id:
                    seen_chunk_ids.add(chunk_id)

                if doc.page_content and len(doc.page_content.strip()) >= 20:
                    doc.metadata = {
                        key: _safe_metadata_value(value)
                        for key, value in doc.metadata.items()
                        if value is not None
                    }
                    documents.append(doc)

    return documents


def build_faiss_index(
    documents: list[Document],
    index_dir: Path,
    *,
    batch_size: int = 128,
    force: bool = False,
) -> FAISS:
    if not documents:
        raise RuntimeError("No documents found to index.")

    if index_dir.exists() and force:
        print(f"[info] removing existing index: {index_dir}")
        shutil.rmtree(index_dir)

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


def build_base_info_index(
    *,
    input_dir: Path = BASE_INFO_INPUT_DIR,
    index_dir: Path = BASE_INFO_INDEX_DIR,
    force: bool = False,
    max_chars: int = 1400,
    overlap_chars: int = 180,
    batch_size: int = 128,
) -> FAISS:
    if index_dir.exists() and not force:
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

    documents = load_documents_from_base_info(
        input_dir,
        max_chars=max_chars,
        overlap_chars=overlap_chars,
    )

    print(f"[info] loaded {len(documents)} base-info chunks from {input_dir}")

    source_counts: dict[str, int] = {}
    for doc in documents:
        source = str(doc.metadata.get("source_type") or doc.metadata.get("source_file") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1

    print("[info] sources:")
    for source, count in sorted(source_counts.items()):
        print(f"  - {source}: {count}")

    db = build_faiss_index(
        documents,
        index_dir,
        batch_size=batch_size,
        force=force,
    )

    print(f"[done] base info -> {index_dir} ({len(db.index_to_docstore_id)} vectors)")
    return db


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build FAISS vectorstore from Scrapy base-info JSON outputs."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=BASE_INFO_INPUT_DIR,
        help="Directory containing Scrapy JSON outputs.",
    )
    parser.add_argument(
        "--index-dir",
        type=Path,
        default=BASE_INFO_INDEX_DIR,
        help="Output FAISS vectorstore directory.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Rebuild even if the FAISS index already exists.",
    )
    parser.add_argument("--max-chars", type=int, default=1400)
    parser.add_argument("--overlap-chars", type=int, default=180)
    parser.add_argument("--batch-size", type=int, default=128)
    args = parser.parse_args()

    build_base_info_index(
        input_dir=args.input_dir,
        index_dir=args.index_dir,
        force=args.force,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
