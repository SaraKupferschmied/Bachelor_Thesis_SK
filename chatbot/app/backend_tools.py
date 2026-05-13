import requests
from typing import Any, Optional, Callable
from .config import settings


def _get(path: str, params: Optional[dict[str, Any]] = None) -> Any:
    r = requests.get(f"{settings.backend_api_base}{path}", params=params, timeout=8)
    r.raise_for_status()
    return r.json()


def _post(path: str, json_body: dict[str, Any]) -> Any:
    r = requests.post(f"{settings.backend_api_base}{path}", json=json_body, timeout=12)
    r.raise_for_status()
    return r.json()


# ------------------------
# API wrappers
# ------------------------

def get_courses(
    ects: Optional[int | float | str] = None,
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
    total_ects: Optional[int | float | str] = None,
    program_type: Optional[str] = None,
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
    if program_type:
        params["program_type"] = program_type

    return _get("/programs", params=params)

def get_program_by_id(program_id: int | str) -> Optional[dict[str, Any]]:
    r = requests.get(f"{settings.backend_api_base}/programs/{program_id}", timeout=10)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_program_courses(
    program_id: int | str,
    ects: Optional[int | float | str] = None,
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
    program_en: Optional[str] = None,
    program_de: Optional[str] = None,
    program_fr: Optional[str] = None,
    degree_level: Optional[str] = None,
    faculty_id: Optional[int | str] = None,
    faculty_name: Optional[str] = None,
    study_start: Optional[str] = None,
    total_ects: Optional[int | float | str] = None,
    course_type: Optional[str] = None,
    semester_type: Optional[str] = None,
    ects: Optional[int | float | str] = None,
    domain_id: Optional[int | str] = None,
    domain_name: Optional[str] = None,
    language: Optional[str] = None,
    semester: Optional[str] = None,
    name_contains: Optional[str] = None,
    mobility: Optional[bool] = None,
    soft_skills: Optional[bool] = None,
    section_contains: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}

    if program_en:
        params["program_en"] = program_en
    if program_de:
        params["program_de"] = program_de
    if program_fr:
        params["program_fr"] = program_fr
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
    if section_contains:
        params["section_contains"] = section_contains
    if limit is not None:
        params["limit"] = limit

    return _get("/programs/courses", params=params)


def get_program_course_sections(
    program_id: int | str,
    course_code: Optional[str] = None,
    section_heading: Optional[str] = None,
    non_empty_only: Optional[bool] = True,
    limit: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Return section headings from the program-course "consists of" table.

    These headings may contain useful curriculum placement hints, such as proposed
    study year/semester, but they can also contain noisy import text. The chatbot
    should treat them as auxiliary metadata, not as authoritative requirements.
    """
    params: dict[str, Any] = {}
    if course_code:
        params["code"] = course_code
    if section_heading:
        params["section_contains"] = section_heading
    if non_empty_only is not None:
        params["non_empty_only"] = str(non_empty_only).lower()
    if limit is not None:
        params["limit"] = limit
    return _get(f"/programs/{program_id}/course-sections", params=params)

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

def get_planner_programs(
    q: str | None = None,
    degree_level: str | None = None,
    locale: str = "en",
    limit: int = 20,
) -> list[dict[str, Any]]:
    params = {"locale": locale, "limit": limit}
    if q:
        params["q"] = q
    if degree_level:
        params["degree_level"] = degree_level
    return _get("/planner/programs", params=params)


def get_planner_courses(
    sem_id: str,
    program_ids: list[int],
    locale: str = "en",
) -> dict[str, Any]:
    return _get(
        "/planner/courses",
        params={
            "sem_id": sem_id,
            "program_ids": ",".join(str(x) for x in program_ids),
            "locale": locale,
        },
    )


def get_study_program_plan(
    program_id: int | str,
    semesters: int = 8,
    locale: str = "en",
    total_ects: Optional[int | float | str] = None,
    selected_elective_codes: Optional[list[str] | str] = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "program_id": program_id,
        "semesters": semesters,
        "locale": locale,
    }
    if total_ects is not None:
        params["total_ects"] = total_ects
    if selected_elective_codes:
        if isinstance(selected_elective_codes, list):
            params["selected_elective_codes"] = ",".join(selected_elective_codes)
        else:
            params["selected_elective_codes"] = selected_elective_codes
    return _get("/planner/study-program-plan-proposal", params=params)


def get_mobility_courses(
    semesters: list[str],
    interest: str,
    language: Optional[str] = None,
    limit_per_semester: int = 30,
) -> dict[str, Any]:
    by_semester: dict[str, list[dict[str, Any]]] = {}

    for sem in semesters[:2]:
        matches = []

        for search_fn in [
            lambda: get_program_courses_by_metadata(
                program_name=interest,
                degree_level="Bachelor",
                semester=sem,
                mobility=True,
                language=language,
                limit=limit_per_semester,
            ),
            lambda: get_courses(
                domain_name=interest,
                mobility=True,
                semester=sem,
                language=language,
                limit=limit_per_semester,
            ),
            lambda: get_courses(
                name_contains=interest,
                mobility=True,
                semester=sem,
                language=language,
                limit=limit_per_semester,
            ),
        ]:
            rows = search_fn()

            for row in rows:
                row = dict(row)
                row["requested_semester"] = sem
                row["matched_by"] = "mobility_search"
                matches.append(row)

            if len(matches) >= 8:
                break

        by_semester[sem] = matches

    return {
        "semesters": semesters[:2],
        "interest": interest,
        "courses_by_semester": by_semester,
    }

ToolFn = Callable[..., Any]

TOOLS: dict[str, ToolFn] = {
    "get_courses": get_courses,
    "get_mobility_courses": get_mobility_courses,
    "get_course_by_code": get_course_by_code,
    "get_programs": get_programs,
    "get_program_course_sections": get_program_course_sections,
    "get_program_by_id": get_program_by_id,
    "get_program_courses": get_program_courses,
    "get_program_courses_by_metadata": get_program_courses_by_metadata,
    "get_program_docs": get_program_docs,
    "get_offerings": get_offerings,
    "get_planner_context": get_planner_context,
    "get_planner_programs": get_planner_programs,
    "get_planner_courses": get_planner_courses,
    "get_study_program_plan": get_study_program_plan,
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
                    "type": ["integer", "number", "string"],
                    "description": "Filter by ECTS. Supports exact values like 6 and comparisons as strings like >6, >=6, <3, <=3.",
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
            "FAST DB TOOL: list study programs. Best for questions like 'all Bachelor programs', "
            "'minor programs', 'mono Master programs', or programs by ECTS/faculty/start. "
            "Use program_type for minor/major/mono and total_ects for numeric comparisons. "
            "Returns authoritative database rows; prefer over RAG for program lists."
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
                "total_ects": {"type": ["integer", "number", "string"], "description": "Filter by total ECTS. Supports exact values like 180 and comparisons as strings like >90, >=120, <90, <=60."},
                "program_type": {"type": "string", "enum": ["minor", "major", "mono"], "description": "Derived from degree level and total ECTS: minor <90, Bachelor major 90-150, Master major 90, Bachelor mono 180, Master mono 120."},
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
                    "type": ["integer", "number", "string"],
                    "description": "Filter by ECTS. Supports exact values like 6 and comparisons as strings like >6, >=6, <3, <=3.",
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
            "FAST DB TOOL: courses for a named study program when no program_id is known. "
            "Best for 'mandatory courses in Bachelor Business Informatics 180 ECTS', "
            "'courses in Economics Bachelor', electives, ECTS/language/semester filters, and first-year/section questions. "
            "Returns consist_of.description as program_course_description when imported; use this as study-year/section hint."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "program_en": {"type": "string"},
                "program_de": {"type": "string"},
                "program_fr": {"type": "string"},
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
                "total_ects": {"type": ["integer", "number", "string"], "description": "Filter by total program ECTS. Supports exact values and comparisons like >90 or <120."},

                "course_type": {
                    "type": "string",
                    "enum": ["Mandatory", "Elective"]
                },
                "semester_type": {
                    "type": "string",
                    "enum": ["Autumn", "Spring"]
                },
                "ects": {"type": ["integer", "number", "string"], "description": "Filter by ECTS. Supports exact values and comparisons like >6 or <3."},
                "domain_id": {"type": ["integer", "string"]},
                "domain_name": {"type": "string"},
                "language": {"type": "string"},
                "semester": {"type": "string"},
                "name_contains": {"type": "string"},
                "mobility": {"type": "boolean"},
                "soft_skills": {"type": "boolean"},
                "section_contains": {"type": "string", "description": "Filter imported program-course description/section text, e.g. first year or assessment."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": [],
        },
    },

    {
        "name": "get_program_course_sections",
        "description": (
            "FAST DB TOOL: return consist_of.description values for courses in a specific program. "
            "Use this when the user asks where courses are placed in a study plan, proposed study year, "
            "curriculum section headings, or additional program-course table metadata. "
            "The returned field is named section_heading for compatibility, but it comes from the database description column and may be noisy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "program_id": {"type": ["integer", "string"], "description": "The program id"},
                "course_code": {"type": "string", "description": "Optional exact course code"},
                "section_heading": {"type": "string", "description": "Optional substring filter on the imported description/section text"},
                "non_empty_only": {"type": "boolean", "description": "Whether to return only rows with a non-empty heading", "default": True},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
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