import re
from typing import Any, Dict, List
import logging
import json

from .planner import plan_tool_usage
from .backend_tools import TOOLS
from .ollama_rag import answer_question as rag_answer
from .session_state import update_session_state
from .hero_semester import is_plan_semester_hero, start_plan_semester_flow
from .performance import timed_step
from .config import settings

from langchain_ollama import ChatOllama
from langchain_core.prompts import ChatPromptTemplate

logger = logging.getLogger(__name__)

def _has_tool_result(result: Any) -> bool:
    if result is None:
        return False
    if isinstance(result, list):
        return len(result) > 0
    if isinstance(result, dict):
        return len(result) > 0
    if isinstance(result, str):
        return bool(result.strip())
    return True

def _compact_tool_results(tool_results: List[Dict[str, Any]], max_rows: int = 80) -> List[Dict[str, Any]]:
    """Keep enough structured data for answer synthesis without flooding the LLM."""
    compact: List[Dict[str, Any]] = []
    for item in tool_results:
        result = item.get("result")
        if isinstance(result, list):
            compact.append({
                "tool": item.get("tool"),
                "row_count": len(result),
                "rows": result[:max_rows],
                "truncated": len(result) > max_rows,
            })
        else:
            compact.append({
                "tool": item.get("tool"),
                "result": result,
                "error": item.get("error"),
            })
    return compact


def _synthesize_tool_answer(question: str, plan: Dict[str, Any], tool_results: List[Dict[str, Any]]) -> str:
    """Turn raw API rows into a chatbot answer.

    This is the key difference between a chatbot and a hard-coded backend route:
    tools retrieve reliable structured facts; the LLM decides how to answer the
    user's actual question from those facts.
    """
    llm = ChatOllama(
        model=settings.ollama_model,
        temperature=0,
        base_url=settings.ollama_host,
    )
    prompt = ChatPromptTemplate.from_template("""
You are a university study chatbot. Answer the user's question using ONLY the tool results.

User question:
{question}

Planner decision:
{plan}

Tool results:
{tool_results}

Instructions:
- Do not dump raw database rows unless the user explicitly asked for a list.
- If the user asked for one fact, answer that fact directly.
- If multiple database rows match, explain the relevant distinction briefly.
  Example: a program may exist as 30/60 ECTS minor and 180 ECTS mono.
- If the user asked for a calculation or comparison, compute it from the tool results.
- If the tool result is insufficient, say exactly what is missing.
- Keep the answer concise and conversational.
""")
    msg = prompt.format_messages(
        question=question,
        plan=json.dumps(plan, ensure_ascii=False),
        tool_results=json.dumps(_compact_tool_results(tool_results), ensure_ascii=False, indent=2, default=str),
    )
    with timed_step("tool_answer.synthesize"):
        resp = llm.invoke(msg)
    return str(resp.content).strip()



def _program_type(item: dict[str, Any]) -> str | None:
    value = item.get("program_type")
    if value:
        return str(value)
    total = item.get("total_ects")
    degree = item.get("degree_level")
    try:
        total_f = float(total)
    except Exception:
        return None
    if total_f < 90:
        return "minor"
    if degree == "Master" and total_f == 90:
        return "major"
    if degree == "Bachelor" and 90 <= total_f < 180:
        return "major"
    if degree == "Master" and total_f == 120:
        return "mono"
    if degree == "Bachelor" and total_f == 180:
        return "mono"
    return None

def _looks_like_new_general_question(question: str) -> bool:
    q = question.lower().strip()

    general_markers = [
        "what programs",
        "which programs",
        "what can i study",
        "what courses",
        "tell me about",
        "explain",
        "regulation",
        "regulations",
        "requirements",
        "deadline",
        "how do i",
        "can you explain",
        "something else",
        "now can you tell me",
        "next up",
        "new topic",
    ]

    return any(marker in q for marker in general_markers)

def _question_asks_first_year(question: str) -> bool:
    q = question.lower()
    return any(x in q for x in ["first study year", "first year", "1st year", "1. year", "1st study year", "erstes studienjahr", "1. studienjahr"])


def _looks_like_first_year_description(text: str | None) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(x in t for x in ["first year", "first study year", "1st year", "1st study year", "1. year", "1. studienjahr", "erstes studienjahr", "1ère année", "première année"])


def _format_dict_result(tool_name: str, item: dict[str, Any]) -> str:
    if tool_name == "get_course_by_code":
        name = item.get("name") or item.get("course_name") or item.get("code") or "Course"
        lines = [f"**{name}**"]
        for label, key in [
            ("Code", "code"), ("ECTS", "ects"), ("Faculty", "faculty_name"),
            ("Domain", "domain_name"), ("Mobility", "mobility"), ("Soft skills", "soft_skills"),
            ("Description", "description"),
        ]:
            if item.get(key) is not None:
                lines.append(f"- {label}: {item.get(key)}")
        return "\n".join(lines)
    return "\n".join(f"- {k}: {v}" for k, v in item.items() if v is not None)


def _format_tool_result(tool_name: str, result: Any, question: str = "") -> str:
    if result is None:
        return f"{tool_name}: no result found."

    if isinstance(result, dict):
        if not result:
            return f"{tool_name}: no result found."
        return _format_dict_result(tool_name, result)

    if isinstance(result, list):
        if not result:
            return f"{tool_name}: no matching results found."

        filtered_for_first_year = False
        if tool_name == "get_program_courses_by_metadata" and _question_asks_first_year(question):
            first_year = [
                item for item in result
                if isinstance(item, dict) and _looks_like_first_year_description(str(item.get("program_course_description") or item.get("section_heading") or ""))
            ]
            if first_year:
                result = first_year
                filtered_for_first_year = True

        max_items = 300
        shown = result[:max_items]
        lines = [f"Found {len(result)} result(s):"]
        if filtered_for_first_year:
            lines.append("Filtered to rows whose imported program-course description looks like first-year metadata.")

        for item in shown:
            if isinstance(item, dict):
                if tool_name == "get_programs":
                    name = item.get("name") or item.get("name_en") or "Program"
                    bits = []
                    if item.get("degree_level"):
                        bits.append(str(item.get("degree_level")))
                    if item.get("total_ects") is not None:
                        bits.append(f"{item.get('total_ects')} ECTS")
                    ptype = _program_type(item)
                    if ptype:
                        bits.append(ptype)
                    if item.get("program_id") is not None:
                        bits.append(f"id {item.get('program_id')}")
                    if item.get("faculty_name"):
                        bits.append(str(item.get("faculty_name")))
                    lines.append(f"- **{name}**" + (f" — {', '.join(bits)}" if bits else ""))
                    continue

                if tool_name in {"get_program_courses", "get_program_courses_by_metadata"}:
                    code = item.get("code")
                    name = item.get("name") or item.get("course_name") or code or "Course"
                    bits = []
                    if code:
                        bits.append(str(code))
                    if item.get("ects") is not None:
                        bits.append(f"{item.get('ects')} ECTS")
                    if item.get("course_type"):
                        bits.append(str(item.get("course_type")))
                    program_name = item.get("program_name_en") or item.get("program_name")
                    if program_name:
                        bits.append(str(program_name))
                    if item.get("total_ects") is not None:
                        bits.append(f"program {item.get('total_ects')} ECTS")
                    section = item.get("program_course_description") or item.get("section_heading")
                    lines.append(f"- **{name}**" + (f" — {', '.join(bits)}" if bits else ""))
                    if section:
                        lines.append(f"  - Section/description hint: {section}")
                    continue

                if tool_name == "get_program_course_sections":
                    name = item.get("course_name") or item.get("canonical_course_name") or item.get("code") or "Course"
                    bits = []
                    if item.get("code"):
                        bits.append(str(item.get("code")))
                    if item.get("course_type"):
                        bits.append(str(item.get("course_type")))
                    if item.get("ects") is not None:
                        bits.append(f"{item.get('ects')} ECTS")
                    lines.append(f"- **{name}**" + (f" — {', '.join(bits)}" if bits else ""))
                    if item.get("section_heading"):
                        lines.append(f"  - Description/section hint: {item.get('section_heading')}")
                    continue

                name = item.get("name") or item.get("title") or item.get("code") or "item"
                code = item.get("code")
                ects = item.get("ects")
                total_ects = item.get("total_ects")
                degree_level = item.get("degree_level")
                faculty_name = item.get("faculty_name")
                program_id = item.get("program_id")
                section_heading = item.get("section_heading") or item.get("program_course_description")

                extra = []
                if code:
                    extra.append(str(code))
                if program_id is not None:
                    extra.append(f"program_id {program_id}")
                if degree_level:
                    extra.append(str(degree_level))
                if ects is not None:
                    extra.append(f"{ects} ECTS")
                if total_ects is not None:
                    extra.append(f"{total_ects} total ECTS")
                if faculty_name:
                    extra.append(str(faculty_name))
                if section_heading:
                    extra.append(f"section: {section_heading}")

                suffix = f" ({', '.join(extra)})" if extra else ""
                lines.append(f"- {name}{suffix}")
            else:
                lines.append(f"- {item}")

        if len(result) > max_items:
            lines.append(f"... {len(result) - max_items} more result(s) not shown. Add filters to narrow the list.")
        return "\n".join(lines)

    return str(result)

