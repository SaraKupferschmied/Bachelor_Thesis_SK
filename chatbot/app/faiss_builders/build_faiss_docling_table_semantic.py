from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Any

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings

from ..config import settings


METADATA_START = "---METADATA_JSON---"
METADATA_END = "---/METADATA_JSON---"
PAGE_PATTERN = re.compile(r"---PAGE\s+(\d+)---\s*\n", re.IGNORECASE)

# Only these source metadata fields are copied from the parsed TXT header.
# Per-chunk technical metadata is added separately below.
SOURCE_METADATA_WHITELIST = {
    "parsed_at",
    "pages",
    "parser",
    "doc_key",
    "program_key",
    "faculty",
    "degree_level",
    "total_ects",
    "program_name",
    "doc_label",
    "title",
    "source_url",
    "source_type",
    "curriculum_url",
    "fetched_at",
}

# Accept common misspellings / older names in parsed metadata and normalize them.
SOURCE_METADATA_ALIASES = {
    "Parsed_At": "parsed_at",
    "parsed_At": "parsed_at",
    "doc_lable": "doc_label",
    "doc_lable": "doc_label",
    "programme_url": None,   # intentionally dropped
    "Programme_Key": "program_key",
    "programKey": "program_key",
    "Faculty": "faculty",
    "local_path": None,      # intentionally dropped
    "sha256": None,          # intentionally dropped
    "notes": None,           # intentionally dropped
}


@dataclass(frozen=True)
class PageSpan:
    page: int
    text: str


@dataclass(frozen=True)
class Block:
    page: int
    kind: str  # heading | table | text | list | image
    text: str
    heading_path: tuple[str, ...]


def _parsed_dir_for(target: str, parser: str) -> Path:
    root = Path(__file__).resolve().parents[3] / "scrapy_crawler" / "outputs"
    if parser not in {"docling", "docling_table_semantic"}:
        raise ValueError(f"This builder only supports docling table semantic, got: {parser}")
    if target == "studyplans":
        return root / "parsed_fulltext_docling_new2"
    if target in {"regulations", "reglementations"}:
        return root / "reglementation_docs" / "parsed_fulltext_docling_new"
    raise ValueError(f"Unknown target: {target}")


def _index_dir_for(target: str, parser: str, suffix: str) -> Path:
    if target == "studyplans":
        return settings.vectorstore_dir / "faiss_studyplans_docling_table_semantic_new"
    if target in {"regulations", "reglementations"}:
        return settings.vectorstore_dir / "faiss_reglementations"
    raise ValueError(f"Unknown target: {target}")


def _iter_txt_files(parsed_dir: Path) -> Iterable[Path]:
    if not parsed_dir.exists():
        raise FileNotFoundError(f"Parsed directory not found: {parsed_dir}")
    for path in sorted(parsed_dir.glob("*.txt")):
        if not path.name.startswith("_"):
            yield path


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("&amp;", "&")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_metadata_and_body(raw: str) -> tuple[dict[str, Any], str]:
    start_idx = raw.find(METADATA_START)
    end_idx = raw.find(METADATA_END)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return {}, raw
    json_blob = raw[start_idx + len(METADATA_START):end_idx].strip()
    body = raw[end_idx + len(METADATA_END):].strip()
    try:
        return json.loads(json_blob), body
    except json.JSONDecodeError:
        return {}, body


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
        text = _normalize_text(body[start:end])
        if text:
            pages.append(PageSpan(page=page_num, text=text))
    return pages


def _source_metadata(header_meta: dict[str, Any], file_path: Path) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for raw_key, value in header_meta.items():
        key = SOURCE_METADATA_ALIASES.get(raw_key, raw_key)
        if key in SOURCE_METADATA_WHITELIST:
            cleaned[key] = value
    for key in SOURCE_METADATA_WHITELIST:
        cleaned.setdefault(key, None)
    cleaned["source_file"] = file_path.name
    return cleaned


