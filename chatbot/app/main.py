from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.requests import Request

from .performance import get_timer, log_timing, reset_request_timer, start_request_timer, timed_step

from .config import settings
from .orchestrator import answer_question
from .schemas import AskRequest, AskResponse
from .session_state import empty_session_state
from .ollama_rag import _retrieve_metadata_and_language_aware
from langchain_community.vectorstores import FAISS
from langchain_ollama import OllamaEmbeddings

if settings.rag_parser == "docling_language_aware":
    from .build_faiss_docling_language_aware import build_index_for
elif settings.rag_parser == "docling":
    from .build_faiss_docling import build_index_for
elif settings.rag_parser == "docling_table_semantic":
    from .build_faiss_docling_table_semantic import build_index_for
elif settings.rag_parser == "docling_parent_child":
    from .build_faiss_docling_parent_child import build_index_for
else:
    from .build_faiss import build_index_for

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
db_base = None
db_rag = None


def _load_faiss_index(index_dir, label: str):
    index_dir = settings.vectorstore_dir / index_dir if isinstance(index_dir, str) else index_dir
    faiss_file = index_dir / "index.faiss"
    pkl_file = index_dir / "index.pkl"

    if not faiss_file.exists() or not pkl_file.exists():
        raise FileNotFoundError(f"Missing FAISS files for {label}: {index_dir}")

    # These indexes are created by the project build scripts with OllamaEmbeddings
    # (settings.ollama_embedding_model). Loading/querying them with the old
    # HuggingFace embedding object can cause FAISS dimension assertion errors.
    embeddings = OllamaEmbeddings(
        model=settings.ollama_embedding_model,
        base_url=settings.ollama_host,
    )
    db = FAISS.load_local(
        str(index_dir),
        embeddings,
        allow_dangerous_deserialization=True,
    )
    _tag_vectorstore_docs(db, label)
    return db


def _tag_vectorstore_docs(db, label: str):
    for doc in db.docstore._dict.values():
        doc.metadata.setdefault("rag_source", label)
        doc.metadata.setdefault("category", label)


def _base_data_index_path():
    # Keep this helper instead of relying only on Settings.base_data_index so the
    # app still starts if an older config.py is mounted in Docker.
    return getattr(settings, "base_data_index", settings.vectorstore_dir / "faiss_BaseData")


class _CombinedDocstore:
    def __init__(self, stores):
        self._dict = {}
        for store_index, store in enumerate(stores):
            docs = getattr(getattr(store, "docstore", None), "_dict", {}) or {}
            for doc_id, doc in docs.items():
                self._dict[f"store{store_index}:{doc_id}"] = doc


def _query_source_intent(query: str) -> str:
    q = (query or "").lower()

    regulations_terms = [
        "reglement", "regulation", "regulations", "ordnung", "article", "artikel",
        "paragraph", "§", "admission requirements", "zulassung", "exam regulation",
    ]
    studyplan_terms = [
        "course", "courses", "module", "modules", "semester", "study plan",
        "curriculum", "kurs", "kurse", "modul", "studienplan", "pflichtfach",
        "wahlfach", "recommended course", "obligatory course", "course code",
    ]
    base_terms = [
        "how many ects", "how many credits", "contains", "comprise", "consist of",
        "duration", "how long", "what is a bachelor", "what is a master",
        "bachelor program", "bachelor programme", "master program", "master programme",
        "study program", "study programme", "degree", "overview", "base data",
    ]

    if any(term in q for term in regulations_terms):
        return "reglementations"
    if any(term in q for term in studyplan_terms):
        return "studyplans"
    if any(term in q for term in base_terms):
        return "base_data"
    return "base_data"


def _doc_text_score(query: str, doc) -> float:
    import re

    q_tokens = set(re.findall(r"[a-zA-ZäöüÄÖÜéèàç0-9]+", (query or "").lower()))
    text = ((doc.page_content or "") + " " + " ".join(str(v) for v in (doc.metadata or {}).values())).lower()
    return float(sum(1 for token in q_tokens if token and token in text))


def _source_priority_score(intent: str, doc) -> float:
    source = str((doc.metadata or {}).get("rag_source") or (doc.metadata or {}).get("category") or "")
    if intent == "base_data":
        return {"base_data": 100.0, "reglementations": 35.0, "studyplans": 10.0}.get(source, 0.0)
    if intent == "reglementations":
        return {"reglementations": 100.0, "base_data": 35.0, "studyplans": 10.0}.get(source, 0.0)
    return {"studyplans": 100.0, "base_data": 35.0, "reglementations": 20.0}.get(source, 0.0)


class _CombinedRetriever:
    def __init__(self, stores, search_kwargs=None):
        self.stores = list(stores)
        self.search_kwargs = search_kwargs or {}

    def invoke(self, query: str):
        k = int(self.search_kwargs.get("k", 10))
        # Pull enough from every store so later stores, especially BaseData, are not
        # starved by the final k limit. Final ranking below decides the best mix.
        per_store_k = max(k * 4, 24)
        intent = _query_source_intent(query)
        docs = []
        seen = set()

        for store in self.stores:
            try:
                retrieved = store.as_retriever(search_kwargs={"k": per_store_k}).invoke(query)
            except Exception as exc:
                print(f"[warn] combined retriever skipped one store: {exc!r}")
                continue

            for rank, doc in enumerate(retrieved):
                key = (
                    doc.metadata.get("rag_source"),
                    doc.metadata.get("chunk_id"),
                    doc.metadata.get("source_file"),
                    doc.page_content[:120],
                )
                if key in seen:
                    continue
                seen.add(key)
                docs.append((doc, rank))

        ranked = sorted(
            docs,
            key=lambda item: (
                _source_priority_score(intent, item[0])
                + _doc_text_score(query, item[0]) * 3.0
                - item[1] * 0.05
            ),
            reverse=True,
        )

        return [doc for doc, _rank in ranked[:k]]


