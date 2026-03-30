import requests
from typing import Any, Optional, Callable
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
# API wrappers
# ------------------------

def get_courses(
    mobility: Optional[bool] = None,
    soft_skills: Optional[bool] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {}

    if mobility is not None:
        params["mobility"] = str(mobility).lower()
    if soft_skills is not None:
        params["soft_skills"] = str(soft_skills).lower()
    if limit is not None:
        params["limit"] = str(limit)

    return _get("/courses", params=params)


def get_course_by_code(code: str) -> Optional[dict[str, Any]]:
    r = requests.get(f"{settings.backend_api_base}/courses/{code}", timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


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


def get_program_docs(program_id: int | str) -> list[dict[str, Any]]:
    return _get(f"/docs/program/{program_id}")


def get_offerings(sem_id: str) -> list[dict[str, Any]]:
    return _get("/offerings", params={"sem_id": sem_id})


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


ToolFn = Callable[..., Any]

TOOLS: dict[str, ToolFn] = {
    "get_courses": get_courses,
    "get_course_by_code": get_course_by_code,
    "get_programs": get_programs,
    "get_program_by_id": get_program_by_id,
    "get_program_courses": get_program_courses,
    "get_program_docs": get_program_docs,
    "get_offerings": get_offerings,
    "get_planner_context": get_planner_context,
}

def execute_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    if tool_name not in TOOLS:
        raise ValueError(f"Unknown tool: {tool_name}")

    tool_fn = TOOLS[tool_name]
    return tool_fn(**arguments)

# ------------------------
# Tool metadata specifications for LLM tool calling
# ------------------------

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_course_by_code",
        "description": (
            "Return one exact course by course code. "
            "Use this when the user asks about a specific course, gives a code like "
            "'UE-F24.00824', or asks a follow-up about one previously discussed course. "
            "Also use it for checking a single course property such as mobility, ECTS, "
            "learning goals, description, or faculty/domain."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The exact course code, e.g. UE-F24.00824",
                }
            },
            "required": ["code"],
        },
    },
    {
        "name": "get_courses",
        "description": (
            "Return a list of courses matching structured filters. "
            "Use this when the user asks for multiple courses, such as "
            "'name 10 mobility courses' or 'show soft skills courses'. "
            "Do not use this for one specific course code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mobility": {"type": "boolean"},
                "soft_skills": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": [],
        },
    },
    {
        "name": "get_programs",
        "description": (
            "Return all programs. Use for general questions asking for available programs."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_program_by_id",
        "description": (
            "Return a specific program by id. Use when the program id is known."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "program_id": {"type": ["integer", "string"]},
            },
            "required": ["program_id"],
        },
    },
    {
        "name": "get_program_courses",
        "description": (
            "Return courses belonging to a program. Use when the user asks which courses "
            "belong to a given program."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "program_id": {"type": ["integer", "string"]},
            },
            "required": ["program_id"],
        },
    },
    {
        "name": "get_program_docs",
        "description": (
            "Return program-related documents. Use when the user asks for docs or official "
            "documents related to a program."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "program_id": {"type": ["integer", "string"]},
            },
            "required": ["program_id"],
        },
    },
    {
        "name": "get_offerings",
        "description": (
            "Return semester offerings. Use when the user asks what is offered in a given semester."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sem_id": {"type": "string"},
            },
            "required": ["sem_id"],
        },
    },
    {
        "name": "get_planner_context",
        "description": (
            "Return structured semester planning context for a given program and semester. "
            "Use for planning questions that combine program, semester, flags, and course types."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "program_id": {"type": "integer"},
                "sem_id": {"type": "string"},
                "include_types": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "include_flags": {
                    "type": "object",
                    "additionalProperties": {"type": "boolean"},
                },
            },
            "required": ["program_id", "sem_id"],
        },
    },
]