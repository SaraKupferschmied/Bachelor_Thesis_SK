from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal

LanguageCode = Literal["de", "en", "fr"]
RunMode = Literal["auto", "rag", "tool", "hybrid"]
RagSource = Literal["auto", "studyplans", "reglementations", "base_data"]

class AskRequest(BaseModel):
    question: str
    language: Optional[LanguageCode] = None
    run_mode: Optional[RunMode] = "auto"
    rag_source: Optional[RagSource] = "auto"
    session_id: Optional[str] = None
    
class SourceSnippet(BaseModel):
    source: str
    page: Optional[int] = None
    snippet: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_type: str = "pdf"

class SourceDocument(BaseModel):
    title: Optional[str] = None
    doc_key: Optional[str] = None
    doc_type: Optional[str] = None
    source_url: Optional[str] = None
    download_url: Optional[str] = None
    page: Optional[int] = None

class AskResponse(BaseModel):
    answer: str
    sources: List[SourceSnippet] = Field(default_factory=list)
    documents: List[SourceDocument] = Field(default_factory=list)
    used_tools: List[str] = Field(default_factory=list)
    session_state: Optional[dict] = None
    plan: Optional[dict] = None
    planning_errors: Optional[str] = None
    timing: Optional[Dict[str, Any]] = None