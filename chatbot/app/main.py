from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from .performance import get_timer, log_timing, reset_request_timer, start_request_timer, timed_step

from .config import settings
from .orchestrator import answer_question
from .ollama_rag import _retrieve_metadata_aware
from .schemas import AskRequest, AskResponse
from .session_state import empty_session_state
from .ollama_rag import _retrieve_metadata_and_language_aware

if settings.rag_parser == "docling_language_aware":
    from .faiss_builders.build_faiss_docling_language_aware import build_index_for
elif settings.rag_parser == "docling":
    from .faiss_builders.build_faiss_docling import build_index_for
elif settings.rag_parser == "docling_table_semantic":
    from .faiss_builders.build_faiss_docling_table_semantic import build_index_for
elif settings.rag_parser == "docling_parent_child":
    from .faiss_builders.build_faiss_docling_parent_child import build_index_for
else:
    from .faiss_builders.build_faiss import build_index_for

app = FastAPI(title="Regulations & Studyplan Chatbot (Ollama RAG)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://localhost:4201"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_STORE: dict[str, Any] = {}

@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    timer, token = start_request_timer(f"{request.method} {request.url.path}")
    status = "ok"

    try:
        with timed_step("request.handler"):
            response = await call_next(request)
        status = "error" if response.status_code >= 500 else "ok"
        response.headers["X-Process-Time-ms"] = str(timer.elapsed_ms())
        return response
    except Exception:
        status = "error"
        raise
    finally:
        log_timing(timer.snapshot(status=status))
        reset_request_timer(token)


db_study = None
db_regl = None


def choose_db(question: str, db_study, db_regl):
    q = question.lower()

    study_keywords = [
        "studienplan",
        "study plan",
        "module",
        "modul",
        "kurs",
        "course",
        "ects",
        "pflicht",
        "mandatory",
        "bachelor",
        "master",
        "semester",
        "wirtschaftsinformatik",
        "business informatics",
        "program",
        "curriculum",
    ]

    regl_keywords = [
        "reglement",
        "regulation",
        "regulations",
        "ordnung",
        "article",
        "artikel",
        "paragraph",
        "§",
    ]

    if any(k in q for k in study_keywords):
        return db_study or db_regl

    if any(k in q for k in regl_keywords):
        return db_regl or db_study

    return db_study or db_regl


@app.on_event("startup")
def startup():
    global db_study, db_regl

    print("🚀 Chatbot API started")
    print("📄 Swagger UI: http://localhost:8000/docs")
    print(f"[startup] active parser: {settings.rag_parser}")

    try:
        db_study = build_index_for("studyplans", parser=settings.rag_parser, force_rebuild=False)
        print("[startup] loaded studyplans index")
    except Exception as e:
        db_study = None
        print(f"[startup] failed to load studyplans index: {e!r}")

    try:
        db_regl = build_index_for("reglementations", parser=settings.rag_parser, force_rebuild=False)
        print("[startup] loaded reglementations index")
    except Exception as e:
        db_regl = None
        print(f"[startup] failed to load reglementations index: {e!r}")


@app.get("/health")
def health():
    return {
        "parser": settings.rag_parser,
        "vectorstore_dir": str(settings.vectorstore_dir),
        "study_index": str(settings.studyplans_index),
        "regl_index": str(settings.reglementations_index),
        "study_loaded": db_study is not None,
        "regl_loaded": db_regl is not None,
    }


@app.post("/rebuild/studyplans")
def rebuild_studyplans():
    global db_study
    db_study = build_index_for("studyplans", parser=settings.rag_parser, force_rebuild=True)
    return {
        "status": "studyplans rebuilt",
        "parser": settings.rag_parser,
    }


@app.post("/rebuild/reglementations")
def rebuild_reglementations():
    global db_regl
    db_regl = build_index_for("reglementations", parser=settings.rag_parser, force_rebuild=True)
    return {
        "status": "reglementations rebuilt",
        "parser": settings.rag_parser,
    }


@app.post("/rebuild")
def rebuild():
    global db_study, db_regl

    result = {"parser": settings.rag_parser}

    try:
        db_study = build_index_for("studyplans", parser=settings.rag_parser, force_rebuild=True)
        result["studyplans"] = "rebuilt"
    except Exception as e:
        db_study = None
        result["studyplans"] = f"failed: {e}"

    try:
        db_regl = build_index_for("reglementations", parser=settings.rag_parser, force_rebuild=True)
        result["reglementations"] = "rebuilt"
    except Exception as e:
        db_regl = None
        result["reglementations"] = f"failed: {e}"

    return result


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    session_id = payload.session_id or "default"
    session_state = SESSION_STORE.get(session_id, empty_session_state())

    use_study = db_study
    use_regl = db_regl

    if payload.run_mode == "tool":
        use_study = None
        use_regl = None

    with timed_step("ask.answer_question"):
        result = answer_question(
            question=payload.question,
            db_study=use_study,
            db_regl=use_regl,
            language=payload.language,
            session_state=session_state,
            run_mode=payload.run_mode,
        )

    timer = get_timer()
    if timer is not None:
        result["timing"] = timer.snapshot()

    SESSION_STORE[session_id] = result.get("session_state", session_state)

    return AskResponse(**result)


@app.post("/debug/retrieve")
def debug_retrieve(payload: AskRequest):
    db = choose_db(payload.question, db_study, db_regl)

    if db is None:
        return JSONResponse(
            status_code=400,
            content={
                "error": (
                    f"Indexes not loaded for parser '{settings.rag_parser}'. "
                    "Call POST /rebuild."
                )
            },
        )

    with timed_step("debug_retrieve.rag_retrieval"):
        docs, retrieval_debug = _retrieve_metadata_and_language_aware(
            db=db,
            question=payload.question,
            k=10,
            language=payload.language,
        )
    
    return [
        {
            "source": d.metadata.get("source"),
            "page": d.metadata.get("page") if isinstance(d.metadata.get("page"), int) else d.metadata.get("page_start"),
            "snippet": d.page_content[:500],
            "metadata": d.metadata,
            "retrieval_debug": retrieval_debug,
        }
        for d in docs
    ]
