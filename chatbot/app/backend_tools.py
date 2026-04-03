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
    ects: Optional[int] = None,
    faculty_id: Optional[int | str] = None,
    faculty_name: Optional[str] = None,
    domain_id: Optional[int | str] = None,
    domain_name: Optional[str] = None,
    language: Optional[str] = None,
    semester: Optional[str] = None,
    name_contains: Optional[str] = None,
    mobility: Optional[bool] = None,
    soft_skills: Optional[bool] = None,
    program_id: Optional[int | str] = None,
    program_name: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}

    if ects is not None:
        params["ects"] = ects
    if faculty_id is not None:
        params["faculty_id"] = faculty_id
    if faculty_name:
        params["faculty_name"] = faculty_name
    if domain_id is not None:
        params["domain_id"] = domain_id
    if domain_name:
        params["domain_name"] = domain_name
    if language:
        params["language"] = language
    if semester:
        params["semester"] = semester
    if name_contains:
        params["name_contains"] = name_contains
    if mobility is not None:
        params["mobility"] = str(mobility).lower()
    if soft_skills is not None:
        params["soft_skills"] = str(soft_skills).lower()
    if program_id is not None:
        params["program_id"] = program_id
    if program_name:
        params["program_name"] = program_name
    if limit is not None:
        params["limit"] = limit

    return _get("/courses", params=params)

