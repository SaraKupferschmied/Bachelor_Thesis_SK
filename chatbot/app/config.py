from pydantic import BaseModel
import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = ROOT_DIR.parent
DEFAULT_STUDYPLANS_PARSED = PROJECT_ROOT / 'scrapy_crawler' / 'scrapy_crawler' / 'outputs' / 'parsed_fulltext'
DEFAULT_REGULATIONS_PARSED = PROJECT_ROOT / 'scrapy_crawler' / 'scrapy_crawler' / 'outputs' / 'reglementation_docs' / 'parsed_fulltext_shorttitles'


class Settings(BaseModel):
    pdf_dir: Path = Path(os.getenv('PDF_DIR', './data/pdfs'))
    parsed_dir: Path = Path(os.getenv('PARSED_DIR', './data/parsed'))
    vectorstore_dir: Path = Path(os.getenv('VECTORSTORE_DIR', str(ROOT_DIR / 'vectorstore')))
    ollama_host: str = os.getenv('OLLAMA_HOST', 'http://localhost:11434')
    ollama_model: str = os.getenv('OLLAMA_MODEL', 'mistral')
    ollama_embedding_model: str = os.getenv('OLLAMA_EMBEDDING_MODEL', 'nomic-embed-text')
    rag_parser: str = os.getenv("RAG_PARSER", "pdfjs")
    
    k: int = int(os.getenv('RETRIEVAL_K', '8'))

    backend_api_base: str = os.getenv('BACKEND_API_BASE', 'http://localhost:3002')
    studyplans_parsed_override: str | None = os.getenv('STUDYPLANS_PARSED_DIR')
    reglementations_parsed_override: str | None = os.getenv('REGULATIONS_PARSED_DIR') or os.getenv('REGLEMENTATIONS_PARSED_DIR')

    @property
    def studyplans_parsed(self) -> Path:
        if self.studyplans_parsed_override:
            return Path(self.studyplans_parsed_override)

        if self.rag_parser == "llamaparse":
            return PROJECT_ROOT / "scrapy_crawler" / 'scrapy_crawler'/ "outputs" / "parsed_fulltext_llamaparse"

        if self.rag_parser == "docling":
            return PROJECT_ROOT / "scrapy_crawler" / "scrapy_crawler" / "outputs" / "parsed_fulltext_docling"

        return DEFAULT_STUDYPLANS_PARSED

    @property
    def reglementations_parsed(self) -> Path:
        if self.reglementations_parsed_override:
            return Path(self.reglementations_parsed_override)

        if self.rag_parser == "llamaparse":
            return PROJECT_ROOT / "scrapy_crawler" / 'scrapy_crawler'/ "outputs" / "reglementation_docs" / "parsed_fulltext_llamaparse"

        if self.rag_parser == "docling":
            return PROJECT_ROOT / "scrapy_crawler" / "scrapy_crawler" / "outputs" / "reglementation_docs" / "parsed_fulltext_docling"

        return DEFAULT_REGULATIONS_PARSED

    @property
    def studyplans_index(self) -> Path:
        return self.vectorstore_dir / 'faiss_studyplans'

    @property
    def reglementations_index(self) -> Path:
        return self.vectorstore_dir / 'faiss_reglementations'


settings = Settings()
