from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal

LanguageCode = Literal["de", "en", "fr"]

class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    language: Optional[LanguageCode] = None
    session_id: Optional[str] = None

class SourceSnippet(BaseModel):
    source: str
    page: Optional[int] = None
    snippet: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    source_type: str = "pdf"

class AskResponse(BaseModel):
    answer: str
    sources: List[SourceSnippet] = Field(default_factory=list)
    used_tools: List[str] = Field(default_factory=list)

    session_state: Optional[dict] = None
    plan: Optional[dict] = None
    planning_errors: Optional[str] = None