def get_course_by_code(code: str) -> Optional[dict[str, Any]]:
    r = requests.get(f"{settings.backend_api_base}/courses/{code}", timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_programs(
    name: Optional[str] = None,
    degree_level: Optional[str] = None,
    faculty_id: Optional[int | str] = None,
    faculty_name: Optional[str] = None,
    study_start: Optional[str] = None,
    total_ects: Optional[int | float] = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}

    if name:
        params["name"] = name
    if degree_level:
        params["degree_level"] = degree_level
    if faculty_id is not None:
        params["faculty_id"] = faculty_id
    if faculty_name:
        params["faculty_name"] = faculty_name
    if study_start:
        params["study_start"] = study_start
    if total_ects is not None:
        params["total_ects"] = total_ects

    return _get("/programs", params=params)

def get_program_by_id(program_id: int | str) -> Optional[dict[str, Any]]:
    r = requests.get(f"{settings.backend_api_base}/programs/{program_id}", timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_program_courses(
    program_id: int | str,
    ects: Optional[int] = None,
    faculty_id: Optional[int | str] = None,
    faculty_name: Optional[str] = None,
    domain_id: Optional[int | str] = None,
    domain_name: Optional[str] = None,
    language: Optional[str] = None,
    semester: Optional[str] = None,
    name_contains: Optional[str] = None,
    mobility: Optional[bool] = None,
    soft_skills: Optional[bool] = None,
    course_type: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}

    if ects is not None:
        params["ects"] = ects
    if faculty_id is not None:
        params["faculty_id"] = faculty_id
    if faculty_name:
        params["faculty_name"] = faculty_name
    if domain_id is not None:
        params["domain_id"] = domain_id
    if domain_name:
        params["domain_name"] = domain_name
    if language:
        params["language"] = language
    if semester:
        params["semester"] = semester
    if name_contains:
        params["name_contains"] = name_contains
    if mobility is not None:
        params["mobility"] = str(mobility).lower()
    if soft_skills is not None:
        params["soft_skills"] = str(soft_skills).lower()
    if course_type:
        params["course_type"] = course_type
    if limit is not None:
        params["limit"] = limit

    return _get(f"/programs/{program_id}/courses", params=params)

def get_program_courses_by_metadata(
    program_name: Optional[str] = None,
    degree_level: Optional[str] = None,
    faculty_id: Optional[int | str] = None,
    faculty_name: Optional[str] = None,
    study_start: Optional[str] = None,
    total_ects: Optional[int | float] = None,
    course_type: Optional[str] = None,
    semester_type: Optional[str] = None,
    ects: Optional[int | float] = None,
    domain_id: Optional[int | str] = None,
    domain_name: Optional[str] = None,
    language: Optional[str] = None,
    semester: Optional[str] = None,
    name_contains: Optional[str] = None,
    mobility: Optional[bool] = None,
    soft_skills: Optional[bool] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}

    if program_name:
        params["program_name"] = program_name
    if degree_level:
        params["degree_level"] = degree_level
    if faculty_id is not None:
        params["faculty_id"] = faculty_id
    if faculty_name:
        params["faculty_name"] = faculty_name
    if study_start:
        params["study_start"] = study_start
    if total_ects is not None:
        params["total_ects"] = total_ects
    if course_type:
        params["course_type"] = course_type
    if semester_type:
        params["semester_type"] = semester_type
    if ects is not None:
        params["ects"] = ects
    if domain_id is not None:
        params["domain_id"] = domain_id
    if domain_name:
        params["domain_name"] = domain_name
    if language:
        params["language"] = language
    if semester:
        params["semester"] = semester
    if name_contains:
        params["name_contains"] = name_contains
    if mobility is not None:
        params["mobility"] = str(mobility).lower()
    if soft_skills is not None:
        params["soft_skills"] = str(soft_skills).lower()
    if limit is not None:
        params["limit"] = limit

    return _get("/programs/courses", params=params)

def get_program_docs(program_id: int | str) -> list[dict[str, Any]]:
    return _get(f"/docs-api/program/{program_id}")


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
    "get_program_courses_by_metadata": get_program_courses_by_metadata,
    "get_program_docs": get_program_docs,
    "get_offerings": get_offerings,
    "get_planner_context": get_planner_context,
}

def execute_tool(tool_name: str, arguments: dict[str, Any]) -> Any:
    if tool_name not in TOOLS:
        raise ValueError(f"Unknown tool: {tool_name}")

    if arguments is None:
        arguments = {}

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
            "Use this when the user asks for multiple courses or a filtered list of courses. "
            "Examples: 'show 6 ECTS English courses', 'find mobility courses', "
            "'show AI courses', 'courses with data in the name', "
            "'courses in Business Informatics'. "
            "Do not use this for one exact course code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ects": {
                    "type": "integer",
                    "description": "Filter by exact ECTS value, e.g. 6",
                },
                "faculty_id": {
                    "type": ["integer", "string"],
                    "description": "Faculty id if known",
                },
                "faculty_name": {
                    "type": "string",
                    "description": "Faculty name, e.g. Engineering",
                },
                "domain_id": {
                    "type": ["integer", "string"],
                    "description": "Domain id if known",
                },
                "domain_name": {
                    "type": "string",
                    "description": "Domain name, e.g. AI or Data Science",
                },
                "language": {
                    "type": "string",
                    "description": "Course language, e.g. English or German",
                },
                "semester": {
                    "type": "string",
                    "description": "Semester id or semester label, e.g. FS-2026 or Autumn",
                },
                "name_contains": {
                    "type": "string",
                    "description": "Keyword that should appear in the course name",
                },
                "mobility": {
                    "type": "boolean",
                    "description": "Whether the course is a mobility course",
                },
                "soft_skills": {
                    "type": "boolean",
                    "description": "Whether the course is a soft skills course",
                },
                "program_id": {
                    "type": ["integer", "string"],
                    "description": "Program id if known",
                },
                "program_name": {
                    "type": "string",
                    "description": "Program name, e.g. Business Informatics",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum number of courses to return",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_programs",
        "description": (
            "Return study programs matching optional structured filters. "
            "Use this when the user asks for programs by name, degree level, faculty, "
            "study start, or total ECTS. Examples: "
            "'show bachelor programs', 'find business informatics programs', "
            "'programs in Engineering', 'master programs starting in Autumn'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "degree_level": {
                    "type": "string",
                    "enum": ["Bachelor", "Master", "Doctorate"]
                },
                "faculty_id": {"type": ["integer", "string"]},
                "faculty_name": {"type": "string"},
                "study_start": {
                    "type": "string",
                    "enum": ["Autumn", "Spring", "Both"]
                },
                "total_ects": {"type": ["integer", "number"]},
            },
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
            "Return courses belonging to one specific program. "
            "Use this when the user asks for courses within a program and the program id is known, "
            "or after a previous step identified the program. "
            "Supports additional filtering such as ects, language, semester, course type, "
            "and course name keywords."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "program_id": {
                    "type": ["integer", "string"],
                    "description": "The program id",
                },
                "ects": {
                    "type": "integer",
                    "description": "Filter by exact ECTS value",
                },
                "faculty_id": {
                    "type": ["integer", "string"],
                },
                "faculty_name": {
                    "type": "string",
                },
                "domain_id": {
                    "type": ["integer", "string"],
                },
                "domain_name": {
                    "type": "string",
                },
                "language": {
                    "type": "string",
                },
                "semester": {
                    "type": "string",
                },
                "course_type": {
                    "type": "string",
                    "description": "Program course type, e.g. Mandatory or Elective",
                },
                "name_contains": {
                    "type": "string",
                },
                "mobility": {
                    "type": "boolean",
                },
                "soft_skills": {
                    "type": "boolean",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["program_id"],
        },
    },
    {
        "name": "get_program_courses_by_metadata",
        "description": (
            "Return courses belonging to one or more programs selected by program metadata "
            "instead of program_id. Use this when the user asks for courses in a named program "
            "but does not know the program id, or when the program name may need disambiguation "
            "using degree level, total ECTS, faculty, or study start. "
            "Examples: 'show mandatory courses in the Bachelor Business Informatics program', "
            "'find English electives in the Master Data Science program'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "program_name": {"type": "string"},
                "degree_level": {
                    "type": "string",
                    "enum": ["Bachelor", "Master", "Doctorate"]
                },
                "faculty_id": {"type": ["integer", "string"]},
                "faculty_name": {"type": "string"},
                "study_start": {
                    "type": "string",
                    "enum": ["Autumn", "Spring", "Both"]
                },
                "total_ects": {"type": ["integer", "number"]},

                "course_type": {
                    "type": "string",
                    "enum": ["Mandatory", "Elective"]
                },
                "semester_type": {
                    "type": "string",
                    "enum": ["Autumn", "Spring"]
                },
                "ects": {"type": ["integer", "number"]},
                "domain_id": {"type": ["integer", "string"]},
                "domain_name": {"type": "string"},
                "language": {"type": "string"},
                "semester": {"type": "string"},
                "name_contains": {"type": "string"},
                "mobility": {"type": "boolean"},
                "soft_skills": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": [],
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