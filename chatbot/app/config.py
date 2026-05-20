from pydantic import BaseModel
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT_DIR.parent

DEFAULT_STUDYPLANS_PARSED = (
    PROJECT_ROOT / "scrapy_crawler" / "outputs" / "parsed_chunks"
)
DEFAULT_REGULATIONS_PARSED = (
    PROJECT_ROOT / "scrapy_crawler" / "outputs" / "parsed_chunks_regulations"
)

DOCLING_STUDYPLANS_PARSED = Path(
    os.getenv(
        "DOCLING_STUDYPLANS_PARSED",
        str(PROJECT_ROOT / "scrapy_crawler" / "outputs" / "parsed_fulltext_docling"),
    )
)
DOCLING_REGULATIONS_PARSED = Path(
    os.getenv(
        "DOCLING_REGULATIONS_PARSED",
        str(PROJECT_ROOT / "scrapy_crawler" / "outputs" / "reglementation_docs" / "parsed_fulltext_docling"),
    )
)

DOCLING_LANGUAGE_AWARE_STUDYPLANS_PARSED = Path(
    os.getenv(
        "DOCLING_LANGUAGE_AWARE_STUDYPLANS_PARSED",
        str(PROJECT_ROOT / "scrapy_crawler" / "outputs" / "parsed_fulltext_docling_new_clean_language_suffixes"),
    )
)
DOCLING_LANGUAGE_AWARE_REGULATIONS_PARSED = Path(
    os.getenv(
        "DOCLING_LANGUAGE_AWARE_REGULATIONS_PARSED",
        str(PROJECT_ROOT / "scrapy_crawler" / "outputs" / "reglementation_docs" / "parsed_fulltext_docling"),
    )
)


class Settings(BaseModel):
    vectorstore_dir: Path = Path(os.getenv("VECTORSTORE_DIR", str(ROOT_DIR / "vectorstore")))
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "mistral")
    ollama_embedding_model: str = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    rag_parser: str = os.getenv("RAG_PARSER", "default")
    vectorstore_variant: str = os.getenv("VECTORSTORE_VARIANT", "")
    k: int = int(os.getenv("RETRIEVAL_K", "8"))
    backend_api_base: str = os.getenv("BACKEND_API_BASE", "http://localhost:3000")

    @property
    def studyplans_parsed(self) -> Path:
        if self.rag_parser == "docling_language_aware":
            return DOCLING_LANGUAGE_AWARE_STUDYPLANS_PARSED
        if self.rag_parser == "docling":
            return DOCLING_STUDYPLANS_PARSED
        return DEFAULT_STUDYPLANS_PARSED

    @property
    def reglementations_parsed(self) -> Path:
        if self.rag_parser == "docling_language_aware":
            return DOCLING_LANGUAGE_AWARE_REGULATIONS_PARSED
        if self.rag_parser == "docling":
            return DOCLING_REGULATIONS_PARSED
        return DEFAULT_REGULATIONS_PARSED

    @property
    def _variant_suffix(self) -> str:
        safe = "".join(c if c.isalnum() or c in {"_", "-"} else "_" for c in self.vectorstore_variant.strip())
        return f"_{safe}" if safe else ""

    @property
    def studyplans_index(self) -> Path:
        suffix = self._variant_suffix
        if self.rag_parser == "docling_language_aware":
            return self.vectorstore_dir / "faiss_studyplans_docling_language_aware"
        if self.rag_parser == "docling_table_semantic":
            return self.vectorstore_dir / "faiss_studyplans_docling_table_semantic_new"
        if self.rag_parser == "docling":
            return self.vectorstore_dir / f"faiss_studyplans_docling{suffix}"
        return self.vectorstore_dir / f"faiss_studyplans_default{suffix}"

    @property
    def reglementations_index(self) -> Path:
        suffix = self._variant_suffix
        if self.rag_parser == "docling_language_aware":
            return self.vectorstore_dir / "faiss_reglementations_docling_language_aware"
        if self.rag_parser == "docling_table_semantic":
            return self.vectorstore_dir / "faiss_reglementations"
        if self.rag_parser == "docling":
            return self.vectorstore_dir / f"faiss_reglementations_docling{suffix}"
        return self.vectorstore_dir / f"faiss_reglementations_default{suffix}"


    @property
    def base_data_index(self) -> Path:
        return self.vectorstore_dir / "faiss_BaseData"


settings = Settings()
