import requests
from typing import Any, Optional
from .config import settings


def _get(path: str, params: Optional[dict[str, Any]] = None) -> Any:
    r = requests.get(f"{settings.backend_api_base}{path}", params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def _post(path: str, json_body: dict[str, Any]) -> Any:
    r = requests.post(f"{settings.backend_api_base}{path}", json=json_body, timeout=15)
    r.raise_for_status()
    return r.json()


# ------------------------
# Courses
# ------------------------

def get_courses(
    mobility: Optional[bool] = None,
    soft_skills: Optional[bool] = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {}

    if mobility is not None:
        params["mobility"] = str(mobility).lower()
    if soft_skills is not None:
        params["soft_skills"] = str(soft_skills).lower()

    return _get("/courses", params=params)


def get_course_by_code(code: str) -> Optional[dict[str, Any]]:
    r = requests.get(f"{settings.backend_api_base}/courses/{code}", timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


# ------------------------
# Programs
# ------------------------

def get_programs() -> list[dict[str, Any]]:
    return _get("/programs")


def get_program_by_id(program_id: int | str) -> Optional[dict[str, Any]]:
    r = requests.get(f"{settings.backend_api_base}/programs/{program_id}", timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_program_courses(program_id: int | str) -> list[dict[str, Any]]:
    return _get(f"/programs/{program_id}/courses")


# ------------------------
# Docs
# ------------------------

def get_program_docs(program_id: int | str) -> list[dict[str, Any]]:
    # Fastify route: GET /docs/program/:id
    return _get(f"/docs/program/{program_id}")


# ------------------------
# Offerings
# ------------------------

def get_offerings(sem_id: str) -> list[dict[str, Any]]:
    # Fastify route requires sem_id query param
    return _get("/offerings", params={"sem_id": sem_id})


# ------------------------
# Planner
# ------------------------

def get_planner_context(
    program_id: int,
    sem_id: str,
    include_types: Optional[list[str]] = None,
    include_flags: Optional[dict[str, bool]] = None,
) -> dict[str, Any]:
    body = {
        "program_id": program_id,
        "sem_id": sem_id,
        "include_types": include_types or ["Mandatory", "Elective"],
        "include_flags": include_flags or {},
    }
    return _post("/planner/context", body)


TOOLS = {
    "get_courses": get_courses,
    "get_course_by_code": get_course_by_code,
    "get_programs": get_programs,
    "get_program_by_id": get_program_by_id,
    "get_program_courses": get_program_courses,
    "get_program_docs": get_program_docs,
    "get_offerings": get_offerings,
    "get_planner_context": get_planner_context,
}