def _is_markdown_table(block: str) -> bool:
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    pipe_lines = [ln for ln in lines if "|" in ln]
    if len(pipe_lines) < 2:
        return False
    return any(re.match(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$", ln) for ln in lines)


def _markdown_table_to_records(table_md: str) -> str:
    """Convert a Markdown table to retrieval-friendly row sentences while preserving the raw table."""
    lines = [ln.strip() for ln in table_md.splitlines() if ln.strip() and "|" in ln]
    if len(lines) < 2:
        return table_md

    def cells(line: str) -> list[str]:
        return [re.sub(r"\s+", " ", c).strip() for c in line.strip("|").split("|")]

    header = cells(lines[0])
    rows = []
    for line in lines[1:]:
        if re.match(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$", line):
            continue
        row = cells(line)
        pairs = []
        for idx, value in enumerate(row):
            if not value:
                continue
            col = header[idx] if idx < len(header) and header[idx] else f"column_{idx + 1}"
            pairs.append(f"{col}: {value}")
        if pairs:
            rows.append("; ".join(pairs))

    if not rows:
        return table_md
    return "Table rows:\n" + "\n".join(f"- {row}" for row in rows) + "\n\nRaw table:\n" + table_md


def _heading_level(line: str) -> int | None:
    m = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
    if m:
        return len(m.group(1))
    s = line.strip()
    if re.match(r"^((§\s*)?\d+(\.\d+){0,4}|[A-Z]\d+|Modul\s+\d+|Module\s+\d+)\s*[:.\-]?\s+.+$", s, re.I):
        return 2 + s.count(".")
    return None


def _is_heading_block(block: str) -> bool:
    s = block.strip()
    if not s or "\n" in s or len(s) > 180:
        return False
    return _heading_level(s) is not None or (s.endswith(":") and len(s) < 100)


def _page_to_blocks(page: PageSpan) -> list[Block]:
    blocks: list[Block] = []
    heading_stack: list[tuple[int, str]] = []
    raw_blocks = [b.strip() for b in re.split(r"\n\s*\n", page.text) if b.strip()]

    for raw in raw_blocks:
        if raw == "<!-- image -->":
            continue
        kind = "table" if _is_markdown_table(raw) else "list" if raw.lstrip().startswith(("- ", "* ", "1. ")) else "text"
        if _is_heading_block(raw):
            level = _heading_level(raw) or 3
            title = re.sub(r"^#{1,6}\s+", "", raw).strip()
            heading_stack = [(lvl, h) for lvl, h in heading_stack if lvl < level]
            heading_stack.append((level, title))
            blocks.append(Block(page=page.page, kind="heading", text=title, heading_path=tuple(h for _, h in heading_stack)))
            continue
        blocks.append(Block(page=page.page, kind=kind, text=raw, heading_path=tuple(h for _, h in heading_stack)))
    return blocks


def _all_blocks(pages: list[PageSpan]) -> list[Block]:
    out: list[Block] = []
    for page in pages:
        out.extend(_page_to_blocks(page))
    return out


def _block_text_for_embedding(block: Block) -> str:
    prefix = " > ".join(block.heading_path)
    body = _markdown_table_to_records(block.text) if block.kind == "table" else block.text
    if prefix and block.kind != "heading":
        return f"Section: {prefix}\n\n{body}"
    return body


def _pack_semantic_chunks(
    blocks: list[Block],
    *,
    target_chars: int,
    max_chars: int,
    overlap_chars: int,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current: list[Block] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        texts = [_block_text_for_embedding(b) for b in current if b.kind != "heading"]
        if not texts:
            current = []
            current_len = 0
            return
        chunks.append({
            "text": "\n\n".join(texts).strip(),
            "pages": sorted({b.page for b in current}),
            "heading_path": current[-1].heading_path,
            "contains_table": any(b.kind == "table" for b in current),
            "block_kinds": sorted({b.kind for b in current}),
        })
        current = []
        current_len = 0

    for block in blocks:
        if block.kind == "heading":
            # Keep heading as context for following blocks, not as a standalone vector.
            continue
        text = _block_text_for_embedding(block)
        block_len = len(text)

        # Tables are high-value structured retrieval units: never merge them into unrelated prose.
        if block.kind == "table":
            flush()
            if block_len <= max_chars:
                chunks.append({
                    "text": text,
                    "pages": [block.page],
                    "heading_path": block.heading_path,
                    "contains_table": True,
                    "block_kinds": ["table"],
                })
            else:
                for i in range(0, block_len, max_chars - overlap_chars):
                    piece = text[i:i + max_chars].strip()
                    chunks.append({
                        "text": piece,
                        "pages": [block.page],
                        "heading_path": block.heading_path,
                        "contains_table": True,
                        "block_kinds": ["table"],
                    })
            continue

        if block_len > max_chars:
            flush()
            step = max_chars - overlap_chars
            for i in range(0, block_len, step):
                piece = text[i:i + max_chars].strip()
                if piece:
                    chunks.append({
                        "text": piece,
                        "pages": [block.page],
                        "heading_path": block.heading_path,
                        "contains_table": False,
                        "block_kinds": [block.kind],
                    })
            continue

        projected = current_len + (2 if current else 0) + block_len
        same_section = not current or current[-1].heading_path == block.heading_path
        if current and (projected > target_chars or not same_section):
            flush()
        current.append(block)
        current_len += (2 if current_len else 0) + block_len

    flush()

    if overlap_chars > 0 and len(chunks) > 1:
        for i in range(1, len(chunks)):
            if chunks[i].get("contains_table"):
                continue
            prev_tail = chunks[i - 1]["text"][-overlap_chars:].strip()
            if prev_tail:
                chunks[i]["text"] = f"Previous context: {prev_tail}\n\n{chunks[i]['text']}"
    return chunks


def load_documents_from_fulltext(parsed_dir: Path, *, target_chars: int, max_chars: int, overlap_chars: int) -> list[Document]:
    docs: list[Document] = []
    seen_ids: set[str] = set()

    for file_path in _iter_txt_files(parsed_dir):
        raw = file_path.read_text(encoding="utf-8", errors="ignore")
        header_meta, body = _extract_metadata_and_body(raw)
        pages = _split_pages(body)
        if not pages:
            continue
        blocks = _all_blocks(pages)
        chunks = _pack_semantic_chunks(blocks, target_chars=target_chars, max_chars=max_chars, overlap_chars=overlap_chars)
        base_meta = _source_metadata(header_meta, file_path)
        doc_key = base_meta.get("doc_key") or file_path.stem
        previous_chunk_id: str | None = None

        for idx, chunk in enumerate(chunks, start=1):
            chunk_id = f"{doc_key}::semantic_table::chunk::{idx:04d}"
            if chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)
            metadata = {
                **base_meta,
                "index_variant": "table_semantic_v1",
                "chunk_id": chunk_id,
                "chunk_type": "table" if chunk["contains_table"] else "semantic",
                "section": " > ".join(chunk.get("heading_path") or []),
                "page_start": min(chunk["pages"]) if chunk["pages"] else None,
                "page_end": max(chunk["pages"]) if chunk["pages"] else None,
                "chunk_pages": chunk["pages"],
                "contains_table": chunk["contains_table"],
                "block_kinds": chunk["block_kinds"],
                "prev_chunk_id": previous_chunk_id,
                "next_chunk_id": None,
            }
            docs.append(Document(page_content=chunk["text"], metadata=metadata))
            if previous_chunk_id is not None:
                docs[-2].metadata["next_chunk_id"] = chunk_id
            previous_chunk_id = chunk_id
    return docs


def build_faiss_index(documents: list[Document], index_dir: Path, batch_size: int = 128) -> FAISS:
    if not documents:
        raise RuntimeError("No documents found to index.")
    index_dir.mkdir(parents=True, exist_ok=True)
    embeddings = OllamaEmbeddings(model=settings.ollama_embedding_model, base_url=settings.ollama_host)
    db: FAISS | None = None
    for start in range(0, len(documents), batch_size):
        end = min(start + batch_size, len(documents))
        print(f"[embed] batch {start}-{end} / {len(documents)}")
        if db is None:
            db = FAISS.from_documents(documents[start:end], embeddings)
        else:
            db.add_documents(documents[start:end])
    assert db is not None
    db.save_local(str(index_dir))
    return db


def build_index_for(target: str, parser: str = "docling", force_rebuild: bool = False, *, target_chars: int = 1300, max_chars: int = 1800, overlap_chars: int = 180) -> FAISS:
    parsed_dir = _parsed_dir_for(target, parser)
    index_dir = _index_dir_for(target, parser, "table_semantic")
    embeddings = OllamaEmbeddings(model=settings.ollama_embedding_model, base_url=settings.ollama_host)
    if index_dir.exists() and not force_rebuild:
        print(f"[info] index already exists: {index_dir}")
        return FAISS.load_local(str(index_dir), embeddings, allow_dangerous_deserialization=True)
    documents = load_documents_from_fulltext(parsed_dir, target_chars=target_chars, max_chars=max_chars, overlap_chars=overlap_chars)
    print(f"[info] loaded {len(documents)} table-aware semantic chunks from {parsed_dir}")
    print(f"[info] tables: {sum(1 for d in documents if d.metadata.get('contains_table'))}")
    return build_faiss_index(documents, index_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a table-aware semantic FAISS index from Docling TXT files.")
    parser.add_argument("--target", choices=["all", "studyplans", "regulations", "reglementations"], default="all")
    parser.add_argument("--parser", choices=["docling"], default="docling")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--target-chars", type=int, default=1300)
    parser.add_argument("--max-chars", type=int, default=1800)
    parser.add_argument("--overlap-chars", type=int, default=180)
    args = parser.parse_args()

    targets: list[str] = []
    if args.target in {"all", "studyplans"}:
        targets.append("studyplans")
    if args.target in {"all", "regulations", "reglementations"}:
        targets.append("regulations")

    for target in targets:
        print(f"\n=== Building {target} table-aware semantic index ===")
        db = build_index_for(target, parser=args.parser, force_rebuild=args.force, target_chars=args.target_chars, max_chars=args.max_chars, overlap_chars=args.overlap_chars)
        print(f"[done] {target}: {len(db.index_to_docstore_id)} vectors")


if __name__ == "__main__":
    main()
