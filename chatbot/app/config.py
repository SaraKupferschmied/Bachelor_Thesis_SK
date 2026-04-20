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

DOCLING_STUDYPLANS_PARSED = (
    PROJECT_ROOT / "scrapy_crawler" / "outputs" / "parsed_fulltext_docling"
)
DOCLING_REGULATIONS_PARSED = (
    PROJECT_ROOT / "scrapy_crawler" / "outputs" / "reglementation_docs" / "parsed_fulltext_docling"
)


class Settings(BaseModel):
    vectorstore_dir: Path = Path(os.getenv("VECTORSTORE_DIR", str(ROOT_DIR / "vectorstore")))
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "mistral")
    ollama_embedding_model: str = os.getenv("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    rag_parser: str = os.getenv("RAG_PARSER", "default")
    k: int = int(os.getenv("RETRIEVAL_K", "8"))
    backend_api_base: str = os.getenv("BACKEND_API_BASE", "http://localhost:3000")

    @property
    def studyplans_parsed(self) -> Path:
        if self.rag_parser == "docling":
            return DOCLING_STUDYPLANS_PARSED
        return DEFAULT_STUDYPLANS_PARSED

    @property
    def reglementations_parsed(self) -> Path:
        if self.rag_parser == "docling":
            return DOCLING_REGULATIONS_PARSED
        return DEFAULT_REGULATIONS_PARSED

    @property
    def studyplans_index(self) -> Path:
        if self.rag_parser == "docling":
            return self.vectorstore_dir / "faiss_studyplans_docling"
        return self.vectorstore_dir / "faiss_studyplans_default"

    @property
    def reglementations_index(self) -> Path:
        if self.rag_parser == "docling":
            return self.vectorstore_dir / "faiss_reglementations_docling"
        return self.vectorstore_dir / "faiss_reglementations_default"


settings = Settings()