from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings

from ..config import settings

METADATA_START = "---METADATA_JSON---"
METADATA_END = "---/METADATA_JSON---"
PAGE_PATTERN = re.compile(r"---PAGE\s+(\d+)---\s*\n", re.IGNORECASE)

SOURCE_METADATA_WHITELIST = {
    "parsed_at", "pages", "parser", "doc_key", "degree_level", "total_ects",
    "program_name", "doc_label", "source_url", "source_type", "curriculum_url", "fetched_at",
}
SOURCE_METADATA_ALIASES = {"Parsed_At": "parsed_at", "doc_lable": "doc_label"}

@dataclass(frozen=True)
class PageSpan:
    page: int
    text: str

@dataclass(frozen=True)
class Section:
    section_id: str
    heading: str | None
    text: str
    pages: list[int]
    contains_table: bool


def _parsed_dir_for(target: str, parser: str) -> Path:
    root = Path(__file__).resolve().parents[2] / "scrapy_crawler" / "outputs"
    if parser != "docling":
        raise ValueError(f"This builder only supports parser='docling', got: {parser}")
    if target == "studyplans":
        return root / "parsed_fulltext_docling"
    if target in {"regulations", "reglementations"}:
        return root / "reglementation_docs" / "parsed_fulltext_docling"
    raise ValueError(f"Unknown target: {target}")


def _index_dir_for(target: str, parser: str, suffix: str) -> Path:
    if target == "studyplans":
        return settings.studyplans_index
    if target in {"regulations", "reglementations"}:
        return settings.reglementations_index
    raise ValueError(f"Unknown target: {target}")


def _iter_txt_files(parsed_dir: Path) -> Iterable[Path]:
    if not parsed_dir.exists():
        raise FileNotFoundError(f"Parsed directory not found: {parsed_dir}")
    for p in sorted(parsed_dir.glob("*.txt")):
        if not p.name.startswith("_"):
            yield p


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("&amp;", "&")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_metadata_and_body(raw: str) -> tuple[dict[str, Any], str]:
    start_idx = raw.find(METADATA_START)
    end_idx = raw.find(METADATA_END)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return {}, raw
    try:
        meta = json.loads(raw[start_idx + len(METADATA_START):end_idx].strip())
    except json.JSONDecodeError:
        meta = {}
    return meta, raw[end_idx + len(METADATA_END):].strip()