def _select_rag_db(question: str, db_study=None, db_regl=None, db_base=None):
    q = question.lower()

    study_keywords = [
        "course", "courses", "module", "modules", "semester", "study plan", "program", "ects",
        "kurs", "kurse", "modul", "module", "semester", "studienplan", "bachelor", "master",
        "wirtschaftsinformatik", "business informatics", "pflichtfach", "wahlfach",
    ]

    regl_keywords = [
        "reglement", "regulation", "regulations", "ordnung", "article", "artikel", "paragraph", "§",
    ]

    base_keywords = [
        "datetime", "time", "teacher", "lecturer", "room",
        "metadata", "course content", "description", "day_time_info"
    ]

    if any(k in q for k in study_keywords):
        return db_study or db_base or db_regl
    if any(k in q for k in regl_keywords):
        return db_regl or db_study or db_base
    if any(k in q for k in base_keywords):
        return db_base or db_study or db_regl

    return db_base or db_study or db_regl

def _extract_semester_count(text: str) -> int | None:
    # Important: do NOT treat every standalone number as a duration. Replies
    # like "I completed ... Competences documentaires ... 1" previously became
    # "plan this in 1 semester". Only parse explicit duration phrases.
    match = re.search(
        r"\b(?:in|within|over|during|for)?\s*(\d{1,2})\s*(semester|semesters|semestri|semestren)\b",
        text.lower(),
    )
    if not match:
        return None

    value = int(match.group(1))

    if 1 <= value <= 12:
        return value

    return None


def _extract_program_id(text: str) -> int | None:
    match = re.search(r"\b(?:id\s*)?(\d+)\b", text.lower())
    if not match:
        return None
    return int(match.group(1))


def _extract_total_ects(text: str) -> int | None:
    match = re.search(r"\b(\d{2,3})\s*ects\b", text.lower())
    if not match:
        return None
    return int(match.group(1))


def _extract_semester_id(text: str) -> str | None:
    ids = _extract_semester_ids(text)
    return ids[0] if ids else None


def _extract_semester_ids(text: str) -> list[str]:
    matches = re.findall(r"\b(FS|HS|SS|AS)[-\s]?(\d{4})\b", text, re.IGNORECASE)
    result = []

    for prefix, year in matches:
        prefix = prefix.upper()
        if prefix == "SS":
            prefix = "FS"
        if prefix == "AS":
            prefix = "HS"

        sem = f"{prefix}-{year}"
        if sem not in result:
            result.append(sem)

    return result[:2]

def _extract_required_course_codes(question: str, courses: list[Dict[str, Any]]) -> list[str]:
    q = question.lower()
    if not any(x in q for x in ["has to be in", "must include", "include", "absolutely"]):
        return []
    return _courses_mentioned_by_user(question, courses)

def _extract_requested_extra_course_count(question: str) -> int | None:
    match = re.search(r"\b(\d{1,2})\s+other courses\b|\badd\s+(\d{1,2})\s+more courses\b", question.lower())
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _strip_semesters(text: str) -> str:
    text = re.sub(r"\b(FS|HS|SS|AS)[-\s]?\d{4}\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ,.-:")


def is_plan_study_program_hero(question: str) -> bool:
    q = question.lower().strip()
    return q in {
        "__hero__:plan_study_program",
        "__hero__:plan_whole_study_program",
        "__hero__:complete_study_plan",
    }


def is_plan_mobility_hero(question: str) -> bool:
    return question.strip().lower() == "__hero__:plan_mobility"


def start_plan_study_program_flow(session_state: Dict[str, Any]) -> Dict[str, Any]:
    session_state["hero_flow"] = {
        "name": "plan_study_program",
        "program_id": None,
        "program_name": None,
        "candidate_programs": [],
        "semesters": None,
        "total_ects": None,
    }

    return {
        "answer": (
            "Sure — which study program would you like to plan?\n\n"
            "Please tell me:\n"
            "1. the study program, for example **Business Informatics Bachelor**\n"
            "2. in how many semesters you would like to finish, for example **8 semesters**"
        ),
        "sources": [],
        "used_tools": [],
        "session_state": session_state,
        "plan": {"mode": "hero"},
        "planning_errors": None,
    }


def _normalize_course_code(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper().replace("UE-", ""))


def _extract_selected_elective_codes(question: str, elective_candidates: list[dict[str, Any]] | None = None) -> list[str]:
    """Extract selected elective course codes from a free-text reply.

    Prefer explicit UE-XXX codes. As a fallback, match candidate names that occur
    in the answer so users can reply with course names only.
    """
    text = question or ""
    found: list[str] = []

    for match in re.finditer(r"\b(?:UE[-\s]?)?([A-Z]{3})[.-]?(\d{5})\b", text, flags=re.IGNORECASE):
        code = f"UE-{match.group(1).upper()}.{match.group(2)}"
        if code not in found:
            found.append(code)

    lowered = text.lower()
    for course in elective_candidates or []:
        code = str(course.get("code") or "")
        name = str(course.get("course_name") or course.get("name") or "")
        if not code:
            continue
        norm_code = _normalize_course_code(code)
        if any(_normalize_course_code(existing) == norm_code for existing in found):
            continue
        if name and name.lower() in lowered:
            found.append(code)

    return found

