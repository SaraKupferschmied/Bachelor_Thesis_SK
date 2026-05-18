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


def _ordered_sources_for_intent(intent: str) -> list[str]:
    # Minimal quota routing: keep the old intent detector, but retrieve more
    # candidates from the expected FAISS and fewer from the other two.
    if intent == "reglementations":
        return ["reglementations", "base_data", "studyplans"]
    if intent == "studyplans":
        return ["studyplans", "base_data", "reglementations"]
    return ["base_data", "studyplans", "reglementations"]


def _quota_for_source_rank(rank: int) -> int:
    return [15, 5, 3][rank] if rank < 3 else 0


class _CombinedRetriever:
    def __init__(self, named_stores, search_kwargs=None):
        self.named_stores = [(name, store) for name, store in named_stores if store is not None]
        self.search_kwargs = search_kwargs or {}

    def invoke(self, query: str):
        intent = _query_source_intent(query)
        source_order = _ordered_sources_for_intent(intent)
        stores_by_name = dict(self.named_stores)

        docs = []
        seen = set()
        quota_debug = {}

        for rank, source_name in enumerate(source_order):
            store = stores_by_name.get(source_name)
            if store is None:
                continue

            quota = _quota_for_source_rank(rank)
            if quota <= 0:
                continue

            try:
                retrieved = store.as_retriever(search_kwargs={"k": quota}).invoke(query)
            except Exception as exc:
                print(f"[warn] combined retriever skipped {source_name}: {exc!r}")
                quota_debug[source_name] = {"quota": quota, "error": repr(exc)}
                continue

            quota_debug[source_name] = {"quota": quota, "returned": len(retrieved)}
            for doc in retrieved:
                doc.metadata = dict(doc.metadata or {})
                doc.metadata.setdefault("rag_source", source_name)
                doc.metadata.setdefault("category", source_name)
                doc.metadata["combined_retrieval_intent"] = intent
                doc.metadata["combined_retrieval_quotas"] = quota_debug

                key = (
                    doc.metadata.get("rag_source"),
                    doc.metadata.get("chunk_id"),
                    doc.metadata.get("source_file"),
                    doc.metadata.get("page") or doc.metadata.get("page_start"),
                    doc.page_content[:120],
                )
                if key in seen:
                    continue
                seen.add(key)
                docs.append(doc)

        return docs


class CombinedVectorStores:
    """Query several FAISS vectorstores without physically merging them.

    In auto mode this keeps the stores separate and retrieves fixed quotas:
    15 from the expected FAISS, 5 from the second, and 3 from the third.
    """

    def __init__(self, named_stores):
        self.named_stores = [(name, store) for name, store in named_stores if store is not None]
        self.stores = [store for _name, store in self.named_stores]
        self.docstore = _CombinedDocstore(self.stores)

    def as_retriever(self, search_kwargs=None):
        return _CombinedRetriever(self.named_stores, search_kwargs=search_kwargs)


def _merge_vectorstores(db_study=None, db_regl=None, db_base=None):
    named = [
        ("studyplans", db_study),
        ("reglementations", db_regl),
        ("base_data", db_base),
    ]
    loaded = [(name, db) for name, db in named if db is not None]
    if not loaded:
        return None
    if len(loaded) == 1:
        return loaded[0][1]
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