def _split_pages(body: str) -> list[PageSpan]:
    matches = list(PAGE_PATTERN.finditer(body))
    if not matches:
        text = _normalize_text(body)
        return [PageSpan(1, text)] if text else []
    pages: list[PageSpan] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        text = _normalize_text(body[start:end])
        if text:
            pages.append(PageSpan(int(match.group(1)), text))
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
    pipe_count = sum(1 for ln in lines if "|" in ln)
    sep = any(re.match(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$", ln) for ln in lines)
    return pipe_count >= 2 and sep


def _table_to_row_sentences(table_md: str) -> str:
    lines = [ln.strip() for ln in table_md.splitlines() if ln.strip() and "|" in ln]
    if len(lines) < 2:
        return table_md
    def cells(line: str) -> list[str]:
        return [re.sub(r"\s+", " ", c).strip() for c in line.strip("|").split("|")]
    header = cells(lines[0])
    rows: list[str] = []
    for line in lines[1:]:
        if re.match(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$", line):
            continue
        pairs = []
        for i, val in enumerate(cells(line)):
            if val:
                col = header[i] if i < len(header) and header[i] else f"column_{i + 1}"
                pairs.append(f"{col}: {val}")
        if pairs:
            rows.append("; ".join(pairs))
    if not rows:
        return table_md
    return "Table rows:\n" + "\n".join(f"- {r}" for r in rows) + "\n\nRaw table:\n" + table_md


def _heading_title(block: str) -> str | None:
    s = block.strip()
    m = re.match(r"^#{1,6}\s+(.+)$", s)
    if m:
        return m.group(1).strip()
    if "\n" not in s and len(s) <= 180 and re.match(r"^((§\s*)?\d+(\.\d+){0,4}|[A-Z]\d+|Modul\s+\d+|Module\s+\d+)\s*[:.\-]?\s+.+$", s, re.I):
        return s
    if "\n" not in s and s.endswith(":") and len(s) < 100:
        return s
    return None


def _blocks_for_page(page: PageSpan) -> list[tuple[int, str, str | None, bool]]:
    out: list[tuple[int, str, str | None, bool]] = []
    current_heading: str | None = None
    for block in [b.strip() for b in re.split(r"\n\s*\n", page.text) if b.strip()]:
        if block == "<!-- image -->":
            continue
        heading = _heading_title(block)
        if heading:
            current_heading = heading
            continue
        is_table = _is_markdown_table(block)
        text = _table_to_row_sentences(block) if is_table else block
        if current_heading:
            text = f"Section: {current_heading}\n\n{text}"
        out.append((page.page, text, current_heading, is_table))
    return out


def _sections_from_pages(pages: list[PageSpan], doc_key: str, *, parent_target_chars: int) -> list[Section]:
    sections: list[Section] = []
    parts: list[str] = []
    page_set: set[int] = set()
    heading: str | None = None
    has_table = False
    counter = 0
    def flush() -> None:
        nonlocal parts, page_set, heading, has_table, counter
        if not parts:
            return
        counter += 1
        sections.append(Section(f"{doc_key}::parent::{counter:04d}", heading, "\n\n".join(parts).strip(), sorted(page_set), has_table))
        parts = []
        page_set = set()
        has_table = False
    for page in pages:
        for page_num, text, h, is_table in _blocks_for_page(page):
            starts_new_heading = bool(h and h != heading)
            too_large = sum(len(p) for p in parts) + len(text) > parent_target_chars
            if parts and (starts_new_heading or too_large or is_table):
                flush()
            heading = h or heading
            parts.append(text)
            page_set.add(page_num)
            has_table = has_table or is_table
            if is_table:
                flush()
    flush()
    return sections


def _child_chunks(section: Section, *, child_target_chars: int, child_max_chars: int, overlap_chars: int) -> list[str]:
    if section.contains_table and len(section.text) <= child_max_chars * 2:
        return [section.text]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section.text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current).strip())
            current = []
            current_len = 0
    for para in paragraphs:
        if len(para) > child_max_chars:
            flush()
            step = max(1, child_max_chars - overlap_chars)
            for i in range(0, len(para), step):
                piece = para[i:i + child_max_chars].strip()
                if piece:
                    chunks.append(piece)
            continue
        projected = current_len + (2 if current else 0) + len(para)
        if current and projected > child_target_chars:
            flush()
        current.append(para)
        current_len += (2 if current_len else 0) + len(para)
    flush()
    if overlap_chars > 0 and len(chunks) > 1:
        for i in range(1, len(chunks)):
            chunks[i] = f"Previous context: {chunks[i-1][-overlap_chars:].strip()}\n\n{chunks[i]}"
    return chunks


def load_documents_from_fulltext(parsed_dir: Path, *, child_target_chars: int, child_max_chars: int, overlap_chars: int, parent_target_chars: int) -> tuple[list[Document], dict[str, dict[str, Any]]]:
    docs: list[Document] = []
    parent_store: dict[str, dict[str, Any]] = {}
    for file_path in _iter_txt_files(parsed_dir):
        raw = file_path.read_text(encoding="utf-8", errors="ignore")
        header_meta, body = _extract_metadata_and_body(raw)
        base_meta = _source_metadata(header_meta, file_path)
        doc_key = str(base_meta.get("doc_key") or file_path.stem)
        pages = _split_pages(body)
        if not pages:
            continue
        parents = _sections_from_pages(pages, doc_key, parent_target_chars=parent_target_chars)
        for parent in parents:
            parent_store[parent.section_id] = {"parent_id": parent.section_id, "text": parent.text, "section": parent.heading, "pages": parent.pages, "contains_table": parent.contains_table, "metadata": base_meta}
            children = _child_chunks(parent, child_target_chars=child_target_chars, child_max_chars=child_max_chars, overlap_chars=overlap_chars)
            for i, child in enumerate(children, start=1):
                child_id = f"{parent.section_id}::child::{i:03d}"
                searchable = f"Program: {base_meta.get('program_name')}\nDegree: {base_meta.get('degree_level')}\nDocument: {base_meta.get('doc_label')}\n\n{child}"
                docs.append(Document(page_content=searchable, metadata={**base_meta, "index_variant": "parent_child_v1", "chunk_id": child_id, "chunk_type": "child_table" if parent.contains_table else "child_text", "parent_id": parent.section_id, "section": parent.heading, "page_start": min(parent.pages) if parent.pages else None, "page_end": max(parent.pages) if parent.pages else None, "chunk_pages": parent.pages, "contains_table": parent.contains_table}))
    return docs, parent_store


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


def build_index_for(target: str, parser: str = "docling", force_rebuild: bool = False, *, child_target_chars: int = 700, child_max_chars: int = 1000, overlap_chars: int = 120, parent_target_chars: int = 4200) -> FAISS:
    parsed_dir = _parsed_dir_for(target, parser)
    index_dir = _index_dir_for(target, parser, "parent_child")
    embeddings = OllamaEmbeddings(model=settings.ollama_embedding_model, base_url=settings.ollama_host)
    if index_dir.exists() and not force_rebuild:
        print(f"[info] index already exists: {index_dir}")
        return FAISS.load_local(str(index_dir), embeddings, allow_dangerous_deserialization=True)
    documents, parent_store = load_documents_from_fulltext(parsed_dir, child_target_chars=child_target_chars, child_max_chars=child_max_chars, overlap_chars=overlap_chars, parent_target_chars=parent_target_chars)
    print(f"[info] loaded {len(documents)} child vectors and {len(parent_store)} parents from {parsed_dir}")
    db = build_faiss_index(documents, index_dir)
    (index_dir / "parent_store.json").write_text(json.dumps(parent_store, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[info] wrote parent store: {index_dir / 'parent_store.json'}")
    return db


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a parent-child FAISS index from Docling TXT files.")
    parser.add_argument("--target", choices=["all", "studyplans", "regulations", "reglementations"], default="all")
    parser.add_argument("--parser", choices=["docling"], default="docling")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--child-target-chars", type=int, default=700)
    parser.add_argument("--child-max-chars", type=int, default=1000)
    parser.add_argument("--overlap-chars", type=int, default=120)
    parser.add_argument("--parent-target-chars", type=int, default=4200)
    args = parser.parse_args()
    targets: list[str] = []
    if args.target in {"all", "studyplans"}:
        targets.append("studyplans")
    if args.target in {"all", "regulations", "reglementations"}:
        targets.append("regulations")
    for target in targets:
        print(f"\n=== Building {target} parent-child index ===")
        db = build_index_for(target, parser=args.parser, force_rebuild=args.force, child_target_chars=args.child_target_chars, child_max_chars=args.child_max_chars, overlap_chars=args.overlap_chars, parent_target_chars=args.parent_target_chars)
        print(f"[done] {target}: {len(db.index_to_docstore_id)} child vectors")

if __name__ == "__main__":
    main()
