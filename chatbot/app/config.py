from pydantic import BaseModel
import os
from pathlib import Path


class Settings(BaseModel):
    pdf_dir: Path = Path(os.getenv("PDF_DIR", "./data/pdfs"))
    parsed_dir: Path = Path(os.getenv("PARSED_DIR", "./data/parsed"))
    vectorstore_dir: Path = Path(os.getenv("VECTORSTORE_DIR", "./vectorstore"))

    ollama_model: str = os.getenv("OLLAMA_MODEL", "mistral")
    k: int = int(os.getenv("RETRIEVAL_K", "4"))

    backend_api_base: str = os.getenv("BACKEND_API_BASE", "http://localhost:3000")

    @property
    def studyplans_parsed(self) -> Path:
        return self.parsed_dir / "studyplans"

    @property
    def reglementations_parsed(self) -> Path:
        return self.parsed_dir / "reglementations"

    @property
    def studyplans_index(self) -> Path:
        return self.vectorstore_dir / "faiss_studyplans"

    @property
    def reglementations_index(self) -> Path:
        return self.vectorstore_dir / "faiss_reglementations"


settings = Settings()