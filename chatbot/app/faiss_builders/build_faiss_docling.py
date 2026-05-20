from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings

from app.config import settings


METADATA_START = "---METADATA_JSON---"
METADATA_END = "---/METADATA_JSON---"

PAGE_PATTERN = re.compile(r"---PAGE\s+(\d+)---\s*\n", re.IGNORECASE)


@dataclass
class PageSpan:
    page: int
    text: str


def _parsed_dir_for(target: str, parser: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "scrapy_crawler" / "outputs"

    if parser != "docling":
        raise ValueError(f"This builder only supports parser='docling', got: {parser}")

    if target == "studyplans":
        return root / "parsed_fulltext_docling"
    if target in {"regulations", "reglementations"}:
        return root / "reglementation_docs" / "parsed_fulltext_docling"

    raise ValueError(f"Unknown target: {target}")


def _index_dir_for(target: str, parser: str) -> Path:
    if target == "studyplans":
        return settings.studyplans_index
    if target in {"regulations", "reglementations"}:
        return settings.reglementations_index
    raise ValueError(f"Unknown target: {target}")


def _iter_txt_files(parsed_dir: Path) -> Iterable[Path]:
    if not parsed_dir.exists():
        raise FileNotFoundError(f"Parsed directory not found: {parsed_dir}")

    for path in sorted(parsed_dir.glob("*.txt")):
        if path.name.startswith("_"):
            continue
        yield path


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_metadata_and_body(raw: str) -> tuple[dict, str]:
    start_idx = raw.find(METADATA_START)
    end_idx = raw.find(METADATA_END)

    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return {}, raw

    json_start = start_idx + len(METADATA_START)
    json_blob = raw[json_start:end_idx].strip()
    body = raw[end_idx + len(METADATA_END):].strip()

    try:
        meta = json.loads(json_blob)
    except json.JSONDecodeError:
        meta = {}

    return meta, body


def _split_pages(body: str) -> list[PageSpan]:
    matches = list(PAGE_PATTERN.finditer(body))

    if not matches:
        text = _normalize_text(body)
        return [PageSpan(page=1, text=text)] if text else []

    pages: list[PageSpan] = []
    for i, match in enumerate(matches):
        page_num = int(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        page_text = _normalize_text(body[start:end])
        if page_text:
            pages.append(PageSpan(page=page_num, text=page_text))

    return pages


def _looks_like_heading(block: str) -> bool:
    s = block.strip()
    if not s:
        return False

    if len(s) > 120:
        return False

    if "\n" in s:
        return False

    # numbered headings, e.g. "1", "1.2", "§ 4", "Module 3"
    if re.match(r"^((§\s*)?\d+(\.\d+){0,4}|[A-Z]\d+)\s+.+$", s):
        return True

    # colon headings, e.g. "Admission:"
    if s.endswith(":") and len(s) < 100:
        return True

    # title-ish lines
    words = s.split()
    if 1 <= len(words) <= 12:
        upper_ratio = sum(1 for c in s if c.isupper()) / max(1, sum(1 for c in s if c.isalpha()))
        if upper_ratio > 0.6:
            return True

    return False


def _page_blocks(page: PageSpan) -> list[tuple[int, str]]:
    """
    Split a page into coarse semantic blocks using blank lines.
    Returns tuples of (page_number, block_text).
    """
    blocks = [b.strip() for b in re.split(r"\n\s*\n", page.text) if b.strip()]
    return [(page.page, b) for b in blocks]


def _collect_sections(pages: list[PageSpan]) -> list[dict]:
    """
    Build document sections across pages.
    A section starts when a heading-like block appears.
    """
    sections: list[dict] = []
    current = {
        "heading": None,
        "blocks": [],
        "pages": set(),
    }

    def flush_current() -> None:
        if not current["blocks"]:
            return
        text = "\n\n".join(current["blocks"]).strip()
        if text:
            sections.append(
                {
                    "heading": current["heading"],
                    "text": text,
                    "pages": sorted(current["pages"]),
                }
            )

    for page in pages:
        for page_num, block in _page_blocks(page):
            if _looks_like_heading(block):
                flush_current()
                current = {
                    "heading": block.strip(),
                    "blocks": [],
                    "pages": {page_num},
                }
                continue

            current["blocks"].append(block.strip())
            current["pages"].add(page_num)

    flush_current()

    # fallback: if no sections were found, build one global section
    if not sections:
        joined = "\n\n".join(p.text for p in pages).strip()
        if joined:
            sections.append(
                {
                    "heading": None,
                    "text": joined,
                    "pages": [p.page for p in pages],
                }
            )

    return sections


def _split_into_paragraphs(text: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if paras:
        return paras

    # fallback if text has no blank-line paragraph structure
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []

    # soft split on sentence boundaries
    parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ])", text)
    return [p.strip() for p in parts if p.strip()]


def _estimate_pages_for_chunk(chunk_text: str, section_pages: list[int]) -> list[int]:
    # page mapping is approximate because chunking happens after pages are merged into sections.
    # better than losing provenance entirely.
    return section_pages[:]


def _chunk_section_text(
    text: str,
    heading: str | None,
    pages: list[int],
    *,
    target_chars: int = 1400,
    max_chars: int = 1800,
    overlap_chars: int = 220,
) -> list[dict]:
    paragraphs = _split_into_paragraphs(text)
    if not paragraphs:
        return []

    chunks: list[dict] = []
    current_parts: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current_parts, current_len
        if not current_parts:
            return

        body = "\n\n".join(current_parts).strip()
        if heading and not body.lower().startswith(heading.lower()):
            full_text = f"{heading}\n\n{body}"
        else:
            full_text = body

        chunks.append(
            {
                "text": full_text,
                "heading": heading,
                "pages": _estimate_pages_for_chunk(full_text, pages),
            }
        )
        current_parts = []
        current_len = 0

    for para in paragraphs:
        para_len = len(para)

        # single oversized paragraph -> split with overlap
        if para_len > max_chars:
            flush()
            start = 0
            while start < para_len:
                end = min(start + max_chars, para_len)
                piece = para[start:end].strip()

                if heading:
                    piece_text = f"{heading}\n\n{piece}"
                else:
                    piece_text = piece

                chunks.append(
                    {
                        "text": piece_text,
                        "heading": heading,
                        "pages": _estimate_pages_for_chunk(piece_text, pages),
                    }
                )

                if end >= para_len:
                    break
                start = max(0, end - overlap_chars)
            continue

        projected = current_len + (2 if current_parts else 0) + para_len

        if current_parts and projected > target_chars:
            flush()

        current_parts.append(para)
        current_len += (2 if current_len else 0) + para_len

    flush()

    # add lightweight overlap from previous chunk tail
    if overlap_chars > 0 and len(chunks) > 1:
        overlapped: list[dict] = []
        prev_body = ""

        for i, ch in enumerate(chunks):
            text = ch["text"]
            if i == 0:
                overlapped.append(ch)
            else:
                tail = prev_body[-overlap_chars:].strip()
                merged = f"{tail}\n\n{text}" if tail else text
                new_chunk = dict(ch)
                new_chunk["text"] = merged
                overlapped.append(new_chunk)

            prev_body = text

        chunks = overlapped

    return chunks


def _base_metadata(header_meta: dict, file_path: Path) -> dict:
    return {
        "doc_key": header_meta.get("doc_key"),
        "program_key": header_meta.get("program_key"),
        "source_url": header_meta.get("source_url"),
        "local_path": header_meta.get("local_path"),
        "sha256": header_meta.get("sha256"),
        "title": header_meta.get("title"),
        "faculty": header_meta.get("faculty"),
        "degree_level": header_meta.get("degree_level"),
        "total_ects": header_meta.get("total_ects"),
        "program_name": header_meta.get("program_name"),
        "doc_label": header_meta.get("doc_label"),
        "source_type": header_meta.get("source_type"),
        "source_file": file_path.name,
        "parser": header_meta.get("parser", "docling"),
        "index_variant": "docling_semantic",
    }


def _load_documents_from_fulltext(parsed_dir: Path) -> list[Document]:
    docs: list[Document] = []
    seen_ids: set[str] = set()

    for file_path in _iter_txt_files(parsed_dir):
        raw = file_path.read_text(encoding="utf-8", errors="ignore")
        header_meta, body = _extract_metadata_and_body(raw)

        pages = _split_pages(body)
        if not pages:
            continue

        sections = _collect_sections(pages)
        base_meta = _base_metadata(header_meta, file_path)
        doc_key = header_meta.get("doc_key", file_path.stem)

        previous_chunk_id: str | None = None
        section_counter = 0
        chunk_counter = 0

        for section in sections:
            section_counter += 1
            section_id = f"{doc_key}::section::{section_counter}"
            section_heading = section.get("heading")
            section_text = section.get("text", "")
            section_pages = section.get("pages", [])

            section_chunks = _chunk_section_text(
                section_text,
                section_heading,
                section_pages,
                target_chars=1400,
                max_chars=1800,
                overlap_chars=220,
            )

            for local_idx, chunk in enumerate(section_chunks, start=1):
                chunk_counter += 1
                chunk_id = f"{section_id}::chunk::{local_idx}"

                if chunk_id in seen_ids:
                    continue
                seen_ids.add(chunk_id)

                metadata = {
                    **base_meta,
                    "chunk_id": chunk_id,
                    "chunk_type": "semantic_section",
                    "section_id": section_id,
                    "section": section_heading,
                    "subsection": None,
                    "page_start": min(chunk["pages"]) if chunk["pages"] else None,
                    "page_end": max(chunk["pages"]) if chunk["pages"] else None,
                    "pages": chunk["pages"],
                    "prev_chunk_id": previous_chunk_id,
                    "next_chunk_id": None,
                }

                doc = Document(page_content=chunk["text"], metadata=metadata)
                docs.append(doc)

                if previous_chunk_id is not None:
                    docs[-2].metadata["next_chunk_id"] = chunk_id

                previous_chunk_id = chunk_id

    return docs


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


def build_index_for(target: str, parser: str = "docling", force_rebuild: bool = False) -> FAISS:
    parsed_dir = _parsed_dir_for(target, parser)
    index_dir = _index_dir_for(target, parser)

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

    documents = _load_documents_from_fulltext(parsed_dir)
    print(f"[info] loaded {len(documents)} semantic chunks from {parsed_dir}")

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
    parser = argparse.ArgumentParser(description="Build semantic FAISS indexes from Docling fulltext TXT files.")
    parser.add_argument(
        "--target",
        choices=["all", "studyplans", "regulations", "reglementations"],
        default="all",
        help="Which index to build.",
    )
    parser.add_argument(
        "--parser",
        choices=["docling"],
        default="docling",
        help="Parser type.",
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
        print(f"\n=== Building {target} semantic index ({args.parser}) ===")
        db = build_index_for(target, parser=args.parser, force_rebuild=args.force)
        print(f"[done] {target} -> {_index_dir_for(target, args.parser)} ({len(db.index_to_docstore_id)} vectors)")


if __name__ == "__main__":
    main()