class CombinedVectorStores:
    """Query several FAISS vectorstores without physically merging them.

    FAISS.merge_from only works when all indexes have the same vector dimension.
    Existing indexes in this project may have been built with different embedding
    models, so this wrapper keeps them separate and combines retrieval results at
    query time. It also exposes a combined docstore so the existing metadata-aware
    code can still detect programmes across all loaded stores.
    """

    def __init__(self, stores):
        self.stores = [store for store in stores if store is not None]
        self.docstore = _CombinedDocstore(self.stores)

    def as_retriever(self, search_kwargs=None):
        return _CombinedRetriever(self.stores, search_kwargs=search_kwargs)


def _merge_vectorstores(*stores):
    loaded = [db for db in stores if db is not None]
    if not loaded:
        return None
    if len(loaded) == 1:
        return loaded[0]
    return CombinedVectorStores(loaded)


def _load_existing_or_build(target: str, label: str):
    # For docling_table_semantic, studyplans use the table-semantic index, but
    # regulations intentionally use the already-built normal faiss_reglementations
    # store. Loading directly avoids trying to rebuild from a non-existing
    # parsed_fulltext_docling_new regulations folder.
    if settings.rag_parser == "docling_table_semantic" and target in {"reglementations", "regulations"}:
        return _load_faiss_index(settings.reglementations_index, label)
    return build_index_for(target, parser=settings.rag_parser, force_rebuild=False)


def choose_db(question: str, db_study=None, db_regl=None, db_base=None, db_all=None):
    if db_all is not None:
        return db_all
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
        return db_study or db_base or db_regl

    if any(k in q for k in regl_keywords):
        return db_regl or db_base or db_study

    return db_base or db_study or db_regl


@app.on_event("startup")
def startup():
    global db_study, db_regl, db_base, db_rag

    print("🚀 Chatbot API started")
    print("📄 Swagger UI: http://localhost:8000/docs")
    print(f"[startup] active parser: {settings.rag_parser}")

    try:
        db_study = _load_existing_or_build("studyplans", "studyplans")
        _tag_vectorstore_docs(db_study, "studyplans")
        print("[startup] loaded studyplans index")
    except Exception as e:
        db_study = None
        print(f"[startup] failed to load studyplans index: {e!r}")

    try:
        db_regl = _load_existing_or_build("reglementations", "reglementations")
        _tag_vectorstore_docs(db_regl, "reglementations")
        print("[startup] loaded reglementations index")
    except Exception as e:
        db_regl = None
        print(f"[startup] failed to load reglementations index: {e!r}")

    try:
        db_base = _load_faiss_index(_base_data_index_path(), "base_data")
        print("[startup] loaded base data index")
    except Exception as e:
        db_base = None
        print(f"[startup] failed to load base data index: {e!r}")

    db_rag = _merge_vectorstores(db_study, db_regl, db_base)
    print(f"[startup] combined RAG index loaded: {db_rag is not None}")


@app.get("/health")
def health():
    return {
        "parser": settings.rag_parser,
        "vectorstore_dir": str(settings.vectorstore_dir),
        "study_index": str(settings.studyplans_index),
        "regl_index": str(settings.reglementations_index),
        "base_data_index": str(_base_data_index_path()),
        "study_loaded": db_study is not None,
        "regl_loaded": db_regl is not None,
        "base_data_loaded": db_base is not None,
        "combined_rag_loaded": db_rag is not None,
    }


@app.post("/rebuild/studyplans")
def rebuild_studyplans():
    global db_study, db_rag
    db_study = build_index_for("studyplans", parser=settings.rag_parser, force_rebuild=True)
    _tag_vectorstore_docs(db_study, "studyplans")
    db_rag = _merge_vectorstores(db_study, db_regl, db_base)
    return {
        "status": "studyplans rebuilt",
        "parser": settings.rag_parser,
    }


@app.post("/rebuild/reglementations")
def rebuild_reglementations():
    global db_regl, db_rag
    db_regl = build_index_for("reglementations", parser=settings.rag_parser, force_rebuild=True)
    _tag_vectorstore_docs(db_regl, "reglementations")
    db_rag = _merge_vectorstores(db_study, db_regl, db_base)
    return {
        "status": "reglementations rebuilt",
        "parser": settings.rag_parser,
    }


@app.post("/rebuild")
def rebuild():
    global db_study, db_regl, db_rag

    result = {"parser": settings.rag_parser}

    try:
        db_study = build_index_for("studyplans", parser=settings.rag_parser, force_rebuild=True)
        _tag_vectorstore_docs(db_study, "studyplans")
        result["studyplans"] = "rebuilt"
    except Exception as e:
        db_study = None
        result["studyplans"] = f"failed: {e}"

    try:
        db_regl = build_index_for("reglementations", parser=settings.rag_parser, force_rebuild=True)
        _tag_vectorstore_docs(db_regl, "reglementations")
        result["reglementations"] = "rebuilt"
    except Exception as e:
        db_regl = None
        result["reglementations"] = f"failed: {e}"

    db_rag = _merge_vectorstores(db_study, db_regl, db_base)
    result["base_data"] = "loaded" if db_base is not None else "not loaded"
    result["combined_rag_loaded"] = db_rag is not None

    return result


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest) -> AskResponse:
    session_id = payload.session_id or "default"
    session_state = SESSION_STORE.get(session_id, empty_session_state())

    use_study = db_rag
    use_regl = None

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
    db = choose_db(payload.question, db_study, db_regl, db_base, db_rag)

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
