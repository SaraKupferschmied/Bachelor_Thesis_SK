from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .schemas import AskRequest, AskResponse
from .orchestrator import answer_question
from .config import settings
from .session_state import empty_session_state
from .build_faiss import build_index_for

app = FastAPI(title="Regulations & Studyplan Chatbot (Ollama RAG)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4201"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_STUDY = None
DB_REGL = None
SESSION_STORE: dict[str, Any] = {}

def choose_db(question: str):
    q = question.lower()
    study_keywords = [
        "studienplan", "study plan", "module", "modul", "kurs", "course",
        "ects", "pflicht", "mandatory", "bachelor", "master", "semester",
        "wirtschaftsinformatik", "business informatics",
    ]
    if any(k in q for k in study_keywords):
        return DB_STUDY
    return DB_REGL


@app.on_event("startup")
def startup():
    global DB_STUDY, DB_REGL

    DB_STUDY = None
    DB_REGL = None

    try:
        DB_STUDY = build_index_for("studyplans", parser=settings.rag_parser, force_rebuild=False)
        print("[startup] studyplans loaded")
    except Exception as e:
        print("[startup] studyplans failed:", repr(e))

    try:
        DB_REGL = build_index_for("reglementations", parser=settings.rag_parser, force_rebuild=False)
        print("[startup] reglementations loaded")
    except Exception as e:
        print("[startup] reglementations failed:", repr(e))

    print("🚀 Chatbot API started")
    print("📄 Swagger UI: http://localhost:8001/docs")

@app.get("/health")
def health():
    return {"study_loaded": DB_STUDY is not None, "regl_loaded": DB_REGL is not None}


@app.post("/rebuild/reglementations")
def rebuild_reglementations():
    global DB_REGL

    DB_REGL = build_index_for("reglementations", parser=settings.rag_parser, force_rebuild=False)

    return {"status": "reglementations rebuilt"}


@app.post("/rebuild/studyplans")
def rebuild_studyplans():
    global DB_STUDY

    DB_STUDY = build_index_for("studyplans", parser=settings.rag_parser, force_rebuild=False)

    return {"status": "studyplans rebuilt"}


@app.post("/rebuild")
def rebuild():
    global DB_STUDY, DB_REGL

    result = {}

    try:
        DB_STUDY = build_index_for("studyplans", parser=settings.rag_parser, force_rebuild=False)
        result["studyplans"] = "rebuilt"
    except Exception as e:
        DB_STUDY = None
        result["studyplans"] = f"failed: {e}"

    try:
        DB_REGL = build_index_for("reglementations", parser=settings.rag_parser, force_rebuild=False)
        result["reglementations"] = "rebuilt"
    except Exception as e:
        DB_REGL = None
        result["reglementations"] = f"failed: {e}"

    return result


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    session_id = payload.session_id or "default"

    session_state = SESSION_STORE.get(session_id, empty_session_state())

    result = answer_question(
        question=payload.question,
        db_study=DB_STUDY,
        db_regl=DB_REGL,
        language=payload.language,
        session_state=session_state,
        run_mode=payload.run_mode,
    )

    SESSION_STORE[session_id] = result.get("session_state", session_state)

    return AskResponse(**result)


@app.post("/debug/retrieve")
def debug_retrieve(payload: AskRequest):
    db = choose_db(payload.question)
    if db is None:
        return JSONResponse(status_code=400, content={"error": "Indexes not loaded. Call POST /rebuild."})

    docs = db.as_retriever(search_kwargs={"k": 10}).invoke(payload.question)
    return [
        {
            "source": d.metadata.get("source"),
            "page": d.metadata.get("page") if isinstance(d.metadata.get("page"), int) else None,
            "snippet": d.page_content[:300],
        }
        for d in docs
    ]