def _all_plan_courses_from_result(result: Dict[str, Any]) -> list[Dict[str, Any]]:
    courses: list[Dict[str, Any]] = []
    for slot in result.get("suggested_mandatory_semester_plan") or result.get("semesters") or []:
        for group in (slot.get("mandatory") or []) + (slot.get("electives") or []):
            if isinstance(group, dict) and group.get("options"):
                courses.extend(group.get("options") or [])
            elif isinstance(group, dict):
                courses.append(group)
    courses.extend(result.get("elective_courses") or [])

    seen: set[str] = set()
    unique: list[Dict[str, Any]] = []
    for course in courses:
        code = str(course.get("code") or "")
        if code and code not in seen:
            seen.add(code)
            unique.append(course)
    return unique


def _extract_completed_course_codes(question: str, candidates: list[dict[str, Any]] | None = None) -> list[str]:
    return _extract_selected_elective_codes(question, candidates)



def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


DAY_RE = re.compile(
    r"(?P<day>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag)\s+"
    r"(?P<start>\d{1,2}:\d{2})\s*-\s*(?P<end>\d{1,2}:\d{2})",
    re.IGNORECASE,
)


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)

def _format_minutes(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"

def _course_title(course: Dict[str, Any]) -> str:
    return str(course.get("course_name") or course.get("name") or course.get("code") or "")


def _extract_time_blocks(course: Dict[str, Any]) -> list[dict[str, Any]]:
    """Extract comparable weekly time blocks from day_time_info.

    This keeps the current backend contract intact and fixes conflicts such as
    Tuesday 13:15-16:00 overlapping with Tuesday 15:15-18:00.
    """
    blocks: list[dict[str, Any]] = []
    text = str(course.get("day_time_info") or "")
    for match in DAY_RE.finditer(text):
        blocks.append({
            "course": _course_title(course),
            "code": course.get("code"),
            "day": match.group("day").lower(),
            "start": _minutes(match.group("start")),
            "end": _minutes(match.group("end")),
            "label": match.group(0),
        })
    return blocks


def _blocks_overlap(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (
        a["day"] == b["day"]
        and overlaps(a["start"], a["end"], b["start"], b["end"])
    )


def _group_conflicting_blocks(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []

    for i, first in enumerate(blocks):
        group = [first]

        for other in blocks[i + 1:]:
            if all(_blocks_overlap(other, existing) for existing in group):
                group.append(other)

        if len(group) > 1:
            groups.append(group)

    # Remove duplicate/smaller groups.
    unique: list[list[dict[str, Any]]] = []
    seen: set[tuple[str, ...]] = set()

    groups.sort(key=len, reverse=True)

    for group in groups:
        key = tuple(sorted(str(b.get("code")) + str(b.get("start")) + str(b.get("end")) for b in group))
        if key in seen:
            continue

        group_keys = set(key)
        is_subset = False

        for existing in unique:
            existing_key = set(
                str(b.get("code")) + str(b.get("start")) + str(b.get("end"))
                for b in existing
            )
            if group_keys.issubset(existing_key):
                is_subset = True
                break

        if not is_subset:
            seen.add(key)
            unique.append(group)

    return unique

def _detect_course_conflicts(courses: list[Dict[str, Any]]) -> list[str]:
    blocks: list[dict[str, Any]] = []

    for course in courses:
        blocks.extend(_extract_time_blocks(course))

    blocks.sort(key=lambda b: (b["day"], b["start"], b["end"], b["course"]))

    groups = _group_conflicting_blocks(blocks)

    conflicts: list[str] = []

    for group in groups:
        start = min(b["start"] for b in group)
        end = max(b["end"] for b in group)
        day = group[0]["day"].capitalize()

        label = f"{day} {_format_minutes(start)}-{_format_minutes(end)}"

        course_parts = []
        seen_codes = set()

        for b in group:
            if b["code"] in seen_codes:
                continue
            seen_codes.add(b["code"])

            block_start = _format_minutes(b["start"])
            block_end = _format_minutes(b["end"])
            course_parts.append(f"{b['course']} ({block_start}-{block_end})")

        conflicts.append(f"- {label}: " + "; ".join(course_parts))

    return conflicts


def _courses_have_conflict(courses: list[Dict[str, Any]]) -> bool:
    return bool(_detect_course_conflicts(courses))


def _normalize_for_matching(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _courses_mentioned_by_user(question: str, courses: list[Dict[str, Any]]) -> list[str]:
    text = _normalize_for_matching(question)
    found: list[str] = []
    for course in courses:
        code = str(course.get("code") or "")
        name = _course_title(course)
        candidates = [code, name]
        # Match multilingual names separated by slashes individually.
        candidates.extend(part.strip() for part in name.split("/") if part.strip())
        if any(_normalize_for_matching(candidate) in text for candidate in candidates if candidate):
            found.append(code)
    return found

def suggest_semester_courses(
    courses: list[Dict[str, Any]],
    target_ects: int = 30,
    required_courses: list[Dict[str, Any]] | None = None,
    extra_count: int | None = None,
) -> list[Dict[str, Any]]:
    required_courses = required_courses or []

    if _courses_have_conflict(required_courses):
        return required_courses

    required_codes = {c.get("code") for c in required_courses}
    courses = [c for c in courses if c.get("code") not in required_codes]

    zero_ects = [c for c in courses if float(c.get("ects") or 0) == 0]
    positive = [c for c in courses if float(c.get("ects") or 0) > 0]

    scale = 2
    max_ects = target_ects + 6
    max_units = int(max_ects * scale)

    required_units = int(
        round(sum(float(c.get("ects") or 0) for c in required_courses) * scale)
    )

    states: dict[int, list[Dict[str, Any]]] = {
        required_units: required_courses
    }

    def priority(course: Dict[str, Any]) -> tuple[int, float, str]:
        mandatory_rank = 0 if course.get("mandatory_for") else 1
        return (mandatory_rank, -float(course.get("ects") or 0), _course_title(course))

    for course in sorted(positive, key=priority):
        units = int(round(float(course.get("ects") or 0) * scale))
        snapshot = list(states.items())

        for total_units, selected in snapshot:
            new_total = total_units + units
            if new_total > max_units:
                continue

            candidate = selected + [course]
            if _courses_have_conflict(candidate):
                continue

            existing = states.get(new_total)
            if existing is None or _suggestion_score(
                candidate,
                target_ects,
                len(required_courses),
                extra_count,
            ) < _suggestion_score(
                existing,
                target_ects,
                len(required_courses),
                extra_count,
            ):
                states[new_total] = candidate

    best = min(
        states.values(),
        key=lambda plan: _suggestion_score(
            plan,
            target_ects,
            len(required_courses),
            extra_count,
        ),
    )

    for course in zero_ects:
        candidate = best + [course]
        if not _courses_have_conflict(candidate):
            best = candidate

    return best

def _suggestion_score(
    courses: list[Dict[str, Any]],
    target_ects: int,
    required_count: int = 0,
    extra_count: int | None = None,
) -> tuple[float, int, int, int]:
    total = sum(float(c.get("ects") or 0) for c in courses)
    mandatory_count = sum(1 for c in courses if c.get("mandatory_for"))
    positive_count = sum(1 for c in courses if float(c.get("ects") or 0) > 0)

    if extra_count is not None:
        desired_count = required_count + extra_count
        count_penalty = abs(positive_count - desired_count)
    else:
        count_penalty = 0

    return (
        count_penalty,
        abs(total - target_ects),
        -mandatory_count,
        positive_count,
    )

def _plan_score(courses: list[Dict[str, Any]], target_ects: int) -> tuple[float, int, int]:
    total = sum(float(c.get("ects") or 0) for c in courses)
    mandatory_count = sum(1 for c in courses if c.get("mandatory_for"))
    return (abs(total - target_ects), -mandatory_count, len(courses))


def format_semester_suggestion(
    courses: list[Dict[str, Any]],
    target_ects: int | None = None,
) -> str:
    if not courses:
        return "I could not find a conflict-free course combination for that ECTS target."

    total = sum(float(c.get("ects") or 0) for c in courses)

    lines = []
    if target_ects:
        lines.append(f"Here is a conflict-free suggestion close to **{target_ects} ECTS**:")
    else:
        lines.append("Here is a conflict-free suggestion:")

    lines.append(f"\nTotal: **{total:g} ECTS**\n")

    for c in courses:
        lines.append(f"- **{_course_title(c)}** ({c.get('code')}, {c.get('ects')} ECTS)")
        if c.get("day_time_info"):
            lines.append(f"  - Time: {c['day_time_info']}")

    conflicts = _detect_course_conflicts(courses)
    if conflicts:
        lines.append("\n⚠️ Conflicts:")
        lines.extend(conflicts)
    else:
        lines.append("\n✅ No timetable conflicts detected.")

    return "\n".join(lines)

def format_semester_plan(result: Dict[str, Any]) -> str:
    courses = result.get("courses", [])

    if not courses:
        return "I did not find course offerings for this program in the selected semester."

    lines = ["Here are the available courses for this semester:\n"]
    
    conflicts = _detect_course_conflicts(courses)

    for c in courses:
        mandatory = bool(c.get("mandatory_for"))
        tag = "mandatory" if mandatory else "elective"
        name = c.get("course_name") or c.get("code")

        lines.append(f"- **{name}** ({c.get('code')}, {c.get('ects')} ECTS, {tag})")

        if c.get("day_time_info"):
            lines.append(f"  - Time: {c['day_time_info']}")

    if conflicts:
        lines.append("\n⚠️ **Potential schedule conflicts detected:**")
        lines.extend(conflicts)

    lines.append(
        "\nPlease tell me which of these you have already completed, "
        "and which ones you are considering for this semester. "
        "I can then help you avoid timetable conflicts."
    )

    return "\n".join(lines)

def format_filtered_semester_plan(
    all_courses: list[Dict[str, Any]],
    completed_codes: list[str],
) -> str:
    completed = set(completed_codes)
    remaining = [c for c in all_courses if c.get("code") not in completed]

    lines = []
    if completed:
        completed_names = [_course_title(c) for c in all_courses if c.get("code") in completed]
        lines.append("Got it — I will treat these as already completed:")
        lines.extend(f"- {name}" for name in completed_names)
        lines.append("")

    lines.append("For this semester, the remaining relevant courses are:\n")
    for c in remaining:
        mandatory = bool(c.get("mandatory_for"))
        tag = "mandatory" if mandatory else "elective"
        lines.append(f"- **{_course_title(c)}** ({c.get('code')}, {c.get('ects')} ECTS, {tag})")
        if c.get("day_time_info"):
            lines.append(f"  - Time: {c['day_time_info']}")

    conflicts = _detect_course_conflicts(remaining)
    if conflicts:
        lines.append("\n⚠️ **Potential schedule conflicts among the remaining courses:**")
        lines.extend(conflicts)
    else:
        lines.append("\n✅ I do not see timetable conflicts among the remaining courses.")

    lines.append("\nWhich of these remaining courses do you want to take this semester?")
    return "\n".join(lines)


def _fmt_ects(value: Any) -> str:
    if value is None:
        return "unknown ECTS"
    try:
        num = float(value)
        return f"{num:.2f} ECTS" if num.is_integer() else f"{num:g} ECTS"
    except Exception:
        return f"{value} ECTS"


def _course_label_for_plan(course: Dict[str, Any]) -> str:
    name = course.get("course_name") or course.get("name") or course.get("code") or "Course"
    code = course.get("code")
    ects = course.get("ects")
    semester_type = course.get("semester_type") or course.get("offered_in")
    languages = course.get("teaching_languages") or []
    details = []
    if code:
        details.append(str(code))
    if ects is not None:
        details.append(_fmt_ects(ects))
    if semester_type:
        details.append(str(semester_type))
    if languages:
        details.append("/".join(str(x) for x in languages))
    return f"**{name}** ({', '.join(details)})" if details else f"**{name}**"


def format_study_program_plan(result: Dict[str, Any]) -> str:
    """Format whole-program proposal responses.

    Supports both the older backend shape and the newer
    /planner/study-program-plan-proposal shape.
    """
    program = result.get("program", {}) or {}
    program_name = program.get("display_name") or program.get("name") or result.get("program_name") or "the selected program"
    totals = result.get("totals") or {}
    requested_semesters = result.get("requested_semesters") or result.get("semesters_requested")
    selected_codes = result.get("selected_elective_codes") or []

    lines: list[str] = []
    if selected_codes:
        lines.append(f"Here is the completed study plan draft for **{program_name}**.")
    else:
        lines.append(f"Here is a complete study plan draft for **{program_name}**.")

    total_ects = totals.get("total_ects") or result.get("total_ects") or program.get("total_ects") or result.get("required_total_ects")
    mandatory_ects = totals.get("mandatory_ects_after_language_choices") or result.get("mandatory_ects")
    required_elective_ects = totals.get("elective_ects_required") or result.get("required_elective_ects")

    if total_ects:
        intro = f"\nThe program requires **{_fmt_ects(total_ects)}** in total"
        if mandatory_ects is not None and required_elective_ects is not None:
            intro += f", including approximately **{_fmt_ects(mandatory_ects)}** from mandatory courses and **{_fmt_ects(required_elective_ects)}** from elective courses"
        intro += "."
        lines.append(intro)

    if requested_semesters:
        target = totals.get("target_ects_per_semester")
        if target is not None:
            lines.append(f"The target load is about **{_fmt_ects(target)} per semester** over **{requested_semesters} semesters**.")

    lines.append("\nBecause future course offerings are not fully known yet, this plan reuses the next available offering pattern for courses. Exact dates and times may change.")

    assumptions = result.get("assumptions") or []
    if assumptions:
        lines.append("\n## Planning assumptions")
        for assumption in assumptions:
            lines.append(f"- {assumption}")

#    choice_groups = [g for g in (result.get("mandatory_choice_groups") or []) if g.get("requires_choice")]
#    if choice_groups:
#        lines.append("\n## Mandatory language/equivalent choices")
#        for group in choice_groups[:12]:
#            options = group.get("options") or []
#            lines.append("- Choose one of: " + "; ".join(_course_label_for_plan(o) for o in options))

    plan_slots = result.get("suggested_mandatory_semester_plan") or result.get("semesters") or []
    if plan_slots:
        lines.append("\n## Suggested semester distribution")
        for slot in plan_slots:
            number = slot.get("semester_number") or slot.get("index") or "?"
            sem_type = slot.get("semester_type") or slot.get("type") or "semester"
            planned = slot.get("planned_ects")
            header = f"\n### Semester {number} ({sem_type})"
            if planned is not None:
                header += f" — {_fmt_ects(planned)}"
            lines.append(header)

            mandatory = slot.get("mandatory") or slot.get("courses") or []
            electives_in_slot = slot.get("electives") or []
            if not mandatory and not electives_in_slot:
                lines.append("- No courses assigned yet.")
                continue
            for group in mandatory:
                options = group.get("options") if isinstance(group, dict) else None
                if options:
                    if group.get("requires_choice"):
                        lines.append("- Mandatory: choose one language/equivalent option:")
                        for option in options:
                            lines.append(f"  - {_course_label_for_plan(option)}")
                    else:
                        lines.append(f"- Mandatory: {_course_label_for_plan(options[0])}")
                elif isinstance(group, dict):
                    lines.append(f"- Mandatory: {_course_label_for_plan(group)}")
                else:
                    lines.append(f"- Mandatory: {group}")
            for group in electives_in_slot:
                options = group.get("options") if isinstance(group, dict) else None
                if options:
                    if group.get("requires_choice"):
                        lines.append("- Elective: choose one option:")
                        for option in options:
                            lines.append(f"  - {_course_label_for_plan(option)}")
                    else:
                        lines.append(f"- Elective: {_course_label_for_plan(options[0])}")
                elif isinstance(group, dict):
                    lines.append(f"- Elective: {_course_label_for_plan(group)}")

    elective_courses = result.get("elective_courses") or []
    if elective_courses and not selected_codes:
        lines.append("\n## Elective courses")
        lines.append("Elective courses should be selected by the student. The following courses are available candidates:")
        for course in elective_courses:
            lines.append(f"- {_course_label_for_plan(course)}")
        lines.append("\nTo finish the plan, please choose which electives you want to take. You can answer with course codes like `UE-SIN.01022` or with the course names.")
    elif elective_courses and selected_codes:
        lines.append("\nIf you want to change electives, tell me the new elective course codes and I can regenerate the proposal.")
    return "\n".join(lines)


def format_mobility_plan(result: Dict[str, Any]) -> str:
    lines = [f"Here are mobility-enabled course candidates for **{result['interest']}**."]

    for sem in result["semesters"]:
        lines.append(f"\n## {sem}")
        courses = result["courses_by_semester"].get(sem, [])

        if not courses:
            lines.append("- No matching mobility courses found.")
            continue

        for c in courses[:12]:
            details = [c.get("code")]
            if c.get("ects") is not None:
                details.append(f"{c['ects']} ECTS")
            if c.get("domain_name"):
                details.append(c["domain_name"])

            lines.append(
                f"- **{c.get('name') or c.get('code')}** "
                f"({', '.join(x for x in details if x)})"
            )

            if c.get("description"):
                lines.append(f"  - Content: {str(c['description'])[:180]}...")

    return "\n".join(lines)

def _dedupe_documents(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()

    for doc in docs or []:
        if not isinstance(doc, dict):
            continue

        source_url = doc.get("source_url") or doc.get("url")
        doc_key = doc.get("doc_key")
        dedupe_key = doc_key or source_url

        if not dedupe_key or dedupe_key in seen:
            continue

        seen.add(dedupe_key)

        result.append({
            "title": doc.get("title") or doc.get("label") or "Source document",
            "doc_key": doc_key,
            "doc_type": doc.get("doc_type"),
            "source_url": source_url,
            "download_url": doc.get("download_url") or source_url,
            "page": doc.get("page"),
        })

    return result


def answer_question(
    question: str,
    db_study=None,
    db_regl=None,
    db_base=None,
    language: str | None = None,
    session_state: Dict[str, Any] | None = None,
    run_mode: str | None = None,
) -> Dict[str, Any]:
    session_state = session_state or {}
    final_answer = ""

    if is_plan_semester_hero(question):
        return start_plan_semester_flow(session_state)

    if is_plan_study_program_hero(question):
        return start_plan_study_program_flow(session_state)

    flow = session_state.get("hero_flow")

    if flow and _looks_like_new_general_question(question):
        session_state["hero_flow"] = None
        flow = None

    if is_plan_mobility_hero(question):
        session_state["hero_flow"] = {
            "name": "plan_mobility",
            "semesters": [],
            "interest": None,
        }

        return {
            "answer": (
                "Sure — which exchange semester(s) are you here for, "
                "and what course direction are you interested in?"
            ),
            "sources": [],
            "used_tools": [],
            "session_state": session_state,
            "plan": {"mode": "hero", "hero": "plan_mobility"},
            "planning_errors": None,
        }

    # ---------------------------------------------------------------------
    # Whole study-program planner flow
    # ---------------------------------------------------------------------
    if flow and flow.get("name") == "plan_study_program":
        selected_program_id = flow.get("program_id")
        semesters = flow.get("semesters")
        total_ects = flow.get("total_ects")

        candidate_programs = flow.get("candidate_programs", [])

        # Important: when we are waiting for a program-id selection, a reply like
        # "11" is the program id, not a request to plan 11 semesters.
        # Therefore only parse semester counts after the id-selection branch, or
        # when the user explicitly says "8 semesters" in the same message.
        found_semesters = None
        if not (candidate_programs and not selected_program_id) or re.search(
            r"\b\d{1,2}\s*(semester|semesters|semestri|semestren)\b",
            question,
            flags=re.IGNORECASE,
        ):
            found_semesters = _extract_semester_count(question)

        found_total_ects = _extract_total_ects(question)

        if found_semesters:
            semesters = found_semesters
            session_state["hero_flow"]["semesters"] = semesters

        if found_total_ects:
            total_ects = found_total_ects
            session_state["hero_flow"]["total_ects"] = total_ects

        if candidate_programs and not selected_program_id:
            chosen_id = _extract_program_id(question)

            if chosen_id:
                matching = [
                    p for p in candidate_programs
                    if int(p.get("program_id")) == chosen_id
                ]

                if matching:
                    selected_program_id = chosen_id
                    session_state["hero_flow"]["program_id"] = selected_program_id
                    session_state["hero_flow"]["program_name"] = (
                        matching[0].get("display_name")
                        or matching[0].get("name")
                    )
                else:
                    return {
                        "answer": "I could not find that ID in the options I showed. Please choose one of the listed program IDs.",
                        "sources": [],
                        "used_tools": [],
                        "session_state": session_state,
                        "plan": {"mode": "hero"},
                        "planning_errors": None,
                    }

        if not selected_program_id:
            cleaned_question = question

            if found_semesters:
                cleaned_question = re.sub(
                    r"\b\d{1,2}\s*(semester|semesters|semestri|semestren)?\b",
                    "",
                    cleaned_question,
                    flags=re.IGNORECASE,
                )

            if found_total_ects:
                cleaned_question = re.sub(
                    r"\b\d{2,3}\s*ects\b",
                    "",
                    cleaned_question,
                    flags=re.IGNORECASE,
                )

            # Remove degree/semester words before program lookup so a phrase like
            # "Bachelor in Business Informatics in 8 semesters" becomes the actual
            # searchable program name instead of an over-specific literal query.
            cleaned_question = re.sub(
                r"\b(bachelor|master|doctorate|phd|in|for|over|within|complete|study|plan|program|programme)\b",
                " ",
                cleaned_question,
                flags=re.IGNORECASE,
            )
            cleaned_question = re.sub(r"\s+", " ", cleaned_question).strip(" ,.-")

            if not cleaned_question:
                return {
                    "answer": (
                        "Which study program should I plan? "
                        "For example: **Business Informatics Bachelor**."
                    ),
                    "sources": [],
                    "used_tools": [],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            with timed_step("tool.get_planner_programs", flow="study_program"):
                programs = TOOLS["get_planner_programs"](
                    q=cleaned_question,
                    degree_level="Bachelor" if "bachelor" in question.lower() else None,
                    locale=language or "en",
                )

            if not programs:
                return {
                    "answer": "I could not find a matching study program. Please write the official program name.",
                    "sources": [],
                    "used_tools": ["get_planner_programs"],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            bachelor_programs = [
                p for p in programs
                if str(p.get("degree_level", "")).lower() == "bachelor"
            ]

            if "bachelor" in question.lower() and bachelor_programs:
                programs = bachelor_programs

            if len(programs) > 1:
                session_state["hero_flow"]["candidate_programs"] = programs[:8]

                options = "\n".join(
                    (
                        f"- {p['program_id']}: {p.get('display_name')} "
                        f"({p.get('degree_level')}"
                        f"{', ' + str(p.get('total_ects')) + ' ECTS' if p.get('total_ects') else ''})"
                    )
                    for p in programs[:8]
                )

                return {
                    "answer": f"I found several matching programs. Which one should I use?\n\n{options}\n\nPlease answer with the program ID.",
                    "sources": [],
                    "used_tools": ["get_planner_programs"],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            selected_program_id = programs[0]["program_id"]
            session_state["hero_flow"]["program_id"] = selected_program_id
            session_state["hero_flow"]["program_name"] = (
                programs[0].get("display_name")
                or programs[0].get("name")
            )

        if not semesters:
            return {
                "answer": (
                    "In how many semesters would you like to finish? "
                    "For example: **6 semesters** or **8 semesters**."
                ),
                "sources": [],
                "used_tools": [],
                "session_state": session_state,
                "plan": {"mode": "hero"},
                "planning_errors": None,
            }

        planner_args = {
            "program_id": selected_program_id,
            "semesters": semesters,
            "locale": language or "en",
        }

        if total_ects:
            planner_args["total_ects"] = total_ects

        awaiting_electives = bool(flow.get("awaiting_electives"))
        completed_codes = flow.get("completed_codes") or []

        # Follow-up: user asks to rebuild the same study plan without courses
        # they already completed. Keep program_id, semesters and selected
        # electives from the previous state.
        if re.search(r"\b(already completed|completed|done|passed|without those|ohne diese|déjà validé)\b", question, flags=re.IGNORECASE):
            newly_completed = _extract_completed_course_codes(
                question,
                flow.get("course_candidates") or flow.get("elective_candidates") or [],
            )
            completed_codes = list(dict.fromkeys([*completed_codes, *newly_completed]))
            session_state["hero_flow"]["completed_codes"] = completed_codes

        if completed_codes:
            planner_args["completed_course_codes"] = completed_codes

        if awaiting_electives:
            selected_codes = _extract_selected_elective_codes(
                question,
                flow.get("elective_candidates") or [],
            )
            if not selected_codes:
                return {
                    "answer": (
                        "I am still waiting for your elective choices. "
                        "Please answer with elective course codes such as `UE-SIN.01022`, "
                        "or copy the elective course names from the list."
                    ),
                    "sources": [],
                    "used_tools": [],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }
            previous_selected = flow.get("selected_elective_codes") or []
            planner_args["selected_elective_codes"] = list(dict.fromkeys([*previous_selected, *selected_codes]))
        elif flow.get("selected_elective_codes"):
            planner_args["selected_elective_codes"] = flow.get("selected_elective_codes")

        with timed_step("tool.get_study_program_plan"):
            planner_result = TOOLS["get_study_program_plan"](**planner_args)

        print("DEBUG study_program_plan result:", planner_result)

        with timed_step("answer.format_study_program_plan"):
            answer = format_study_program_plan(planner_result)

        if awaiting_electives or planner_result.get("selected_elective_codes"):
            session_state["hero_flow"] = {
                "name": "plan_study_program",
                "program_id": selected_program_id,
                "program_name": flow.get("program_name") or (planner_result.get("program") or {}).get("display_name"),
                "candidate_programs": [],
                "semesters": semesters,
                "total_ects": total_ects,
                "awaiting_electives": False,
                "selected_elective_codes": planner_result.get("selected_elective_codes") or planner_args.get("selected_elective_codes") or [],
                "completed_codes": completed_codes,
                "course_candidates": _all_plan_courses_from_result(planner_result),
                "elective_candidates": planner_result.get("elective_courses") or [],
            }
        elif planner_result.get("elective_courses"):
            session_state["hero_flow"] = {
                "name": "plan_study_program",
                "program_id": selected_program_id,
                "program_name": flow.get("program_name") or (planner_result.get("program") or {}).get("display_name"),
                "candidate_programs": [],
                "semesters": semesters,
                "total_ects": total_ects,
                "awaiting_electives": True,
                "selected_elective_codes": [],
                "completed_codes": completed_codes,
                "course_candidates": _all_plan_courses_from_result(planner_result),
                "elective_candidates": planner_result.get("elective_courses") or [],
            }
        else:
            session_state["hero_flow"] = None

        return {
            "answer": answer,
            "sources": [],
            "used_tools": ["get_study_program_plan"],
            "session_state": session_state,
            "plan": {"mode": "hero"},
            "planning_errors": None,
        }

    # ---------------------------------------------------------------------
    # Existing semester planner flow
    # ---------------------------------------------------------------------
    if flow and flow.get("name") == "plan_semester":
        sem_id = flow.get("sem_id")
        selected_program_id = flow.get("program_id")

        if flow.get("step") == "awaiting_completed_courses":
            courses = flow.get("courses") or []
            completed_codes = _courses_mentioned_by_user(question, courses)
            if not completed_codes:
                return {
                    "answer": "I could not match those to the course list. Please copy the completed course names or course codes from the list.",
                    "sources": [],
                    "used_tools": [],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }
            session_state["hero_flow"]["completed_codes"] = completed_codes
            session_state["hero_flow"]["step"] = "awaiting_courses_to_take"
            return {
                "answer": format_filtered_semester_plan(courses, completed_codes),
                "sources": [],
                "used_tools": [],
                "session_state": session_state,
                "plan": {"mode": "hero"},
                "planning_errors": None,
            }
        
        if flow.get("step") == "awaiting_courses_to_take":
            courses = flow.get("courses") or []
            completed_codes = set(flow.get("completed_codes") or [])
            remaining = [c for c in courses if c.get("code") not in completed_codes]

            target_ects = None
            match = re.search(
                r"\baround\s+(\d{1,2})\s*ects\b|\b(\d{1,2})\s*ects\b",
                question.lower(),
            )
            if match:
                target_ects = int(match.group(1) or match.group(2))

            required_codes = _extract_required_course_codes(question, remaining)
            required_courses = [c for c in remaining if c.get("code") in required_codes]

            extra_count = _extract_requested_extra_course_count(question)

            wants_suggestion = (
                "suggest" in question.lower()
                or "recommend" in question.lower()
                or "of your choice" in question.lower()
                or target_ects is not None
                or bool(required_courses)
                or extra_count is not None
            )

            if wants_suggestion:
                suggestion = suggest_semester_courses(
                    remaining,
                    target_ects or 30,
                    required_courses=required_courses,
                    extra_count=extra_count,
                )

                session_state["hero_flow"]["suggested_codes"] = [
                    c.get("code") for c in suggestion
                ]

                return {
                    "answer": format_semester_suggestion(suggestion, target_ects or 30),
                    "sources": [],
                    "used_tools": [],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            selected_codes = _courses_mentioned_by_user(question, remaining)
            if selected_codes:
                selected = [c for c in remaining if c.get("code") in selected_codes]
                return {
                    "answer": format_semester_suggestion(selected, target_ects or None),
                    "sources": [],
                    "used_tools": [],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

        if not sem_id:
            sem_id = _extract_semester_id(question)

            if sem_id:
                session_state["hero_flow"]["sem_id"] = sem_id
            else:
                return {
                    "answer": "Which semester would you like to plan? Please use a format like **FS-2026** or **HS-2026**.",
                    "sources": [],
                    "used_tools": [],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

        candidate_programs = flow.get("candidate_programs", [])

        if candidate_programs and not selected_program_id:
            chosen_id = _extract_program_id(question)

            if chosen_id:
                matching = [
                    p for p in candidate_programs
                    if int(p.get("program_id")) == chosen_id
                ]

                if matching:
                    selected_program_id = chosen_id
                    session_state["hero_flow"]["program_id"] = selected_program_id
                else:
                    return {
                        "answer": "I could not find that ID in the options I showed. Please choose one of the listed program IDs.",
                        "sources": [],
                        "used_tools": [],
                        "session_state": session_state,
                        "plan": {"mode": "hero"},
                        "planning_errors": None,
                    }

        if not selected_program_id:
            cleaned_question = question.replace(sem_id, "").strip(" ,.-")

            if not cleaned_question:
                return {
                    "answer": (
                        f"Great, I will plan **{sem_id}**.\n\n"
                        "Which study program or direction are you studying? "
                        "For example: **Wirtschaftsinformatik Bachelor**."
                    ),
                    "sources": [],
                    "used_tools": [],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            with timed_step("tool.get_planner_programs", flow="study_program"):
                programs = TOOLS["get_planner_programs"](
                    q=cleaned_question,
                    degree_level="Bachelor" if "bachelor" in question.lower() else None,
                    locale=language or "en",
                )

            if not programs:
                return {
                    "answer": "I could not find a matching study program. Could you write the official program name?",
                    "sources": [],
                    "used_tools": ["get_planner_programs"],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            bachelor_programs = [
                p for p in programs
                if str(p.get("degree_level", "")).lower() == "bachelor"
            ]

            if "bachelor" in question.lower() and bachelor_programs:
                programs = bachelor_programs

            if len(programs) > 1:
                session_state["hero_flow"]["candidate_programs"] = programs[:8]

                options = "\n".join(
                    (
                        f"- {p['program_id']}: {p.get('display_name')} "
                        f"({p.get('degree_level')}"
                        f"{', ' + str(p.get('total_ects')) + ' ECTS' if p.get('total_ects') else ''})"
                    )
                    for p in programs[:8]
                )

                return {
                    "answer": f"I found several matching programs. Which one should I use?\n\n{options}\n\nPlease answer with the program ID.",
                    "sources": [],
                    "used_tools": ["get_planner_programs"],
                    "session_state": session_state,
                    "plan": {"mode": "hero"},
                    "planning_errors": None,
                }

            selected_program_id = programs[0]["program_id"]
            session_state["hero_flow"]["program_id"] = selected_program_id

        planner_result = TOOLS["get_planner_courses"](
            sem_id=sem_id,
            program_ids=[selected_program_id],
            locale=language or "en",
        )

        answer = format_semester_plan(planner_result)

        session_state["hero_flow"] = {
            "name": "plan_semester",
            "step": "awaiting_completed_courses",
            "sem_id": sem_id,
            "program_id": selected_program_id,
            "courses": planner_result.get("courses", []),
        }

        return {
            "answer": answer,
            "sources": [],
            "used_tools": ["get_planner_courses"],
            "session_state": session_state,
            "plan": {"mode": "hero"},
            "planning_errors": None,
        }

    if flow and flow.get("name") == "plan_mobility":
        semesters = _extract_semester_ids(question)
        interest = _strip_semesters(question)

        if not semesters:
            return {
                "answer": "Which semester(s)? Please use a format like FS-2026 or HS-2026.",
                "sources": [],
                "used_tools": [],
                "session_state": session_state,
                "plan": {"mode": "hero", "hero": "plan_mobility"},
                "planning_errors": None,
            }

        if not interest:
            return {
                "answer": "What course direction are you interested in?",
                "sources": [],
                "used_tools": [],
                "session_state": session_state,
                "plan": {"mode": "hero", "hero": "plan_mobility"},
                "planning_errors": None,
            }

        result = TOOLS["get_mobility_courses"](
            semesters=semesters,
            interest=interest,
            language=language or None,
        )

        session_state["hero_flow"] = None

        return {
            "answer": format_mobility_plan(result),
            "sources": [],
            "used_tools": ["get_mobility_courses"],
            "session_state": session_state,
            "plan": {"mode": "hero", "hero": "plan_mobility"},
            "planning_errors": None,
        }

    # ---------------------------------------------------------------------
    # Normal tool/RAG behavior
    # ---------------------------------------------------------------------
    if run_mode and run_mode != "auto":
        if run_mode == "tool":
            try:
                with timed_step("planner.total"):
                    plan = plan_tool_usage(question, session_state=session_state)
                plan["mode"] = "tool"
                plan["reason"] = "Forced mode: tool, planner used for tool calls"
                planning_errors = None
            except Exception as e:
                plan = {"mode": "tool", "tool_calls": [], "reason": "Forced tool mode, planner failed"}
                planning_errors = str(e)
        else:
            plan = {
                "mode": run_mode,
                "tool_calls": [],
                "reason": f"Forced mode: {run_mode}",
            }
            planning_errors = None
    else:
        try:
            with timed_step("planner.total"):
                plan = plan_tool_usage(question, session_state=session_state)
            planning_errors = None
        except Exception as e:
            plan = {"mode": "rag", "tool_calls": [], "reason": "Planner fallback"}
            planning_errors = str(e)

    mode = plan.get("mode", "rag")
    tool_results: List[Dict[str, Any]] = []
    sources = []
    answer_parts: List[str] = []
    documents = []
    debug_tool_calls = []

    if mode in ("tool", "hybrid"):
        for call in plan.get("tool_calls", []):
            tool_name = call.get("tool")
            raw_args = call.get("args", {}) or {}
            args = dict(raw_args)

            allowed_args_by_tool = {
                "get_courses": {
                    "ects", "faculty_id", "faculty_name", "domain_id", "domain_name",
                    "language", "semester", "name_contains", "mobility", "soft_skills",
                    "program_id", "program_name", "limit", "locale",
                },
                "get_programs": {
                    "name", "degree_level", "faculty_id", "faculty_name",
                    "study_start", "total_ects", "program_type",
                },
                "get_program_courses": {
                    "program_id", "ects", "faculty_id", "faculty_name", "domain_id", "domain_name",
                    "language", "semester", "name_contains", "mobility", "soft_skills",
                    "course_type", "limit",
                },
                "get_program_courses_by_metadata": {
                    "program_en", "program_de", "program_fr", "degree_level", "faculty_id",
                    "faculty_name", "study_start", "total_ects", "course_type", "semester_type",
                    "ects", "domain_id", "domain_name", "language", "semester", "name_contains",
                    "mobility", "soft_skills", "section_contains", "limit",
                },
                "get_program_course_sections": {
                    "program_id", "course_code", "section_heading", "non_empty_only", "limit",
                },
                "get_planner_programs": {"q", "degree_level", "locale", "limit"},
                "get_planner_courses": {"sem_id", "program_ids", "locale"},
                "get_study_program_plan": {"program_id", "semesters", "locale", "total_ects", "selected_elective_codes", "completed_course_codes"},
                "get_mobility_courses": {"semesters", "interest", "language"},
            }

            allowed = allowed_args_by_tool.get(tool_name)
            if allowed is not None:
                args = {k: v for k, v in args.items() if k in allowed}

            tool_fn = TOOLS.get(tool_name)
            if not tool_fn:
                tool_results.append({
                    "tool": tool_name,
                    "result": None,
                    "error": f"Unknown tool: {tool_name}",
                })
                continue

            debug_entry = {
                "tool": tool_name,
                "raw_args": raw_args,
                "filtered_args": args,
                "result_type": None,
                "result_count": None,
                "sample_codes": [],
                "error": None,
            }

            if tool_name == "get_courses" and not args and any(
                x.get("tool") == "get_program_courses_by_metadata"
                for x in tool_results
            ):
                debug_entry["error"] = "Skipped broad get_courses after program-specific tool call"
                debug_tool_calls.append(debug_entry)
                logger.warning(
                    "Skipped broad get_courses after program-specific tool call: raw_args=%s filtered_args=%s",
                    raw_args,
                    args,
                )
                continue

            try:
                with timed_step(f"tool.{tool_name}"):
                    result = tool_fn(**args)

                tool_results.append({
                    "tool": tool_name,
                    "result": result,
                })

                # Keep raw results for the final synthesis step below. Do not
                # immediately format rows as the visible answer, otherwise a
                # question like "how many ECTS does X have?" becomes a giant list.
                debug_entry["result_type"] = type(result).__name__

                if isinstance(result, list):
                    debug_entry["result_count"] = len(result)
                    debug_entry["sample_codes"] = [
                        x.get("code")
                        for x in result[:5]
                        if isinstance(x, dict) and x.get("code")
                    ]
                elif isinstance(result, dict):
                    debug_entry["result_count"] = len(result)

                debug_tool_calls.append(debug_entry)

                logger.info(
                    "Tool call: tool=%s raw_args=%s filtered_args=%s result_type=%s result_count=%s sample_codes=%s",
                    tool_name,
                    raw_args,
                    args,
                    debug_entry["result_type"],
                    debug_entry["result_count"],
                    debug_entry["sample_codes"],
                )

            except Exception as e:
                tool_results.append({
                    "tool": tool_name,
                    "result": None,
                    "error": str(e),
                })
                # Tool errors are useful to keep visible, but empty results are not.
                answer_parts.append(f"{tool_name} failed: {e}")

                debug_entry["error"] = str(e)
                debug_tool_calls.append(debug_entry)
                logger.exception("Tool call failed: tool=%s raw_args=%s filtered_args=%s", tool_name, raw_args, args)

    tool_mode_found_anything = any(
        _has_tool_result(x.get("result"))
        for x in tool_results
        if not x.get("error")
    )

    should_run_rag = mode in ("rag", "hybrid") or (mode == "tool" and not tool_mode_found_anything)

    if should_run_rag:
        # Do not hard-route RAG to a single vectorstore.  Pass all loaded stores
        # to the RAG layer; it will retrieve with intent-weighted quotas
        # (for example 10/5/3) and rerank the merged candidates.
        db = {
            name: store
            for name, store in {
                "studyplans": db_study,
                "reglementations": db_regl,
                "base_data": db_base,
            }.items()
            if store is not None
        }

        if db:
            with timed_step("rag.total"):
                rag_text, rag_sources, rag_documents = rag_answer(
                    db=db,
                    question=question,
                    language=language,
                )
            sources.extend(rag_sources)
            documents.extend(rag_documents)

            if mode == "rag" or (mode == "tool" and not tool_mode_found_anything):
                final_answer = rag_text
            else:
                answer_parts.append("Document answer:\n" + rag_text)
        else:
            if mode == "rag" or (mode == "tool" and not tool_mode_found_anything):
                final_answer = "The document index is not loaded."
            else:
                answer_parts.append("The document index is not loaded.")

    if mode == "tool" and not final_answer:
        if tool_mode_found_anything:
            try:
                final_answer = _synthesize_tool_answer(question, plan, tool_results)
            except Exception as e:
                logger.exception("Tool answer synthesis failed")
                final_answer = "\n".join(answer_parts) if answer_parts else f"I found matching data, but could not synthesize the answer: {e}"
        else:
            final_answer = "No matching result found."
    elif mode == "hybrid":
        final_answer = "\n\n".join(part for part in answer_parts if part) or "No answer available."
    elif mode == "rag" and not final_answer:
        final_answer = "No answer available."

    with timed_step("session.update_state"):
        new_session_state = update_session_state(session_state, tool_results)

    return {
        "answer": final_answer,
        "sources": sources,
        "documents": _dedupe_documents(documents),
        "used_tools": [x["tool"] for x in tool_results if x.get("tool")],
        "session_state": new_session_state,
        "plan": plan,
        "planning_errors": planning_errors,

        "debug": {
            "mode": mode,
            "planned_tool_calls": plan.get("tool_calls", []),
            "executed_tool_calls": debug_tool_calls,
            "tool_mode_found_anything": tool_mode_found_anything,
        },
    }
